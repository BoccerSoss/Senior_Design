import tempfile
import unittest
from pathlib import Path
from aip.io import load_json, parse_response


class JSONIntegrityTests(unittest.TestCase):
    def test_duplicate_keys_cannot_hide_conflicting_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "response.json"
            original = b'{"components": [], "components": [{"id":"hidden"}]}'
            path.write_bytes(original)
            for parser in (load_json, parse_response):
                with self.subTest(parser=parser.__name__):
                    with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
                        parser(path)
                    self.assertEqual(path.read_bytes(), original)
