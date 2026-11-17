"""Read touching glyphs in a cropped equipment tag using ordinary font shapes."""
from __future__ import annotations

from functools import lru_cache
from itertools import product
from string import ascii_uppercase, digits

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ALPHABETS = {"letter": ascii_uppercase, "digit": digits, "separator": "-", "slash": "/"}
_MAX_WIDTH = 245


def _bits(image):
    return int.from_bytes(image.convert("1", dither=Image.Dither.NONE).tobytes(), "big")


@lru_cache(maxsize=1)
def _shapes():
    chars = ascii_uppercase + digits + "-/"
    shapes = {char: [] for char in chars}
    for filename in ("arial.ttf", "arialbd.ttf", "ARIALNB.TTF", None):
        try:
            font = ImageFont.truetype(filename, 40) if filename else ImageFont.load_default(size=40)
        except OSError:
            continue
        for char in chars:
            image = Image.new("L", (65, 65))
            ImageDraw.Draw(image).text((10, 10), char, font=font, fill=255)
            template = image.crop(image.getbbox())
            if char == "-":
                size = (21, 6)
            elif char == "/":
                size = (13, 32)
            else:
                size = (max(1, round(template.width * 32 / template.height)), 32)
            template = template.resize(size).point(lambda value: 255 if value > 80 else 0)
            expanded = Image.new("L", (template.width + 4, template.height + 4))
            expanded.paste(template, (2, 2))
            thick = expanded.filter(ImageFilter.MaxFilter(5))
            for shape in (template, thick.crop(thick.getbbox())):
                shapes[char].append((shape.width / shape.height, shape))
    return shapes


@lru_cache(maxsize=1024)
def _template_bits(chars, width, height):
    shapes = _shapes()
    aspect = width / height
    per_character = []
    for char in chars:
        templates = []
        for ratio, template in shapes[char]:
            size_penalty = abs(width - 24) * 0.005 if char == "-" else 0.28 * abs(aspect - ratio)
            templates.append((_bits(template.resize((width, height), Image.Resampling.BILINEAR)), size_penalty))
        per_character.append((char, templates))
    return per_character


def _segments(image, start, mode, projection, letter=None):
    width, height = image.size
    choices = []
    chars = letter if letter is not None else ALPHABETS[mode]
    spans = {"letter": range(3, 35), "digit": range(3, 35), "separator": range(11, 34), "slash": range(8, 21)}
    for span in spans[mode]:
        end = start + span
        if end > width:
            break
        patch = image.crop((start, 0, end, height))
        bounds = patch.getbbox()
        if bounds is None:
            continue
        _, top, _, bottom = bounds
        glyph_height = bottom - top
        if mode == "separator":
            if not (3 <= glyph_height <= 13 and 8 <= top <= 22):
                continue
        elif glyph_height < 23:
            continue
        observed = _bits(patch.crop((0, top, span, bottom)))
        pixel_count = span * glyph_height
        cut = min(projection[end - 1], projection[end]) * 0.125 if end < width else 0
        scores = []
        for char, templates in _template_bits(chars, span, glyph_height):
            score = min((observed ^ shape).bit_count() / pixel_count + penalty for shape, penalty in templates)
            scores.append((score, char))
        choices.extend((score + cut, end, char) for score, char in sorted(scores)[:4])
    return sorted(choices)[:24]


def _tag_shapes(width):
    endings = ((), ("letter",), ("letter", "slash", "letter"))
    for prefix_length, digit_length, suffix in product(range(1, 4), range(2, 6), endings):
        pattern = ("letter",) * prefix_length + ("separator",) + ("digit",) * digit_length + suffix
        if 13 * len(pattern) - 12 <= width <= 33 * len(pattern):
            complexity = 0.017 * prefix_length + 0.017 * max(0, digit_length - 3)
            if not suffix:
                complexity += 0.008
            elif len(suffix) > 1:
                complexity += 0.013
            yield pattern, complexity


def rank_tag_glyphs(crop, ocr_suffixes=(), *, prefix=None):
    """Return alternative readings and mismatch scores for a numeric tag crop.

    The search considers short prefixes and numeric IDs with optional letter
    suffixes. Letterforms come from system fonts, and a cut across heavy ink
    costs more than one through a gap. No drawing IDs or expected tags enter
    the search.
    """
    ink = crop.point(lambda value: 255 if value < 160 else 0)
    bounds = ink.getbbox()
    if bounds is None:
        return []
    tight = ink.crop(bounds)
    width = max(1, round(tight.width * 32 / tight.height))
    if not 28 <= width <= _MAX_WIDTH:
        return []
    image = tight.resize((width, 32)).point(lambda value: 255 if value > 120 else 0)
    projection = [value / 255 for value in image.resize((width, 1), Image.Resampling.BOX).tobytes()]

    @lru_cache(maxsize=None)
    def choices(start, mode, letter):
        return _segments(image, start, mode, projection, letter)

    results = {}
    # Numeric tag patterns share their initial character classes. The same
    # glyphs and search costs give the same beam at that shared prefix.
    beams = {(): [(0, 0, "")]}
    for pattern, complexity in _tag_shapes(width):
        prefix_length = pattern.index("separator")
        if prefix is not None and prefix_length != len(prefix):
            continue
        beam = beams[()]
        for character_index, mode in enumerate(pattern):
            partial = pattern[:character_index + 1]
            if partial in beams:
                beam = beams[partial]
                continue
            letter = prefix[character_index] if prefix is not None and character_index < prefix_length else None
            best = {}
            for cost, position, text in beam:
                for start in range(position, min(width, position + 4)):
                    skipped = sum(projection[position:start])
                    if skipped > 1:
                        break
                    for score, end, char in choices(start, mode, letter):
                        key = end, text + char
                        total = cost + score + skipped * 0.23
                        if total < best.get(key, float("inf")):
                            best[key] = total
            beam = sorted((cost, end, text) for (end, text), cost in best.items())[:12]
            beams[partial] = beam
            if not beam:
                break
        for cost, end, text in beam:
            remaining = sum(projection[end:])
            if width - end >= 5 or remaining > 1:
                continue
            score = (cost + remaining * 0.23) / len(pattern) + complexity
            if text.endswith(("I", "J")):
                score += 0.025
            if text.partition("-")[2] in ocr_suffixes:
                score -= 0.018
            if score < results.get(text, float("inf")):
                results[text] = score
    return [{"text": text, "mismatch": round(score, 5)} for score, text in
            sorted((score, text) for text, score in results.items())[:6]]
