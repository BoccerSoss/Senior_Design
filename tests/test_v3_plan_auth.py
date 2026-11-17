"""The only network peer in these tests is the workbench's own loopback callback."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from urllib import error, parse, request

from aip.v2.providers import ProviderError
from aip.v3 import plan


@unittest.skipUnless(plan.PlanClient().supported, "Requires V3's Windows sign-in dependencies")
class PlanAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives.asymmetric import rsa
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key = json.loads(plan.jwt.algorithms.RSAAlgorithm.to_jwk(cls.private.public_key()))
        cls.keys = {"keys": [{**key, "kid": "synthetic", "alg": "RS256", "use": "sig"}]}

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name, "test.db")
        self.client = plan.PlanClient(path=self.path)
        self.addCleanup(self.client.close)
        self.subject = "synthetic-subject"
        self.granted = plan.SCOPES
        self.renew_error = None
        self.forms = []
        self.claim_edits = {}
        self.signing_key = self.private
        self.client_id = "oaiapp_synthetic"
        self.endpoints = patch.object(plan, "_auth_request", side_effect=self.auth)
        self.endpoints.start()
        self.addCleanup(self.endpoints.stop)
        catalog = patch.object(plan.plan_inference, "list_models", return_value=[
            {"slug": "first", "display_name": "First"}, {"slug": "second", "display_name": "Second"}])
        self.catalog = catalog.start()
        self.addCleanup(catalog.stop)
        transport = patch.object(plan.plan_inference, "generate", return_value="synthetic answer")
        self.transport = transport.start()
        self.addCleanup(transport.stop)
        self.discovery = {"issuer": plan.ISSUER, "jwks_uri": plan.ISSUER + "/.well-known/jwks.json",
                          "revocation_endpoint": plan.ISSUER + "/api/accounts/oauth/revoke"}

    def auth(self, url, form=None, *, empty=False):
        if form:
            self.forms.append((url, form))
        if url.endswith("/openid-configuration"):
            return self.discovery
        if url.endswith("/jwks.json"):
            return self.keys
        if url.endswith("/revoke"):
            return {}
        self.assertEqual(url, plan.TOKEN)
        if form["grant_type"] == "refresh_token":
            if self.renew_error:
                raise self.renew_error
            return {"access_token": "synthetic-rotated-access", "refresh_token": "synthetic-rotated-refresh",
                    "expires_in": 3600, "token_type": "Bearer"}
        claims = {"iss": plan.ISSUER, "aud": form["client_id"], "sub": self.subject,
                  "exp": int(time.time()) + 3600, "email": "test@example.invalid",
                  "nonce": self.query["nonce"], **self.claim_edits}
        token = plan.jwt.encode(claims, self.signing_key, algorithm="RS256", headers={"kid": "synthetic"})
        return {"id_token": token, "access_token": "synthetic-bearer-token", "refresh_token": "synthetic-refresh-secret",
                "expires_in": 3600, "scope": self.granted, "token_type": "Bearer"}

    def start(self, account=None):
        url = parse.urlsplit(self.client.sign_in(account=account)["authorization_url"])
        self.assertEqual((url.scheme, url.netloc, url.path),
                         ("https", "auth.openai.com", "/api/accounts/authorize"))
        self.query = dict(parse.parse_qsl(url.query))
        self.assertEqual(self.query["code_challenge_method"], "S256")
        self.assertNotIn("client_secret", self.query)
        self.assertNotIn("id_token_hint", self.query)
        return self.query

    def callback(self, expected=200, **overrides):
        response = {"state": self.query["state"], "code": "one-use-test-code", **overrides}
        if self.query["client_id"] == plan.INITIAL_CLIENT:
            response.setdefault("client_id", self.client_id)
        url = self.query["redirect_uri"] + "?" + parse.urlencode(response)
        local = request.build_opener(request.ProxyHandler({}))
        try:
            result = local.open(url, timeout=10)
        except error.HTTPError as exc:
            result = exc
        with result:
            self.assertEqual(result.code, expected)
            self.assertNotIn(b"one-use-test-code", result.read())

    def connect(self):
        self.start()
        self.callback()
        return self.client.status()

    def expire_grant(self):
        with self.client.vault.state(update=True) as state:
            state["accounts"][state["active"]]["grant"]["expires"] = time.time() - 1

    def test_first_signin_pkce_restart_and_logout(self):
        self.assertFalse(self.path.exists())
        self.assertFalse(self.client.status()["signed_in"])
        initial = self.start()
        self.assertTrue(self.client.status()["pending"])
        with self.assertRaisesRegex(ProviderError, "already open"):
            self.client.sign_in()
        self.callback(expected=400, state="wrong-state")
        self.assertTrue(self.client.status()["pending"])
        self.assertEqual(self.forms, [])
        self.callback()
        code_form = self.forms[0][1]
        expected = base64.urlsafe_b64encode(hashlib.sha256(code_form["code_verifier"].encode()).digest()).decode().rstrip("=")
        self.assertEqual(initial["code_challenge"], expected)
        self.assertEqual(code_form["client_id"], self.client_id)
        self.assertEqual(code_form["redirect_uri"], initial["redirect_uri"])
        restarted = plan.PlanClient(path=self.path)
        status = restarted.status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["model"], "first")
        self.assertNotIn("synthetic-bearer-token", json.dumps(status))
        self.assertNotIn(b"synthetic-refresh-secret", self.path.read_bytes())
        self.assertNotIn(b"test@example.invalid", self.path.read_bytes())
        self.assertEqual(restarted.generate("instruction", "input", model="second"), "synthetic answer")
        self.transport.assert_called_once_with("synthetic-bearer-token", "second", "instruction", "input", None)
        self.assertEqual(restarted.status()["model"], "second")
        with self.assertRaisesRegex(ProviderError, "listed"):
            restarted.generate("instruction", "input", model="unlisted")
        self.assertFalse(restarted.sign_out()["signed_in"])
        self.assertEqual(self.forms[-1][1], {"token": "synthetic-refresh-secret", "token_type_hint": "refresh_token",
                                             "client_id": self.client_id})
        returning = self.start()
        self.assertEqual(returning["ext_agent_host_id"], initial["ext_agent_host_id"])
        self.assertEqual(returning["client_id"], self.client_id)
        self.assertNotEqual(returning["state"], initial["state"])
        self.assertNotIn("agent_name_hint", returning)

    def test_limited_grant_denies_inference_and_supports_explicit_reconsent(self):
        self.granted = "openid profile email"
        status = self.connect()
        self.assertTrue(status["signed_in"])
        self.assertFalse(status["ready"])
        self.catalog.assert_not_called()
        with self.assertRaisesRegex(ProviderError, "granted"):
            self.client.generate("instructions", "text")
        self.transport.assert_not_called()
        self.assertEqual(self.start()["prompt"], "consent")

    def test_declined_and_new_registration_without_issued_client(self):
        self.start()
        self.callback(expected=400, error="access_denied")
        self.assertFalse(self.client.status()["pending"])
        self.assertIn("declined", self.client.status()["error"])
        self.start()
        self.callback(expected=400, client_id="")
        self.assertFalse(self.client.status()["signed_in"])
        self.assertEqual(self.forms, [])

    def test_limited_discovery_and_oidc_checks(self):
        self.start()
        self.discovery["jwks_uri"] = "https://example.invalid/foreign-keys"
        self.callback(expected=400)
        self.assertFalse(self.client.status()["signed_in"])
        self.discovery["jwks_uri"] = plan.ISSUER + "/.well-known/jwks.json"
        for changed in [{"iss": "untrusted"}, {"aud": "another-registration"},
                        {"nonce": "wrong-nonce"}, {"exp": int(time.time()) - 90}]:
            with self.subTest(claim=list(changed)):
                self.claim_edits = changed
                self.start()
                self.callback(expected=400)
                self.assertFalse(self.client.status()["signed_in"])
        self.catalog.assert_not_called()

    def test_invalid_signature_is_rejected(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.start()
        self.callback(expected=400)
        self.assertIn("identity verification failed", self.client.status()["error"])
        self.assertFalse(self.client.status()["signed_in"])
        self.catalog.assert_not_called()

    def test_expired_attempt_closes_listener_and_allows_fresh_signin(self):
        self.start()
        old_state = self.query["state"]
        self.client._expire(self.client.pending)
        self.assertFalse(self.client.status()["pending"])
        self.assertIn("timed out", self.client.status()["error"])
        self.assertNotEqual(self.start()["state"], old_state)
        self.assertEqual(self.forms, [])

    def test_failed_switch_preserves_verified_account(self):
        active = self.connect()["account"]
        self.start()
        self.subject = "different"
        self.callback(expected=400)
        self.assertEqual(self.client.status()["account"], active)
        self.assertTrue(self.client.status()["ready"])
        self.catalog.assert_called_once()
        self.start(account="new")
        self.client_id = "oaiapp_another"
        self.callback()
        status = self.client.status()
        self.assertNotEqual(status["account"], active)
        self.assertEqual(len(status["accounts"]), 2)
        self.assertEqual(self.start(account=active)["client_id"], "oaiapp_synthetic")

    def test_refresh_is_serialized_between_workbenches(self):
        self.connect()
        self.expire_grant()
        second = plan.PlanClient(path=self.path)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda client: client.generate("instructions", "text"), [self.client, second]))
        self.assertEqual(results, ["synthetic answer"] * 2)
        self.assertEqual(sum(form.get("grant_type") == "refresh_token" for _, form in self.forms), 1)
        self.assertEqual(self.transport.call_count, 2)
        self.assertTrue(all(call.args[0] == "synthetic-rotated-access" for call in self.transport.call_args_list))
        with self.client.vault.state() as state:
            self.assertEqual(state["accounts"][state["active"]]["grant"]["refresh"], "synthetic-rotated-refresh")

    def test_terminal_refresh_and_network_failure_have_distinct_recovery(self):
        self.connect()
        self.expire_grant()
        self.renew_error = plan.AuthError("temporary", retryable=True)
        with self.assertRaisesRegex(ProviderError, "temporary"):
            self.client.generate("instructions", "text")
        self.assertTrue(self.client.status()["signed_in"])
        self.renew_error = plan.AuthError("expired", "refresh_token_reused")
        with self.assertRaisesRegex(ProviderError, "session has ended"):
            self.client.generate("instructions", "text")
        self.assertFalse(self.client.status()["signed_in"])
        self.assertEqual(self.start()["client_id"], self.client_id)
        self.transport.assert_not_called()

    def test_401_marks_only_this_grant_and_never_replays(self):
        self.connect()
        self.transport.side_effect = plan.plan_inference.PlanTokenExpired("Unauthorized")
        with self.assertRaisesRegex(ProviderError, "Unauthorized"):
            self.client.generate("instructions", "text")
        self.assertFalse(self.client.status()["ready"])
        self.assertIn("sign in again", self.client.status()["error"])
        self.transport.assert_called_once()

    def test_signout_clears_tokens_after_revocation_failure(self):
        self.connect()
        with patch.object(plan, "_auth_request", side_effect=plan.AuthError("failed")):
            status = self.client.sign_out()
        self.assertFalse(status["signed_in"])
        self.assertIn("revocation was not confirmed", status["error"])
        with self.client.vault.state() as state:
            self.assertNotIn("grant", state["accounts"][state["active"]])
