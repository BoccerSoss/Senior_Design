import itertools
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from aip import ctc_ocr

try:
    import numpy as np
except ImportError:
    np = None


def record(ident, alternatives=("QK-42", "QK-43"), box=(1, 2, 13, 8)):
    return {"component_id": ident, "source_word_bbox": list(box),
            "repeated_prefix_alternatives": [{"text": text} for text in alternatives]}


class LocalRecognizerTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new("L", (40, 20))

    @unittest.skipIf(np is None, "numeric OCR tests use NumPy")
    def test_ctc_sums_alignments_and_keeps_repeated_letters_separate(self):
        emissions = {"": np.array([0.4, 0.8, 0.7, 0.2]), "A": np.array([0.6, 0.2, 0.3, 0.8])}
        for expected_text in ("A", "AA"):
            probability = 0.0
            for path in itertools.product(emissions, repeat=4):
                collapsed = "".join(symbol for symbol, _ in itertools.groupby(path))
                if collapsed == expected_text:
                    probability += math.prod(emissions[symbol][t] for t, symbol in enumerate(path))
            self.assertAlmostEqual(ctc_ocr._ctc_log_likelihood(np, expected_text, emissions), math.log(probability))

    def test_model_request_is_lazy_and_optional(self):
        components = [{"id": "shape", "tag": None}]
        with tempfile.TemporaryDirectory() as directory:
            blob = Path(directory) / "fake.onnx"
            with patch.object(ctc_ocr, "_load_model") as load:
                for candidate in (blob, Path(directory)):
                    self.assertEqual(ctc_ocr.supplement_blank_tags(
                        self.image, components, [record("shape")], model_path=candidate), 0)
                blob.write_bytes(b"fake")
                self.assertEqual(ctc_ocr.supplement_blank_tags(
                    self.image, components, [record("shape")], model_path=blob), 0)
                with patch.object(ctc_ocr, "MODEL_BYTES", 4):
                    self.assertEqual(ctc_ocr.supplement_blank_tags(
                        self.image, components, [record("shape", alternatives=("QK-42",))], model_path=blob), 0)
                load.assert_not_called()
                load.side_effect = ImportError("optional runtime missing")
                with patch.object(ctc_ocr, "MODEL_BYTES", 4):
                    self.assertEqual(ctc_ocr.supplement_blank_tags(
                        self.image, components, [record("shape")], model_path=blob), 0)
        self.assertEqual(components, [{"id": "shape", "tag": None}])

    def test_reject_unrecognized_model_before_importing_native_libraries(self):
        with tempfile.TemporaryDirectory() as directory:
            blob = Path(directory) / ctc_ocr.MODEL_FILENAME
            blob.write_bytes(b"untrusted file")
            with patch("builtins.__import__", side_effect=AssertionError("unverified model triggered runtime import")):
                self.assertIsNone(ctc_ocr._load_model(blob, blob.stat().st_mtime_ns))

    def test_fill_only_unique_blank_and_preserve_provenance(self):
        components = [{"id": "shown", "tag": "qk–45"},
                      {"id": "fill", "tag": None, "evidence": "Visible outline."}]
        components.extend({"id": ident, "tag": None}
                          for ident in ("occupied", "equal1", "equal2", "twice", "badbox", "ambiguous"))
        records = [record("shown"), record("fill"), record("occupied", ("QK-45", "QK-46")),
                   record("equal1", ("QK-51", "QK-57")), record("equal2", ("QK-51", "QK-58")),
                   record("twice"), record("twice"), record("badbox", box=(1, 1, 55, 10)),
                   record("ambiguous", ("qk–65", "QK-65", "QK-66"))]
        with tempfile.TemporaryDirectory() as directory:
            blob = Path(directory) / "fake.onnx"
            blob.write_bytes(b"fake")
            with patch.object(ctc_ocr, "MODEL_BYTES", 4), patch.object(ctc_ocr, "_load_model", return_value=True), \
                    patch.object(ctc_ocr, "_read_crop", side_effect=lambda model, img, box, tags: (tags[0], 4.0)) as read:
                self.assertEqual(ctc_ocr.supplement_blank_tags(self.image, components, records, model_path=blob), 1)
                self.assertEqual(read.call_count, 4)
        self.assertEqual([c["tag"] for c in components], ["qk–45", "QK-42"] + [None] * 6)
        self.assertEqual(components[1]["tag_bbox"], [1, 2, 13, 8])
        self.assertEqual(components[1]["ocr_supplement"]["candidates"], ["QK-42", "QK-43"])
        self.assertIn("Visible outline.", components[1]["evidence"])
        self.assertIn("PP-OCRv6", components[1]["evidence"])
        self.assertTrue(all("tag_bbox" not in c for i, c in enumerate(components) if i != 1))

    @unittest.skipIf(np is None, "numeric OCR tests use NumPy")
    def test_only_finite_margin_of_at_least_three_passes(self):
        session = Mock()
        session.run.return_value = [np.full((1, 10, 3), 1 / 3)]
        model = (np, session, "image", {"A": [1], "B": [2]})
        for lead, expected in ((0, None), (2.99, None), (3.0, ("A", 3.0)), (math.inf, None)):
            with patch.object(ctc_ocr, "_ctc_log_likelihood", side_effect=[-2.0, -2.0 - lead]):
                self.assertEqual(ctc_ocr._read_crop(model, self.image, (0, 0, 20, 8), ["A", "B"]), expected)


if __name__ == "__main__":
    unittest.main()
