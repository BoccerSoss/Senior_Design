import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from aip.v2.providers import ProviderError
from aip.v3 import plan_inference as plan


def event(kind, **fields):
    data = json.dumps({"type": kind, **fields}).encode()
    return b"event: " + kind.encode() + b"\r\ndata: " + data + b"\r\n\r\n"


class PlanInferenceTests(unittest.TestCase):
    def assert_call(self, mock, method, path):
        mock.assert_called_once()
        req = mock.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.openai.com/v1/" + path)
        self.assertEqual(req.get_method(), method)
        self.assertEqual(req.get_header("Authorization"), "Bearer subscription-token")
        return req

    def test_models_show_only_account_visible_slugs_in_service_order(self):
        models = [
            {"visibility": "hide", "slug": "hidden"},
            {"visibility": "list", "slug": "second", "display_name": "Second"},
            {"visibility": "list", "slug": "second", "display_name": "Duplicate"},
            {"visibility": "list", "slug": "first"},
        ]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "separate-billing", "OPENAI_BASE_URL": "https://example.test"}), \
             patch.object(plan, "_open_request", return_value=io.BytesIO(json.dumps({"models": models}).encode())) as mock:
            result = plan.list_models("subscription-token")
        self.assertEqual(result, [{"slug": "second", "display_name": "Second"}, {"slug": "first", "display_name": "first"}])
        self.assertIsNone(self.assert_call(mock, "GET", "models").data)

    def test_non_plan_catalog_is_rejected(self):
        with patch.object(plan, "_open_request", return_value=io.BytesIO(b'{"data":[]}')), self.assertRaises(ProviderError):
            plan.list_models("subscription-token")

    def test_text_and_image_payloads_use_plan_stream(self):
        image = "data:image/png;base64,aW1hZ2U="
        for drawing in (None, image):
            with self.subTest(image=drawing):
                feed = (b": keepalive\n\n" + event("response.output_text.delta", delta="Hello") +
                        event("response.output_text.delta", delta=" Ω") + event("response.completed"))
                with patch.object(plan, "_open_request", return_value=io.BytesIO(feed)) as mock:
                    self.assertEqual(plan.generate("subscription-token", "allowed", "instructions", "drawing?", drawing), "Hello Ω")
                req = self.assert_call(mock, "POST", "responses")
                self.assertEqual(req.get_header("Accept"), "text/event-stream")
                content = [{"type": "input_text", "text": "drawing?"}]
                if drawing:
                    content.append({"type": "input_image", "image_url": drawing})
                self.assertEqual(json.loads(req.data), {"model": "allowed", "store": False, "stream": True,
                    "instructions": "instructions", "input": [{"role": "user", "content": content}]})

    def test_terminal_response_supplies_authoritative_answer(self):
        final = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "all"}]}]}
        with patch.object(plan, "_open_request", return_value=io.BytesIO(event("response.completed", response=final))):
            self.assertEqual(plan.generate("t", "m", "", "q"), "all")

    def test_partial_answers_and_corrupted_streams_are_never_accepted(self):
        for ending in (b"", b"data: [DONE]\n\n", b"data: {bad}\n\n", event("response.cancelled"),
                       event("response.incomplete"), event("response.completed", response={"status": "incomplete"}),
                       event("response.completed").rstrip(), event("response.failed")):
            feed = io.BytesIO(event("response.output_text.delta", delta="partial") + ending)
            with self.subTest(ending=ending), patch.object(plan, "_open_request", return_value=feed) as mock:
                with self.assertRaises(ProviderError) as caught:
                    plan.generate("t", "m", "", "q")
                self.assertNotIn("partial", str(caught.exception))
                mock.assert_called_once()

    def test_failed_stream_reports_specific_allowance_action(self):
        err = {"code": "subscription_sharing_usage_limit_exceeded", "message": "private text"}
        stream = io.BytesIO(event("response.failed", response={"error": err}))
        with patch.object(plan, "_open_request", return_value=stream), self.assertRaises(ProviderError) as caught:
            plan.generate("t", "m", "", "q")
        self.assertIn("Settings", str(caught.exception))
        self.assertNotIn("private text", str(caught.exception))

    def test_model_discovery_and_stream_byte_limits(self):
        for operation in (lambda: plan.list_models("t"), lambda: plan.generate("t", "m", "", "q")):
            with self.subTest(operation=operation), patch.object(plan, "MAX_RESPONSE_BYTES", 20), \
                 patch.object(plan, "_open_request", return_value=io.BytesIO(b"x" * 22)):
                with self.assertRaisesRegex(ProviderError, "size limit"):
                    operation()

    def test_http_status_actions_no_retries_or_raw_error_disclosure(self):
        for status, body, kind, action in [
            (401, {"detail": "private text"}, plan.PlanTokenExpired, "Sign in again"),
            (403, {"error": {"code": "subscription_sharing_user_not_eligible"}}, ProviderError, "ineligible"),
            (503, {"detail": "private text"}, ProviderError, "later"),
            (307, {"detail": "private text"}, ProviderError, "HTTP 307"),
            (429, {"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "private text"}},
             ProviderError, "allowance"),
        ]:
            headers = {"Retry-After": "40", "x-request-id": "req_ba48cde386a93f38", "Location": "https://example.test"}
            error = HTTPError(plan._RESPONSES, status, "private text", headers, io.BytesIO(json.dumps(body).encode()))
            with self.subTest(status=status), patch.object(plan, "_open_request", side_effect=error) as mock:
                with self.assertRaises(kind) as caught:
                    plan.generate("t", "m", "", "q")
                message = str(caught.exception)
                self.assertIn(f"HTTP {status}", message)
                self.assertIn(action, message)
                self.assertIn("req_ba48cde386a93f38", message)
                self.assertEqual(caught.exception.http_status, status)
                self.assertEqual(caught.exception.body_shape, next(iter(body)))
                if status != 401:
                    self.assertNotIsInstance(caught.exception, plan.PlanTokenExpired)
                if status == 429:
                    self.assertIn("40 seconds", message)
                self.assertNotIn("private text", message)
                self.assertNotIn("example.test", message)
                mock.assert_called_once()

    def test_reject_invalid_data_before_network(self):
        for token, model, image in (("\r", "valid", None), ("t", "\r", None), ("t", "m", "https://example.test/a.png"),
                                    ("t", "m", "data:image/png;base64,%%%%"), ("t", "m", "data:image/svg+xml;base64,YQ==")):
            with self.subTest(model=model, image=image), patch.object(plan, "_open_request") as mock:
                with self.assertRaises(ProviderError):
                    plan.generate(token, model, "", "q", image)
                mock.assert_not_called()
