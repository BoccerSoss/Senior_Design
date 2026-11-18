"""Use the signed-in user's ChatGPT plan on the two supported inference routes."""

import base64
import binascii
from contextlib import contextmanager
from http.client import HTTPException
import json
import re
from urllib import error, request

from aip.v2.providers import (
    MAX_CONTEXT_CHARS, MAX_IMAGE_BYTES, MAX_RESPONSE_BYTES, ProviderError,
    _open_request, _response_text, _retry_seconds,
)


class PlanTokenExpired(ProviderError):
    """The plan access token was missing or rejected."""


_MODELS = "https://api.openai.com/v1/models"
_RESPONSES = "https://api.openai.com/v1/responses"
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")
_IMAGE_PREFIXES = {f"data:image/{mime};base64" for mime in ("png", "jpeg", "webp", "gif")}

_ACTIONS = {
    "subscription_sharing_user_not_eligible":
        "This account is ineligible for ChatGPT plan sharing. Check the signed-in account and workspace.",
    "subscription_sharing_usage_limit_exceeded":
        "The ChatGPT plan or its allowance for this app reached a limit. Check Settings → Usage in ChatGPT.",
    "subscription_sharing_usage_unavailable": "ChatGPT usage accounting is temporarily unavailable. Try again later.",
    "subscription_sharing_user_unavailable": "ChatGPT account information is temporarily unavailable. Try again later.",
    "subscription_sharing_unsupported_capability": "The selected model or ChatGPT plan does not support this request.",
    "subscription_sharing_route_not_supported": "This route is unavailable for ChatGPT plan sharing.",
    "subscription_sharing_invalid_user": "The ChatGPT sign-in is no longer valid. Sign in again.",
    "chatpass_v2_scope_not_authorized": "Sign in again and authorize model requests for this app.",
    "chatpass_v2_invalid_authorization_context": "Sign in again and authorize model requests for this app.",
}


def _service_error(status=None, details=None, headers=None, body_shape="event"):
    code = details.get("code") if isinstance(details, dict) else None
    if isinstance(code, str) and code in _ACTIONS:
        message = f"{_ACTIONS[code]} ({code})"
    elif status == 401:
        message = "The ChatGPT sign-in is no longer valid. Sign in again."
    elif status == 403:
        message = "Check whether the signed-in ChatGPT account permits plan sharing with this app."
    elif status == 429:
        message = "ChatGPT reported a usage or rate limit. Check Settings → Usage in ChatGPT."
    elif status in (400, 404, 422):
        message = "Check that the selected ChatGPT model supports this request."
    else:
        message = "ChatGPT could not finish this request. Try again later."
    if status == 429:
        delay = _retry_seconds((headers or {}).get("Retry-After"))
        if delay is not None:
            message += f" Wait at least {delay} seconds before trying again."
    param = details.get("param") if isinstance(details, dict) else None
    if isinstance(param, str) and re.fullmatch(r"(?:model|instructions|input|store|stream)(?:\[\d{1,5}\]|\.(?:content|type|image_url|text|role)){0,12}", param):
        message += f" Request field: {param}."
    request_id = (headers or {}).get("x-request-id")
    if isinstance(request_id, str) and re.fullmatch(r"(?:req_)?[0-9a-fA-F-]{16,64}", request_id):
        message += f" Request ID: {request_id}."
    label = f"ChatGPT request failed (HTTP {status})." if status is not None else "ChatGPT stream failed."
    exception = PlanTokenExpired if status == 401 else ProviderError
    problem = exception(f"{label} {message}")
    problem.body_shape = body_shape
    problem.http_status = status
    return problem


