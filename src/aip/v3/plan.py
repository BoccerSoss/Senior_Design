"""User-authorized ChatGPT plan sessions following OpenAI's public local-client flow."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, parse, request

from aip.v2.providers import ProviderError, _open_request
from . import plan_inference

try:
    import jwt
    import pywintypes
    import win32crypt
    from jwt.algorithms import has_crypto
    _storage_errors = (sqlite3.Error, OSError, ValueError, pywintypes.error)
except ImportError:
    jwt = win32crypt = None
    has_crypto = False
    _storage_errors = (sqlite3.Error, OSError, ValueError)


ISSUER = "https://auth.openai.com"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
INITIAL_CLIENT = "dynamic_agent_client"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPES = {"resource.invoke", "chatgpt.tokens.use.direct"}
ATTEMPT_SECONDS = 300
REVOKED = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
           "refresh_token_invalidated", "refresh_token_reused"}
SETUP_ERROR = "Open AIP V3.cmd on Windows to install ChatGPT sign-in support."
NO_PLAN = "This account has not granted ChatGPT plan usage. Continue with ChatGPT to review access, or select ChatGPT in your browser."
RECONNECT = "OpenAI did not accept this ChatGPT session. Choose Continue with ChatGPT to sign in again."


class AuthError(ProviderError):
    def __init__(self, message, code="", retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _auth_request(url, form=None, *, empty=False):
    """Fixed, caller-owned OpenAI URLs; never emit server text or credentials."""
    encoded = parse.urlencode(form).encode("ascii") if form is not None else None
    req = request.Request(url, data=encoded, headers={"Accept": "application/json"})
    try:
        with _open_request(req) as response:
            raw = response.read(256_001)
    except error.HTTPError as exc:
        with exc:
            try:
                detail = json.loads(exc.read(16_384))
            except (ValueError, UnicodeError):
                detail = {}
        code = detail.get("error") if isinstance(detail, dict) else ""
        raise AuthError(f"OpenAI declined the sign-in request (HTTP {exc.code}). Check your ChatGPT account and plan permissions.",
                        code if isinstance(code, str) else "", exc.code >= 500) from None
    except (OSError, HTTPException):
        raise AuthError("Could not reach OpenAI for sign-in. Check your connection and try again.", retryable=True) from None
    if empty and not raw:
        return {}
    try:
        result = json.loads(raw) if len(raw) <= 256_000 else None
    except (ValueError, UnicodeError):
        result = None
    if not isinstance(result, dict):
        raise AuthError("OpenAI returned an unreadable sign-in response.")
    return result


def _identity(id_token, client_id, nonce=None):
    if not isinstance(id_token, str) or len(id_token) > 65_536:
        raise AuthError("OpenAI did not return a verifiable identity.")
    discovery = _auth_request(ISSUER + "/.well-known/openid-configuration")
    if discovery.get("issuer") != ISSUER or discovery.get("jwks_uri") != ISSUER + "/.well-known/jwks.json":
        raise AuthError("The OpenAI identity discovery document could not be verified.")
    keys = _auth_request(discovery["jwks_uri"])
    try:
        header = jwt.get_unverified_header(id_token)
        if header["alg"] != "RS256" or not isinstance(header["kid"], str):
            raise ValueError
        public_key = jwt.PyJWKSet.from_dict(keys)[header["kid"]].key
        required = ["iss", "aud", "exp", "sub"] + (["nonce"] if nonce else [])
        claims = jwt.decode(id_token, public_key, algorithms=["RS256"], issuer=ISSUER, audience=client_id,
                            options={"require": required})
        if nonce and not hmac.compare_digest(claims["nonce"], nonce):
            raise ValueError
        audiences = claims["aud"]
        if ("azp" in claims or isinstance(audiences, list) and len(audiences) > 1) and claims.get("azp") != client_id:
            raise ValueError
        if not isinstance(claims["sub"], str) or not claims["sub"]:
            raise ValueError
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise AuthError("OpenAI sign-in identity verification failed. Start a fresh sign-in.") from None
    return claims


def _new_state():
    return {"host": "urn:uuid:" + str(uuid.uuid4()), "accounts": {}, "active": None}


class _Vault:
    """A single DPAPI-protected record. SQLite serializes rotating grants across workbenches."""
    def __init__(self, path):
        self.path = path

    @contextmanager
    def state(self, *, update=False):
        if not update and not self.path.exists():
            yield {"accounts": {}, "active": None}
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=60)) as db, db:
                if update:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("CREATE TABLE IF NOT EXISTS plan (id INTEGER PRIMARY KEY, content BLOB NOT NULL)")
                row = db.execute("SELECT content FROM plan WHERE id=1").fetchone()
                if row:
                    clear = win32crypt.CryptUnprotectData(row[0], None, None, None, 1)[1]
                    state = json.loads(clear)
                else:
                    state = _new_state()
                yield state
                if update:
                    protected = win32crypt.CryptProtectData(json.dumps(state).encode(), "AIP ChatGPT plan",
                                                            None, None, None, 1)
                    db.execute("INSERT OR REPLACE INTO plan VALUES(1, ?)", (protected,))
        except _storage_errors as exc:
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError("Windows could not read or save this account's protected ChatGPT sign-in.") from None


@dataclass
class _Attempt:
    account: str | None
    client: str
    state: str
    nonce: str
    verifier: str
    redirect: str
    server: ThreadingHTTPServer
    timer: threading.Timer | None = None
    claimed: bool = False


def _new_grant(data, previous=None):
    if not isinstance(data.get("access_token"), str) or not data["access_token"]:
        raise AuthError("OpenAI returned an incomplete access token.")
    if str(data.get("token_type")).lower() != "bearer":
        raise AuthError("OpenAI returned an unsupported token type.")
    duration = data.get("expires_in")
    if type(duration) is not int or duration <= 0:
        raise AuthError("OpenAI returned an invalid token lifetime.")
    scope = data.get("scope")
    if not isinstance(scope, str) and previous is None:
        raise AuthError("OpenAI did not confirm the permissions granted to the workbench.")
    if scope is not None and not isinstance(scope, str):
        raise AuthError("OpenAI returned invalid permissions.")
    scopes = scope.split() if scope is not None else previous["scopes"]
    refresh = data.get("refresh_token", "")
    if not isinstance(refresh, str) or ("offline_access" in scopes and not refresh):
        raise AuthError("OpenAI did not return a renewable session. Continue with ChatGPT to sign in again.")
    return {"access": data["access_token"], "refresh": refresh, "expires": time.time() + duration,
            "scopes": scopes, "id_token": data.get("id_token") or (previous or {}).get("id_token")}


class PlanClient:
    def __init__(self, *, path=None):
        local = os.environ.get("LOCALAPPDATA")
        self.supported = bool(os.name == "nt" and jwt and has_crypto and win32crypt and (path or local))
        self.vault = _Vault(Path(path) if path else Path(local or ".") / "AIP" / "ChatGPT" / "sessions.db")
        self.lock = threading.RLock()
        self.pending = None
        self.error = ""

    def status(self):
        result = {"supported": self.supported, "signed_in": False, "ready": False,
                  "pending": False, "error": "", "model": "", "models": [], "account": None, "accounts": []}
        if not self.supported:
            return {**result, "error": SETUP_ERROR}
        try:
            with self.lock, self.vault.state() as state:
                accounts = state["accounts"]
                account = accounts.get(state["active"], {})
                grant = account.get("grant") or {}
                signed_in = bool(grant)
                allowed = PLAN_SCOPES.issubset(grant.get("scopes", []))
                account_error = account.get("error") or (NO_PLAN if signed_in and not allowed else "")
                result.update(signed_in=signed_in, ready=bool(allowed and account["models"] and not account_error),
                              pending=self.pending is not None, error=self.error or account_error,
                              model=account.get("model", ""), models=account.get("models", []), account=state["active"],
                              accounts=[{"id": key, "label": item["label"]} for key, item in accounts.items()])
        except ProviderError as exc:
            result.update(error=str(exc))
        return result

    def sign_in(self, account=None):
        if not self.supported:
            raise ProviderError(SETUP_ERROR)
        with self.lock:
            if self.pending:
                raise ProviderError("A ChatGPT sign-in is already open. Finish it or choose Sign out to cancel.")
            with self.vault.state(update=True) as state:
                selected = None if account == "new" else account or state["active"]
                if selected is not None and (not isinstance(selected, str) or selected not in state["accounts"]):
                    raise ProviderError("Select a saved ChatGPT account or add a new one.")
                saved = state["accounts"].get(selected, {})
                client = saved.get("client", INITIAL_CLIENT)
                params = {"client_id": client, "ext_agent_host_id": state["host"], "response_type": "code",
                          "resource": RESOURCE, "scope": SCOPES, "code_challenge_method": "S256"}
                if not selected:
                    params["agent_name_hint"] = "AIP Drawing Workbench"
                elif saved.get("grant") and not PLAN_SCOPES.issubset(saved["grant"]["scopes"]):
                    params["prompt"] = "consent"
            owner = self

            class Callback(BaseHTTPRequestHandler):
                timeout = 10

                def log_message(self, *args):
                    pass  # The browser callback carries a one-use authorization code.

                def do_GET(self):
                    address = self.server.server_address
                    target = parse.urlsplit(self.path)
                    if self.headers.get("Host") != f"127.0.0.1:{address[1]}" or target.path != "/auth/callback":
                        self.send_error(404)
                        return
                    try:
                        values = parse.parse_qs(target.query, keep_blank_values=True, max_num_fields=24)
                        if any(len(value) != 1 for value in values.values()):
                            raise ValueError
                        ok = owner._callback(attempt, {k: v[0] for k, v in values.items()})
                    except ValueError:
                        ok = False
                    body = (b"Return to the AIP workbench to see your ChatGPT sign-in status." if ok
                            else b"This sign-in callback was not accepted. Return to the AIP workbench.")
                    self.send_response(200 if ok else 400)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            try:
                server = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
            except OSError:
                raise ProviderError("Windows could not open a loopback port to receive ChatGPT sign-in.") from None
            redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
            attempt = _Attempt(selected, client, secrets.token_urlsafe(32), secrets.token_urlsafe(32),
                               secrets.token_urlsafe(48), redirect, server)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode()).digest()).rstrip(b"=").decode()
            params.update(redirect_uri=redirect, nonce=attempt.nonce, state=attempt.state, code_challenge=challenge)
            self.pending, self.error = attempt, ""
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
            attempt.timer = threading.Timer(ATTEMPT_SECONDS, self._expire, args=(attempt,))
            attempt.timer.daemon = True
            attempt.timer.start()
            return {"authorization_url": AUTHORIZE + "?" + parse.urlencode(params), "pending": True}

    def _stop(self, attempt):
        if self.pending is attempt:
            self.pending = None
            if attempt.timer:
                attempt.timer.cancel()
            attempt.server.shutdown()
            attempt.server.server_close()

    def _expire(self, attempt):
        with self.lock:
            if self.pending is attempt and not attempt.claimed:
                self.error = "ChatGPT sign-in timed out. Choose Continue with ChatGPT to try again."
                self._stop(attempt)

    def close(self):
        with self.lock:
            if self.pending:
                self._stop(self.pending)

    def _callback(self, attempt, params):
        with self.lock:
            state = params.get("state", "")
            if self.pending is not attempt or attempt.claimed or not state.isascii() or not hmac.compare_digest(state, attempt.state):
                return False
            attempt.claimed = True
        try:
            if "error" in params:
                raise AuthError("ChatGPT sign-in was declined or interrupted. Check your account's plan settings.")
            client = params.get("client_id", attempt.client)
            if not client or len(client) > 256 or client == INITIAL_CLIENT or (attempt.account and client != attempt.client):
                raise AuthError("ChatGPT returned an unexpected registration. Start a fresh sign-in.")
            if not params.get("code") or len(params["code"]) > 16_384:
                raise AuthError("ChatGPT returned an incomplete authorization code.")
            data = _auth_request(TOKEN, {"grant_type": "authorization_code", "code": params["code"], "client_id": client,
                                         "code_verifier": attempt.verifier, "redirect_uri": attempt.redirect, "resource": RESOURCE})
            claims = _identity(data.get("id_token"), client, attempt.nonce)
            grant = _new_grant(data)
            with self.vault.state() as state:
                selected = state["accounts"].get(attempt.account)
                if selected and selected["subject"] != claims["sub"]:
                    raise AuthError("The signed-in ChatGPT identity does not match this saved account.")
            models, catalog_error = [], ""
            if PLAN_SCOPES.issubset(grant["scopes"]):
                try:
                    models = plan_inference.list_models(grant["access"])
                    if not models:
                        catalog_error = "OpenAI did not list any models for this account."
                except ProviderError as exc:
                    catalog_error = str(exc)
            with self.lock:
                if self.pending is not attempt:
                    return False
                with self.vault.state(update=True) as state:
                    accounts = state["accounts"]
                    key = next((key for key, value in accounts.items() if value["client"] == client), None)
                    if (attempt.account and key != attempt.account) or (key and accounts[key]["subject"] != claims["sub"]):
                        raise AuthError("The signed-in ChatGPT identity does not match this saved account.")
                    key = key or uuid.uuid4().hex
                    saved = accounts.get(key, {})
                    display = claims.get("email") if isinstance(claims.get("email"), str) else "ChatGPT account"
                    model = saved.get("model")
                    if model not in {m["slug"] for m in models}:
                        model = models[0]["slug"] if models else ""
                    accounts[key] = {"subject": claims["sub"], "client": client, "label": f"{display} ({key[:6]})",
                                     "grant": grant, "models": models, "model": model, "error": catalog_error}
                    state["active"] = key
                self.error = catalog_error
            return True
        except ProviderError as exc:
            with self.lock:
                if self.pending is attempt:
                    self.error = str(exc)
            return False
        finally:
            with self.lock:
                self._stop(attempt)

    def sign_out(self):
        if not self.supported:
            raise ProviderError(SETUP_ERROR)
        with self.lock:
            self.close()
            self.error = ""
            with self.vault.state(update=True) as state:
                account = state["accounts"].get(state["active"], {})
                grant = account.pop("grant", None) or {}
                if grant.get("refresh"):
                    try:
                        endpoint = _auth_request(ISSUER + "/.well-known/openid-configuration").get("revocation_endpoint")
                        if endpoint != ISSUER + "/api/accounts/oauth/revoke":
                            raise AuthError("OpenAI returned an unexpected revocation endpoint.")
                        form = {"token": grant["refresh"], "token_type_hint": "refresh_token", "client_id": account["client"]}
                        try:
                            _auth_request(endpoint, form, empty=True)
                        except AuthError as exc:
                            if not exc.retryable:
                                raise
                            time.sleep(0.2)
                            _auth_request(endpoint, form, empty=True)
                    except ProviderError:
                        self.error = "Signed out locally. OpenAI revocation was not confirmed. You can disconnect the app in ChatGPT Settings."
                account.update(models=[], model="", error="")
            return self.status()

    def generate(self, instructions, text, image=None, model=None):
        if not self.supported:
            raise ProviderError(SETUP_ERROR)
        failure = None
        with self.lock, self.vault.state(update=True) as state:
            account = state["accounts"].get(state["active"], {})
            grant = account.get("grant") or {}
            if not grant:
                raise ProviderError("Continue with ChatGPT before sending a request.")
            if not PLAN_SCOPES.issubset(grant["scopes"]):
                raise ProviderError(NO_PLAN)
            if account.get("error"):
                raise ProviderError(account["error"])
            choice = model or account["model"]
            if choice not in {m["slug"] for m in account["models"]}:
                raise ProviderError("Select a model listed by the signed-in ChatGPT account.")
            if grant["expires"] <= time.time() + 60:
                try:
                    if not grant["refresh"]:
                        raise AuthError("ChatGPT sign-in expired.", "token_expired")
                    renewed = _auth_request(TOKEN, {"grant_type": "refresh_token", "refresh_token": grant["refresh"],
                                                    "client_id": account["client"], "resource": RESOURCE})
                    if renewed.get("id_token"):
                        identity = _identity(renewed["id_token"], account["client"])
                        if identity["sub"] != account["subject"]:
                            raise AuthError("The refreshed ChatGPT identity did not match.", "invalid_grant")
                    grant = account["grant"] = _new_grant(renewed, grant)
                    if not PLAN_SCOPES.issubset(grant["scopes"]):
                        failure = NO_PLAN
                except AuthError as exc:
                    if exc.code not in REVOKED:
                        raise
                    account.update(grant=None, models=[], model="", error="")
                    failure = "Your ChatGPT session has ended. Choose Continue with ChatGPT to sign in again."
            if not failure:
                access = grant["access"]
                active = state["active"]
                account["model"] = choice
        if failure:
            raise ProviderError(failure)
        try:
            return plan_inference.generate(access, choice, instructions, text, image)
        except plan_inference.PlanTokenExpired:
            # A 401 can be a permission issue. Retain the renewable grant for explicit
            # reauthorization or revocation, and do not replay a drawing request.
            with self.lock, self.vault.state(update=True) as state:
                account = state["accounts"].get(active, {})
                if (account.get("grant") or {}).get("access") == access:
                    account["error"] = RECONNECT
            raise
