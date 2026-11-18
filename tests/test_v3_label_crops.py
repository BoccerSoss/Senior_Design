import base64
from io import BytesIO
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from aip.review import review_queue
from aip.v3.label_crops import read_crop_tags
from aip.v3.vision import extract_connections


class CropLabelTests(unittest.TestCase):
    def test_crop_requests_reread_typed_blanks_and_preserve_local_labels(self):
        items = [
            {"id": "a", "tag": "P-100", "type": "pump", "bbox": [11, 12, 25, 28]},
            {"id": "b", "tag": "", "type": "valve", "bbox": [60, 50, 75, 66]},
            {"id": "c", "tag": "", "type": "pump", "bbox": [130, 50, 153, 72]},
            {"id": "d", "tag": "", "type": "unknown", "bbox": [88, 5, 100, 20]},
        ]
        generate = Mock(return_value='```json\n{"a":"P-999","b":"V-101","c":"p100","d":"P-500"}\n```')
        image = Image.new("RGB", (200, 100), "white")
        self.assertEqual(read_crop_tags(image, items, generate), 1)
        self.assertEqual([c["tag"] for c in items], ["P-100", "V-101", "", ""])
        self.assertEqual(items[1]["label_source"], "chatgpt_crop")
        image_url = generate.call_args.args[2]
        self.assertTrue(image_url.startswith("data:image/png;base64,"))
        with Image.open(BytesIO(base64.b64decode(image_url.split(",", 1)[1]))) as crop:
            self.assertEqual(crop.format, "PNG")
            self.assertIn((225, 52, 50), {rgb for _, rgb in crop.getcolors(65536)})
        reasons = {item["id"]: item["reason"] for item in review_queue({"components": items})}
        self.assertEqual(reasons["b"], "Check name transcribed from a drawing crop")

    def test_collisions_within_model_response_leave_both_labels_blank(self):
        items = [{"id": key, "tag": "", "type": "valve", "bbox": [10, 10, 30, 30]} for key in ("a", "b")]
        for response in ('{"a":"V-101","b":"v101"}', 'bad result'):
            image = Image.new("RGB", (100, 100), "white")
            self.assertEqual(read_crop_tags(image, items, Mock(return_value=response)), 0)
            self.assertTrue(all(item["tag"] == "" for item in items))

    def test_focus_mode_keeps_local_routes_and_sets_provider_provenance(self):
        image = Image.new("RGB", (120, 120), "white")
        stream = BytesIO()
        image.save(stream, format="PNG")
        local = {"image": {}, "connections": [{"source": "a", "target": "b"}], "components": [
            {"id": "a", "tag": "", "type": "pump", "bbox": [10, 10, 30, 30]},
            {"id": "b", "tag": "V-101", "type": "valve", "bbox": [40, 10, 55, 30]},
        ]}
        settings = {"provider": "chatgpt-plan", "model": "plan-model", "focus_labels": True,
                    "_plan_generate": Mock(return_value='{"a":"P-102"}')}
        with patch("aip.v3.vision.extract_local_connections", return_value=local):
            trace = extract_connections(settings, stream.getvalue(), "image/png", 120, 120)
        self.assertEqual(trace["connections"], [{"source": "a", "target": "b"}])
        self.assertEqual(trace["provider"], "chatgpt-plan")
        self.assertEqual(trace["hosted_labels"], 1)
        self.assertEqual(trace["components"][0]["tag"], "P-102")
