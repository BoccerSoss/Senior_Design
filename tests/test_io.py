"""Behavior tests for the image-only packet and fresh-process input boundary."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw, PngImagePlugin

from aip.io import image_info, parse_response, prepare_input, read_packet


ROOT = Path(__file__).resolve().parents[1]


class PacketIOTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.prompt = self.root / "generic.txt"
        self.prompt.write_text("Interpret visible equipment and connections; preserve uncertainty.", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def image(self, name="source.png"):
        image = Image.new("RGB", (700, 300), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 90, 170, 210), outline="black", width=4)
        draw.ellipse((450, 100, 550, 200), outline="black", width=4)
        draw.line((170, 150, 450, 150), fill="black", width=4)
        path = self.root / name
        image.save(path)
        return path

    def packet(self, source=None):
        path = self.root / "packet"
        prepare_input(source or self.image(), path, self.prompt)
        return path

    def test_packet_has_exactly_image_and_generic_prompt(self):
        packet = self.packet()
        self.assertEqual(sorted(p.name for p in packet.iterdir()), ["image.png", "prompt.txt"])
        metadata = read_packet(packet)
        self.assertEqual(metadata["drawing"]["width"], 700)
        self.assertEqual(metadata["drawing"]["height"], 300)
        self.assertEqual((packet / "prompt.txt").read_bytes(), self.prompt.read_bytes())
        self.assertFalse(any("truth" in p.name or "manifest" in p.name for p in packet.iterdir()))
        (packet / "truth.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "only image.png and prompt.txt"):
            read_packet(packet)

    def test_image_and_prompt_tampering_are_rejected(self):
        for filename in ("image.png", "prompt.txt"):
            with self.subTest(filename=filename):
                folder = self.root / filename.replace(".", "-")
                prepare_input(self.image(), folder, self.prompt)
                path = folder / filename
                path.write_bytes(path.read_bytes() + b"tampered")
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    read_packet(folder)

    def test_png_transparency_on_white_and_answer_metadata_removed(self):
        source = self.root / "transparent.png"
        image = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
        image.putpixel((60, 40), (0, 0, 0, 255))
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("expected-answer", "POISON-TOPOLOGY")
        exif = Image.Exif()
        exif[270] = "POISON-EXIF"
        image.save(source, pnginfo=metadata, exif=exif)
        packet = self.packet(source)
        with Image.open(packet / "image.png") as prepared:
            self.assertEqual(prepared.mode, "RGB")
            self.assertEqual(prepared.getpixel((0, 0)), (255, 255, 255))
            self.assertEqual(prepared.getpixel((60, 40)), (0, 0, 0))
            self.assertEqual(prepared.info, {})

    def test_jpeg_exif_orientation_applied_and_metadata_removed(self):
        source = self.root / "oriented.jpg"
        image = Image.new("RGB", (120, 80), "white")
        exif = Image.Exif()
        exif[274] = 6  # Rotate 90 degrees clockwise for display.
        exif[270] = "POISON-EXIF"
        image.save(source, exif=exif, quality=95)
        packet = self.packet(source)
        with Image.open(packet / "image.png") as prepared:
            self.assertEqual(prepared.size, (80, 120))
            self.assertEqual(prepared.info, {})
        self.assertEqual(read_packet(packet)["drawing"]["width"], 80)

    def test_color_profile_metadata_removed(self):
        source = self.root / "profiled.png"
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        Image.new("RGB", (120, 80), "white").save(source, icc_profile=profile)
        packet = self.packet(source)
        with Image.open(packet / "image.png") as prepared:
            self.assertEqual(prepared.info, {})

    def test_unsupported_and_animated_input_are_explicit(self):
        gif = self.root / "drawing.gif"
        Image.new("RGB", (100, 100), "white").save(gif)
        with self.assertRaisesRegex(ValueError, "PNG or JPEG"):
            image_info(gif)
        animated = self.root / "animated.png"
        first = Image.new("RGB", (100, 100), "white")
        first.save(animated, save_all=True, append_images=[Image.new("RGB", first.size, "black")], duration=100)
        with self.assertRaisesRegex(ValueError, "Animated"):
            image_info(animated)

    def test_fresh_worker_consumes_real_packet_image(self):
        packet = self.packet()
        read_packet(packet)
        result = subprocess.run(
            [sys.executable, "-s", str(ROOT / "scripts/baseline_worker.py"), str(packet)],
            cwd=self.root, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        raw = json.loads(result.stdout)
        self.assertEqual(raw["image"], {"width": 700, "height": 300})
        self.assertEqual(len(raw["connections"]), 1)
        self.assertTrue(all(c["observed_tag"] is None for c in raw["components"]))

    def test_read_boundary_denies_truth_and_packet_writes_in_subprocess(self):
        packet = self.packet()
        truth = self.root / "truth.json"
        truth.write_text('{"tag":"POISON-TAG"}', encoding="utf-8")
        script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from aip.boundary import install_read_boundary
packet, truth = Path(sys.argv[2]), Path(sys.argv[3])
install_read_boundary(packet)
assert (packet/'image.png').read_bytes().startswith(b'\\x89PNG')
for target, mode in [(truth, 'r'), (packet/'unexpected.txt', 'w')]:
    try:
        with open(target, mode):
            pass
    except PermissionError:
        print('DENIED')
    else:
        raise AssertionError('Forbidden open was permitted')
"""
        result = subprocess.run(
            [sys.executable, "-s", "-c", script, str(ROOT / "src"), str(packet), str(truth)],
            cwd=self.root, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["DENIED", "DENIED"])
        self.assertFalse((packet / "unexpected.txt").exists())

    def test_malformed_response_remains_unchanged_after_parser_rejection(self):
        source = self.root / "malformed-response.txt"
        original = b'```json\n{"components": [BROKEN}\n```\n'
        source.write_bytes(original)
        with self.assertRaises(ValueError):
            parse_response(source)
        self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
