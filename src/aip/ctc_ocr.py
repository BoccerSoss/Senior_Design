"""Reread unresolved, image-derived equipment tag candidates with a local CTC recognizer."""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
from hashlib import sha256
import logging
import math
from pathlib import Path
import re
import sys

from PIL import Image, ImageOps

from .core import normalize_tag


MODEL_FILENAME = "pp-ocrv6-small-rec.onnx"
MODEL_SHA256 = "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"
MODEL_BYTES = 21_234_383
MIN_LOG_MARGIN = 3.0
_TAG = re.compile(r"[A-Z]{1,4}-[0-9]{2,7}(?:[A-Z](?:/[A-Z])?)?")
_LOG = logging.getLogger(__name__)


def default_model_path() -> Path:
    return Path(sys.prefix) / "aip-models" / MODEL_FILENAME


@lru_cache(maxsize=2)
def _load_model(path, modified_ns):
    # The timestamp is part of the cache key so replacing the model triggers verification.
    model = path.read_bytes()
    if sha256(model).hexdigest() != MODEL_SHA256:
        return None

    import numpy as np
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    session = ort.InferenceSession(model, opts, providers=["CPUExecutionProvider"])
    # The model embeds the character table used to train this particular decoder.
    chars = [""] + session.get_modelmeta().custom_metadata_map["character"].splitlines()
    indices = {}
    for i, char in enumerate(chars[1:], 1):
        key = char.upper()
        if len(key) == 1 and key in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/":
            indices.setdefault(key, []).append(i)
    return np, session, session.get_inputs()[0].name, indices


def _ctc_log_likelihood(np, text, emissions):
    """Sum over alignments with optional blank frames, including adjacent identical letters."""
    states = [""]
    for char in text:
        states.extend((char, ""))
    emissions = np.stack([emissions[char] for char in states], axis=1)
    can_skip = [j for j in range(3, len(states), 2) if states[j] != states[j - 2]]
    scores = np.zeros(len(states), dtype=np.float64)
    scores[0] = 1.0
    accumulated = 0.0
    for frame in emissions:
        next_scores = scores.copy()
        next_scores[1:] += scores[:-1]
        for j in can_skip:
            next_scores[j] += scores[j - 2]
        next_scores *= frame
        maximum = float(next_scores.max())
        if maximum <= 0 or not math.isfinite(maximum):
            return -math.inf
        scores = next_scores / maximum
        accumulated += math.log(maximum)
    end = float(scores[-2:].sum())
    return accumulated + math.log(end) if end > 0 else -math.inf


def _read_crop(model, image, box, candidates):
    np, session, input_name, indices = model
    chars = set("".join(candidates))
    if not chars.issubset(indices):
        return None
    crop = ImageOps.expand(image.crop(box).convert("L"), (5, 3, 5, 3), fill=255)
    ratio = crop.width / crop.height
    width = int(48 * max(320 / 48, ratio))
    if width > 2048 or crop.getextrema()[0] >= 160:
        return None
    used = min(width, math.ceil(48 * ratio))
    resized = crop.resize((used, 48), Image.Resampling.BILINEAR)
    inputs = np.zeros((1, 3, 48, width), dtype=np.float32)
    inputs[:, :, :, :used] = np.asarray(resized, dtype=np.float32) / 127.5 - 1.0
    frames = session.run(None, {input_name: inputs})[0][0]
    emissions = {c: frames[:, indices[c]].sum(axis=1) for c in chars}
    emissions[""] = frames[:, 0]
    ranked = sorted(((_ctc_log_likelihood(np, tag, emissions), tag) for tag in candidates), reverse=True)
    lead = ranked[0][0] - ranked[1][0]
    if math.isfinite(lead) and lead >= MIN_LOG_MARGIN:
        return ranked[0][1], lead
    return None


def supplement_blank_tags(image: Image.Image, components: list, records: list, *, model_path=None) -> int:
    """Fill blank V3 tags only when the pinned local model distinguishes their raster glyph candidates."""
    ids = Counter(c.get("id") for c in components)
    blanks = {c["id"]: c for c in components if isinstance(c.get("id"), str) and ids[c["id"]] == 1
              and not (c.get("tag") or "").strip()}
    candidates = []
    record_ids = Counter(r.get("component_id") for r in records)
    for record in records:
        ident = record.get("component_id")
        if ident not in blanks or record_ids[ident] != 1:
            continue
        box = record.get("source_word_bbox")
        if (not isinstance(box, (list, tuple)) or len(box) != 4 or
                not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box) or
                not (0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height)):
            continue
        spellings = []
        for reading in record.get("repeated_prefix_alternatives") or []:
            if isinstance(reading, dict) and isinstance(reading.get("text"), str):
                tag = normalize_tag(reading["text"])
                if tag and _TAG.fullmatch(tag):
                    spellings.append(tag)
        if len(spellings) >= 2 and len(set(spellings)) == len(spellings):
            candidates.append((ident, box, sorted(spellings)))
    if not candidates:
        return 0

    path = Path(model_path) if model_path is not None else default_model_path()
    try:
        info = path.stat()
        if info.st_size != MODEL_BYTES:
            return 0
        model = _load_model(path.resolve(), info.st_mtime_ns)
        if model is None:
            return 0
        proposals = []
        for ident, box, spellings in candidates:
            result = _read_crop(model, image, box, spellings)
            if result:
                tag, lead = result
                proposals.append((ident, box, spellings, tag, lead))
    except Exception:
        # OCR is supplemental. Missing wheels and native-runtime failures must not discard the base trace.
        _LOG.debug("The optional local OCR recognizer was unavailable.", exc_info=True)
        return 0

    used = {normalize_tag(c["tag"]) for c in components if c.get("tag")}
    counts = Counter(proposal[3] for proposal in proposals)
    filled = 0
    for ident, box, spellings, tag, lead in proposals:
        if tag in used or counts[tag] != 1:
            continue
        item = blanks[ident]
        item.update(tag=tag, tag_bbox=list(box), ocr_supplement={
            "model": "PP-OCRv6 small recognition", "model_sha256": MODEL_SHA256,
            "candidate_source": "ink_label_readings.repeated_prefix_alternatives",
            "candidates": spellings, "log_likelihood_margin": round(lead, 4)})
        detail = (f"The local PP-OCRv6 small model reread this printed label and favored {tag} "
                  f"among {len(spellings)} spellings obtained from its letter and number shapes.")
        item["evidence"] = " ".join(part for part in (item.get("evidence"), detail) if part)
        filled += 1
    return filled
