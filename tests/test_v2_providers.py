"""Provider boundary tests: no credentials and no network traffic are required."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from aip.v2 import providers
from aip.v2.providers import ProviderError, ask_provider, extract_provider, normalize_extraction, provider_status


def graph():
    return {"components": [
        {"id": "node-a", "tag": "READ-47", "type": "pump", "bbox": [10, 10, 30, 30]},
        {"id": "node-b", "tag": None, "type": "gate valve", "bbox": [60, 10, 80, 30]},
        {"id": "node-c", "tag": "TO DRAWING Z", "type": "connector", "bbox": [85, 10, 100, 30]},
    ], "connections": [
        {"id": "line-a", "source": "node-a", "target": "node-b", "kind": "pipe",
         "path": [[30, 20], [60, 20]], "direction": "source_to_target"},
    ], "arrows": [
        {"id": "arrow-a", "bbox": [40, 15, 50, 25], "tail": [40, 20], "tip": [50, 20], "connection_id": "line-a"},
    ], "connectors": [{"component_id": "node-c", "observed_text": "TO DRAWING Z", "target_sheet": "Z"}],
        "uncertainties": ["An untagged valve needs review."]}


def response(payload):
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


def responses_text(text):
    return {"status": "completed", "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": text}]},
    ]}


class ConfigurationTests(unittest.TestCase):
    def test_status_is_read_only_and_does_not_return_secrets(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-value"}, clear=True), patch.object(providers, "_open_request") as opened:
            result = provider_status({"provider": "openai", "model": "organization-selected-model"})
        self.assertTrue(result["ready"])
        self.assertTrue(result["key_available"])
        self.assertNotIn("secret-value", json.dumps(result))
        self.assertFalse(opened.called)

    def test_model_has_no_fixed_default_and_environment_is_supported(self):
        with patch.dict(os.environ, {}, clear=True):
            result = provider_status({"provider": "openai"})
            self.assertFalse(result["ready"])
            self.assertEqual(result["model"], "")
            with self.assertRaisesRegex(ProviderError, "model ID"):
                ask_provider({"provider": "openai"}, "Question", {})
        with patch.dict(os.environ, {"AIP_AI_MODEL": "chosen-model", "AIP_AI_BASE_URL": "http://127.0.0.1:8000/v1"}, clear=True):
            self.assertTrue(provider_status(SimpleNamespace(provider="compatible"))["ready"])

    def test_arbitrary_gateway_prefix_and_full_endpoint(self):
        settings = {"provider": "compatible", "model": "custom", "base_url": "https://approved.example/api/team/openai/v1/"}
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(provider_status(settings)["base_url"], settings["base_url"].rstrip("/"))
            self.assertTrue(provider_status(settings)["ready"])
        self.assertEqual(providers._endpoint("https://approved.example/v1/chat/completions", "chat/completions"),
                         "https://approved.example/v1/chat/completions")

    def test_invalid_urls_are_rejected_without_echoing_credentials(self):
        addresses = ["https://secret-user:secret-key@example.com/v1", "http://external.example/v1",
                     "file:///C:/private", "https://example.com/v1?key=secret-key", "https://example.com/v1#secret-key",
                     "https://example.com:99999/v1", "https://example.com/v1/../admin", "https://example.com/%2e%2e/admin",
                     "https://example.com/\nsecret-key", "https://example.com\\@127.0.0.1/v1"]
        with patch.dict(os.environ, {}, clear=True), patch.object(providers, "_open_request") as opened:
            for url in addresses:
                with self.subTest(url=url):
                    result = provider_status({"provider": "compatible", "model": "custom", "base_url": url})
                    self.assertFalse(result["ready"])
                    self.assertNotIn("secret-key", json.dumps(result))
            self.assertFalse(opened.called)

    def test_loopback_http_is_allowed_and_local_never_sends_cloud_key(self):
        for provider in ("local", "compatible"):
            for url in ("http://localhost:1234/v1", "http://127.0.0.1:1234/v1", "http://[::1]:1234/v1"):
                with self.subTest(provider=provider, url=url), patch.dict(os.environ, {"OPENAI_API_KEY": "cloud-key"}, clear=True), \
                        patch.object(providers, "_open_request", return_value=response({"choices": [{"message": {"content": "local answer"}}]})) as opened:
                    settings = {"provider": provider, "model": "local-model", "base_url": url}
                    status = provider_status(settings)
                    self.assertTrue(status["ready"])
                    self.assertFalse(status["key_available"])
                    self.assertIsNone(status["key_env"])
                    ask_provider(settings, "Question", {})
                    self.assertIsNone(opened.call_args.args[0].get_header("Authorization"))


class ExtractionValidationTests(unittest.TestCase):
    def test_geometry_direction_and_unconfirmed_metadata_are_preserved(self):
        raw = graph()
        before = copy.deepcopy(raw)
        raw["components"][0]["review_status"] = "confirmed"
        raw["connectors"][0]["status"] = "confirmed"
        result = normalize_extraction(raw, 100, 100)
        self.assertEqual(result["components"][0]["tag"], "READ-47")
        self.assertEqual(result["components"][1]["tag"], "")
        self.assertEqual(result["components"][0]["bbox"], [10, 10, 30, 30])
        self.assertEqual(result["connections"][0]["direction"], "source_to_target")
        self.assertEqual(result["arrows"][0]["tip"], [50, 20])
        self.assertEqual(result["connectors"][0]["status"], "unconfirmed")
        self.assertEqual(result["components"][0]["review_status"], "unreviewed")
        self.assertTrue(result["review_required"])
        self.assertEqual(raw["connections"], before["connections"])

    def test_extraction_matches_store_review_and_connector_contract(self):
        from aip.v2.store import Store
        raw = graph()
        raw["components"][2]["type"] = "off-page connector"
        result = normalize_extraction(raw, 100, 100)
        self.assertEqual(result["components"][2]["type"], "connector")
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            try:
                store.add_sheet({"id": "unit-sheet", "title": "Unit image", "facility_id": "facility-local",
                                 "width": 100, "height": 100, "components": [], "connections": [],
                                 "source_metadata": {}})
                store.replace_analysis("unit-sheet", result["components"], result["connections"], {"method": "unit AI candidate"})
                saved = store.sheet("unit-sheet")
                self.assertEqual(saved["components"][1]["tag"], "")
                self.assertTrue(all(item["review_status"] == "unreviewed" for item in saved["components"]))
            finally:
                store.db.close()

    def test_unknown_type_stays_unknown_and_hash_cannot_select_a_template(self):
        first = graph()
        first["components"][0].update({"type": "imaginary manual widget", "tag": "VISIBLE-Q-999"})
        first["image"] = {"sha256": "a" * 64}
        second = copy.deepcopy(first)
        second["image"]["sha256"] = "b" * 64
        first_result = normalize_extraction(first, 100, 100)
        self.assertEqual(first_result, normalize_extraction(second, 100, 100))
        self.assertEqual(first_result["components"][0]["type"], "unknown")
        self.assertEqual(first_result["components"][0]["observed_type"], "imaginary manual widget")
        self.assertEqual(first_result["components"][0]["tag"], "VISIBLE-Q-999")
        self.assertNotIn("sha256", first_result["image"])

    def test_bad_boxes_are_rejected_instead_of_clipped(self):
        for box in (None, [1, 2, 3], [-1, 1, 5, 5], [1, 1, 101, 5], [3, 3, 3, 4],
                    [9, 1, 1, 9], [True, 1, 4, 5], [1, 1, float("nan"), 5], [1, 1, float("inf"), 5]):
            with self.subTest(box=box):
                raw = graph()
                raw["components"][0]["bbox"] = box
                with self.assertRaises(ProviderError):
                    normalize_extraction(raw, 100, 100)

    def test_bad_graphs_and_polylines_are_rejected(self):
        modifications = [
            lambda raw: raw["components"].append(copy.deepcopy(raw["components"][0])),
            lambda raw: raw["connections"][0].update(source="absent"),
            lambda raw: raw["connections"][0].update(target="node-a"),
            lambda raw: raw["connections"][0].update(kind="guessed pipe"),
            lambda raw: raw["connections"][0].update(kind=[]),
            lambda raw: raw["connections"][0].update(direction=[]),
            lambda raw: raw["connections"][0].update(path=[[1, 1]]),
            lambda raw: raw["connections"][0].update(path=[[1, 1], [101, 20]]),
            lambda raw: raw["arrows"][0].update(connection_id="absent"),
            lambda raw: raw["arrows"][0].update(connection_id={}),
            lambda raw: raw["arrows"][0].update(tip=[40, 20]),
            lambda raw: raw["connectors"][0].update(component_id="node-a"),
            lambda raw: raw["components"][0].update(confidence=1.1),
            lambda raw: raw.update(uncertainties=[{}]),
        ]
        for modify in modifications:
            with self.subTest(modify=modify):
                raw = graph()
                modify(raw)
                with self.assertRaises(ProviderError):
                    normalize_extraction(raw, 100, 100)


class TransportTests(unittest.TestCase):
    def test_openai_uses_responses_image_api_and_real_image_hash(self):
        image = b"unit-image-bytes"
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), \
                patch.object(providers, "_open_request", return_value=response(responses_text(json.dumps(graph())))) as opened:
            result = extract_provider({"provider": "openai", "model": "user-selected-vision-model"}, image, "image/png", 100, 100)
        req = opened.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(req.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(payload["model"], "user-selected-vision-model")
        self.assertFalse(payload["store"])
        self.assertTrue(payload["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,"))
        self.assertIn("Never extrapolate names", payload["instructions"])
        self.assertEqual(result["image"]["sha256"], hashlib.sha256(image).hexdigest())
        self.assertEqual(result["model"], "user-selected-vision-model")

    def test_anthropic_uses_messages_image_api(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True), \
                patch.object(providers, "_open_request", return_value=response({"content": [{"type": "text", "text": "```json\n" + json.dumps(graph()) + "\n```"}]})) as opened:
            extract_provider({"provider": "anthropic", "model": "user-selected-vision-model"}, b"image", "image/png", 100, 100)
        req = opened.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(req.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(req.get_header("X-api-key"), "test-key")
        self.assertIn("Anthropic-version", dict(req.header_items()))
        self.assertEqual(payload["messages"][0]["content"][0]["source"]["media_type"], "image/png")

    def test_compatible_server_receives_question_context_and_image(self):
        settings = {"provider": "compatible", "model": "approved-model", "base_url": "https://approved.example/team/v1"}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "gateway-key"}, clear=True), \
                patch.object(providers, "_open_request", return_value=response({"choices": [{"message": {"content": "Sheet S, component C: observed evidence."}}]})) as opened:
            result = ask_provider(settings, "What connects?", {"sheet_id": "S", "component_id": "C"})
        req = opened.call_args.args[0]
        self.assertEqual(req.full_url, "https://approved.example/team/v1/chat/completions")
        self.assertEqual(req.get_header("Authorization"), "Bearer gateway-key")
        payload = json.loads(req.data)
        self.assertIn('"sheet_id": "S"', payload["messages"][1]["content"][0]["text"])
        self.assertTrue(result["review_required"])
        self.assertEqual(result["provider"], "compatible")

    def test_error_body_and_transport_exception_never_leak_secrets(self):
        settings = {"provider": "openai", "model": "chosen"}
        failures = [HTTPError("https://example.com?secret-key", 401, "secret-key", {}, io.BytesIO(b"secret-key private drawing")),
                    URLError("secret-key private drawing"), TimeoutError("secret-key private drawing")]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), patch.dict(os.environ, {"OPENAI_API_KEY": "secret-key"}, clear=True), \
                    patch.object(providers, "_open_request", side_effect=failure):
                with self.assertRaises(ProviderError) as caught:
                    ask_provider(settings, "Question", {})
                self.assertNotIn("secret-key", str(caught.exception))
                self.assertNotIn("private drawing", str(caught.exception))

    def test_invalid_and_incomplete_responses_fail_cleanly(self):
        responses = [b"not-json-secret", json.dumps({"error": {"message": "secret"}}).encode(),
                     json.dumps({"status": "incomplete"}).encode(),
                     json.dumps({"output": [{"content": None}]}).encode(), b"[]"]
        for body in responses:
            with self.subTest(body=body), patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True), \
                    patch.object(providers, "_open_request", return_value=io.BytesIO(body)):
                with self.assertRaises(ProviderError) as caught:
                    ask_provider({"provider": "openai", "model": "chosen"}, "Question", {})
                self.assertNotIn("secret", str(caught.exception))

    def test_invalid_image_input_and_missing_config_never_open_network(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(providers, "_open_request") as opened:
            with self.assertRaises(ProviderError):
                extract_provider({"provider": "openai", "model": "chosen"}, b"image", "image/png", 100, 100)
            settings = {"provider": "local", "model": "chosen", "base_url": "http://localhost:1234/v1"}
            for image, mime, width, height in ((b"", "image/png", 100, 100), (b"x", "application/pdf", 100, 100),
                                                (b"x", "image/png", -1, 100), (b"x", "image/png", True, 100)):
                with self.assertRaises(ProviderError):
                    extract_provider(settings, image, mime, width, height)
            self.assertFalse(opened.called)


if __name__ == "__main__":
    unittest.main()
