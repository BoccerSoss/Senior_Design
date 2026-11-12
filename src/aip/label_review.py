"""Build a label-only image packet and validate a vision model's transcription."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .core import ValidationError, normalize_tag
from .io import image_info, parse_response, write_json

PROMPT = """Read the equipment label printed in each image cell. The small symbol ID above
each crop identifies the cell. Read the text below that ID from the drawing pixels.
Copy punctuation, spaces, numbers, and letters as printed. Use null if a label
cannot be read confidently. Do not complete a label from neighboring tags or guess
what an acronym means. Read each cell, including cells with text on two lines.

Return a single JSON object with this shape:
{"drawing_key": "COPY THE DRAWING KEY", "labels": [{"id": "symbol-01", "text": "TEXT FROM IMAGE"}]}

Use a JSON null without quotation marks for unreadable text. Include each printed
symbol ID exactly once, with no Markdown or surrounding explanation.
"""


def make_label_packet(image_path, geometry, directory):
    """Create a high-resolution crop sheet with IDs and an image-only prompt."""
    directory = Path(directory)
    image = image_info(image_path)
    labels = [item for item in geometry["observations"]["components"] if item.get("tag_bbox")]
    if not labels:
        raise ValidationError("This drawing has no printed label regions to send for transcription.")
    directory.mkdir(parents=True, exist_ok=False)
    columns, cell_width, cell_height = 3, 520, 144
    sheet = Image.new("RGB", (columns * cell_width, ((len(labels) + 2) // 3) * cell_height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=19)
    key = geometry["image"]["sha256"][:16]
    for index, item in enumerate(labels):
        x, y = index % columns * cell_width, index // columns * cell_height
        draw.rectangle((x, y, x + cell_width - 1, y + cell_height - 1), outline="#D0DAE1")
        draw.text((x + 12, y + 8), item["id"], fill="#245678", font=font)
        crop = image.crop(item["tag_bbox"])
        scale = min((cell_width - 28) / crop.width, (cell_height - 46) / crop.height)
        size = tuple(max(1, round(length * scale)) for length in crop.size)
        sheet.paste(crop.resize(size, Image.Resampling.BICUBIC), (x + 14, y + 37))
    sheet.save(directory / "label-crops.png")
    (directory / "prompt.txt").write_text(f"Drawing key: {key}\n\n{PROMPT}", encoding="utf-8")
    return directory


def read_label_review(path, label_ids, drawing_key):
    """Read one transcription for each requested crop and reject ID substitutions."""
    payload = parse_response(Path(path))
    if (not isinstance(payload, dict) or set(payload) != {"drawing_key", "labels"}
            or not isinstance(payload["labels"], list)):
        raise ValidationError('Label response must contain "drawing_key" and a "labels" array.')
    if payload["drawing_key"] != drawing_key:
        raise ValidationError("Label response is for a different drawing. Open the drawing used to create the crops.")
    expected = set(label_ids)
    result = {}
    for item in payload["labels"]:
        if not isinstance(item, dict) or set(item) != {"id", "text"}:
            raise ValidationError("Each label must have exactly an id and text.")
        ident, value = item["id"], item["text"]
        if not isinstance(ident, str) or ident not in expected or ident in result:
            raise ValidationError(f"Unknown or duplicate label ID {ident!r}.")
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 100):
            raise ValidationError(f"Label {ident} must be short printed text or null.")
        reading = " ".join(value.split()) if value is not None else None
        result[ident] = reading if normalize_tag(reading) is not None else None
    if set(result) != expected:
        raise ValidationError("The response must include each printed label ID exactly once.")
    return result
