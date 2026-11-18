"""Prepare focused drawing crops for an explicitly selected ChatGPT trace."""
from base64 import b64encode
from collections import Counter
from io import BytesIO
from json import JSONDecodeError, loads
from math import ceil
import re

from PIL import Image, ImageDraw, ImageFont

from ..core import normalize_tag


INSTRUCTIONS = (
    "Transcribe engineering drawing equipment names verbatim. Return one valid JSON object, no markdown. "
    "Use null for uncertain or absent labels. Do not reconstruct a name from any pattern or assign it to a nearby non-target symbol."
)
REQUEST = (
    "Each tile is an independent raster crop with the target symbol outlined in red. Black text in the tile header is only "
    "the entry ID. Read the exact black text printed next to the outlined equipment, distinguishing it from nearby equipment "
    "or pipe callouts. Exact digits, prefixes and suffixes matter. Return only keys from the visible headers and the "
    "printed label or null as the value."
)
_COLUMNS, _WIDTH, _HEIGHT = 3, 365, 258
_PAGE_SIZE = 15


def _crop_sheet(image, components):
    contact = Image.new("RGB", (_WIDTH * _COLUMNS, _HEIGHT * ceil(len(components) / _COLUMNS)), "white")
    font = ImageFont.load_default(size=16)
    for index, component in enumerate(components):
        left, top, right, bottom = map(round, component["bbox"])
        x0 = (left + right) // 2 - (_WIDTH - 14) // 2
        y0 = (top + bottom) // 2 - (_HEIGHT - 35) // 2
        cell = Image.new("RGB", (_WIDTH, _HEIGHT), "white")
        bounds = (max(0, x0), max(0, y0), min(image.width, x0 + _WIDTH - 14), min(image.height, y0 + _HEIGHT - 35))
        cell.paste(image.crop(bounds), (7 + max(0, -x0), 27 + max(0, -y0)))
        drawer = ImageDraw.Draw(cell)
        drawer.rectangle((left - x0 + 5, top - y0 + 25, right - x0 + 9, bottom - y0 + 29),
                         outline="#E13432", width=2)
        drawer.rectangle((0, 0, _WIDTH - 1, _HEIGHT - 1), outline="#777777")
        drawer.text((8, 5), component["id"], fill="black", font=font)
        contact.paste(cell, ((index % _COLUMNS) * _WIDTH, (index // _COLUMNS) * _HEIGHT))
    stream = BytesIO()
    contact.save(stream, format="PNG")
    return "data:image/png;base64," + b64encode(stream.getvalue()).decode("ascii")


def _tag_key(name):
    return re.sub(r"[^A-Z0-9]", "", normalize_tag(name) or "")


def _response_tags(response):
    response = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip(), flags=re.IGNORECASE)
    try:
        result = loads(response)
    except JSONDecodeError:
        return {}
    return result if isinstance(result, dict) else {}


def read_crop_tags(image, components, generate):
    """Fill unnamed typed symbols with distinct, source-crop transcriptions."""
    blanks = [item for item in components if not item.get("tag") and item.get("type") not in {None, "", "unknown", "junction"}]
    existing = {_tag_key(item.get("tag")) for item in components}
    readings = {}
    for start in range(0, len(blanks), _PAGE_SIZE):
        batch = blanks[start:start + _PAGE_SIZE]
        result = _response_tags(generate(INSTRUCTIONS, REQUEST, _crop_sheet(image, batch)))
        for item in batch:
            name = result.get(item["id"])
            if isinstance(name, str) and 0 < len(name.strip()) <= 40 and name.isprintable() and _tag_key(name):
                readings[item["id"]] = name.strip()
    counts = Counter(_tag_key(name) for name in readings.values())
    applied = 0
    for item in blanks:
        name = readings.get(item["id"])
        key = _tag_key(name)
        if key and key not in existing and counts[key] == 1:
            item["tag"] = name
            item["evidence"] = ((item.get("evidence") or "") +
                                " ChatGPT transcribed the printed label from a crop of this symbol.").strip()
            item["label_source"] = "chatgpt_crop"
            item["uncertainty"] = "Review this label against the visible drawing."
            applied += 1
    return applied