@contextmanager
def _response(access_token, payload=None):
    if not isinstance(access_token, str) or not re.fullmatch(r"[!-~]{1,65536}", access_token):
        raise PlanTokenExpired("Sign in to ChatGPT to authorize this request.")
    headers = {"Authorization": "Bearer " + access_token}
    if payload is None:
        headers["Accept"] = "application/json"
        req = request.Request(_MODELS, headers=headers, method="GET")
    else:
        headers.update({"Accept": "text/event-stream", "Content-Type": "application/json"})
        req = request.Request(_RESPONSES, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        # The shared HTTPS opener rejects redirects before forwarding credentials or drawing data.
        with _open_request(req) as response:
            yield response
    except error.HTTPError as exc:
        details = None
        body_shape = "unknown"
        with exc:
            try:
                body = exc.read(8193)
                if len(body) <= 8192:
                    data = json.loads(body)
                    if isinstance(data, dict):
                        details = data.get("error")
                        if isinstance(details, dict):
                            body_shape = "error"
                        elif "detail" in data:
                            body_shape = "detail"
            except (ValueError, RecursionError, OSError, HTTPException):
                pass
        raise _service_error(exc.code, details, exc.headers, body_shape) from None
    except (error.URLError, OSError, HTTPException):
        raise ProviderError("The connection to ChatGPT was interrupted. Check the network and try again.") from None


def _object(data):
    try:
        obj = json.loads(data)
    except (ValueError, RecursionError):
        obj = None
    if not isinstance(obj, dict):
        raise ProviderError("ChatGPT returned an invalid response.")
    return obj


def list_models(access_token):
    """List models exposed to this signed-in ChatGPT user in service order."""
    with _response(access_token) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ProviderError("The ChatGPT model list exceeded the response size limit.")
    models = _object(body).get("models")
    if not isinstance(models, list):
        raise ProviderError("ChatGPT returned an invalid model list.")
    result = []
    seen = set()
    for model in models:
        if not isinstance(model, dict) or model.get("visibility") != "list":
            continue
        slug = model.get("slug")
        if not isinstance(slug, str) or not _MODEL_ID.fullmatch(slug) or slug in seen:
            continue
        seen.add(slug)
        name = model.get("display_name")
        result.append({"slug": slug, "display_name": name if isinstance(name, str) and name.strip() else slug})
    return result


def _events(stream):
    remaining = MAX_RESPONSE_BYTES
    parts = []
    while True:
        raw = stream.readline(remaining + 1)
        if not raw:
            break
        remaining -= len(raw)
        if remaining < 0:
            raise ProviderError("The ChatGPT stream exceeded the response size limit.")
        line = raw.rstrip(b"\r\n")
        if line.startswith(b"data:"):
            parts.append(line[5:].removeprefix(b" "))
        elif not line and parts:
            yield b"\n".join(parts)
            parts.clear()


def _read_answer(response):
    deltas = []
    for data in _events(response):
        if data == b"[DONE]":
            break
        event = _object(data)
        kind = event.get("type")
        if kind == "response.output_text.delta":
            delta = event.get("delta")
            if not isinstance(delta, str):
                raise ProviderError("ChatGPT returned invalid answer text.")
            deltas.append(delta)
        elif kind == "response.completed":
            final = event.get("response", {})
            if not isinstance(final, dict) or final.get("status", "completed") != "completed" or final.get("error"):
                raise ProviderError("ChatGPT did not return a completed answer.")
            if "output" in final:
                return _response_text({"provider": "openai"}, final)
            answer = "".join(deltas).strip()
            if not answer:
                raise ProviderError("ChatGPT returned no answer text.")
            return answer
        elif kind in ("response.incomplete", "response.cancelled"):
            raise ProviderError("ChatGPT stopped before completion. Try a smaller drawing or question.")
        elif kind in ("error", "response.failed"):
            failed = event.get("response")
            details = failed.get("error") if isinstance(failed, dict) else event.get("error", event)
            raise _service_error(details=details)
    raise ProviderError("The ChatGPT stream ended before the answer was complete. Try again.")


def _validate_image(image):
    if not isinstance(image, str) or len(image) > 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 32:
        raise ProviderError("Use a supported drawing image within the image size limit.")
    prefix, _, encoded = image.partition(",")
    if prefix not in _IMAGE_PREFIXES or not encoded:
        raise ProviderError("ChatGPT vision requires a base64 PNG, JPEG, WebP or GIF image.")
    try:
        size = len(base64.b64decode(encoded, validate=True))
    except (ValueError, binascii.Error):
        raise ProviderError("The drawing image has invalid base64 data.") from None
    if not 0 < size <= MAX_IMAGE_BYTES:
        raise ProviderError("The drawing image exceeds the image size limit.")


def generate(access_token, model, instructions, text, image=None):
    """Submit text and, optionally, one in-memory image data URL. Return completed text."""
    if not isinstance(model, str) or not _MODEL_ID.fullmatch(model):
        raise ProviderError("Choose a model from the signed-in ChatGPT account.")
    if not isinstance(text, str) or not text.strip() or not isinstance(instructions, str):
        raise ProviderError("ChatGPT requires a text question or drawing analysis request.")
    if len(text) + len(instructions) > MAX_CONTEXT_CHARS:
        raise ProviderError("The ChatGPT request is too large. Use a smaller drawing or question.")
    content = [{"type": "input_text", "text": text}]
    if image is not None:
        _validate_image(image)
        content.append({"type": "input_image", "image_url": image})
    payload = {"model": model, "instructions": instructions, "input": [{"role": "user", "content": content}],
               "stream": True, "store": False}
    with _response(access_token, payload) as response:
        return _read_answer(response)
