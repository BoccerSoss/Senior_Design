"""Image-only packets and immutable run evidence."""
from __future__ import annotations
import hashlib
import json
import platform
import subprocess
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]


def stamp():
    # Windows may lack IANA tzdata; compute current US Eastern DST explicitly.
    now = datetime.now(timezone.utc)
    year = now.year
    march = datetime(year, 3, 1, tzinfo=timezone.utc)
    nov = datetime(year, 11, 1, tzinfo=timezone.utc)
    start = march + timedelta(days=(6-march.weekday()) % 7 + 7, hours=7)
    end = nov + timedelta(days=(6-nov.weekday()) % 7, hours=6)
    return now.astimezone(timezone(timedelta(hours=-4 if start <= now < end else -5))).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      object_pairs_hook=unique_object,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"Non-finite JSON value: {v}")))


def image_info(path):
    path = Path(path)
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise ValueError("Unsupported input format. Supply a PNG or JPEG; PDF rendering is not implemented.")
    with Image.open(path) as source:
        if source.format not in {"PNG", "JPEG"}:
            raise ValueError("File extension and image format disagree; use a real PNG/JPEG.")
        if getattr(source, "n_frames", 1) != 1:
            raise ValueError("Animated images are unsupported; supply one static page.")
        if source.width * source.height > 30_000_000:
            raise ValueError("Image exceeds the 30-million-pixel prototype limit.")
        image = ImageOps.exif_transpose(source)
        if "A" in image.getbands() or "transparency" in image.info:
            rgba = image.convert("RGBA")
            white = Image.new("RGBA", rgba.size, "white")
            image = Image.alpha_composite(white, rgba).convert("RGB")
        else:
            image = image.convert("RGB")
        image.load()
        image.info.clear()
    return image


def prepare_input(image_path, packet, prompt_path=None):
    image_path, packet = Path(image_path).resolve(), Path(packet).resolve()
    if packet.exists() and any(packet.iterdir()):
        raise ValueError(f"Packet directory must be new or empty to prevent mixed inputs: {packet}")
    prompt_path = Path(prompt_path or ROOT / "prompts/extract_from_image.txt")
    image = image_info(image_path)
    prompt = prompt_path.read_bytes()
    packet.mkdir(parents=True, exist_ok=True)
    image.save(packet / "image.png", compress_level=9)
    (packet / "prompt.txt").write_bytes(prompt)
    metadata = {
        "packet_version": "1.0.0", "drawing": {
            "drawing_id": "drawing-" + digest(image_path)[:12], "filename": "image.png",
            "sha256": digest(packet / "image.png"), "source_sha256": digest(image_path),
            "source": str(image_path), "page": None, "width": image.width, "height": image.height,
            "coordinate_convention": "top-left pixels, x right/y down, half-open [xmin,ymin,xmax,ymax]",
        },
        "prompt_version": "1.0.0", "prompt_sha256": digest(packet / "prompt.txt"),
        "created_at": stamp(), "timezone": "America/New_York",
        "allowed_files": ["image.png", "prompt.txt"],
        "transform": "EXIF transpose, transparency on white, RGB PNG, metadata stripped; no resize",
    }
    write_json(packet.with_name(packet.name + ".manifest.json"), metadata)
    return metadata


def read_packet(packet):
    packet = Path(packet).resolve()
    metadata = load_json(packet.with_name(packet.name + ".manifest.json"))
    actual = sorted(p.name for p in packet.iterdir())
    if actual != ["image.png", "prompt.txt"]:
        raise ValueError(f"Inference packet must contain only image.png and prompt.txt; found {actual}")
    if any(p.is_symlink() or not p.is_file() for p in packet.iterdir()):
        raise ValueError("Packet files must be ordinary files, not symlinks/directories.")
    if digest(packet / "image.png") != metadata["drawing"]["sha256"]:
        raise ValueError("Packet image hash mismatch; prepare a new packet.")
    if digest(packet / "prompt.txt") != metadata["prompt_sha256"]:
        raise ValueError("Packet prompt hash mismatch; prepare a new packet.")
    image = image_info(packet / "image.png")
    if (image.width, image.height) != (metadata["drawing"]["width"], metadata["drawing"]["height"]):
        raise ValueError("Packet image dimensions do not match its manifest.")
    return metadata


def git_state():
    def git(*args):
        try:
            p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5)
            return p.stdout.strip() if p.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None
    return {"revision": git("rev-parse", "HEAD"), "dirty_state": git("status", "--porcelain"),
            "note": "No commit yet" if git("rev-parse", "HEAD") is None else None}


def new_run(runs_dir, method, provider, model=None, packet=None, stage="original"):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    path = Path(runs_dir).resolve() / run_id
    path.mkdir(parents=True, exist_ok=False)
    run = {"run_id": run_id, "timestamp": stamp(), "timezone": "America/New_York",
           "method": method, "provider": provider, "model": model, "access_route": method,
           "settings": {}, "manual_stages": [], "duration_seconds": None, "cost": None,
           "raw_response_location": "raw_response.txt", "stage": stage,
           "code": git_state(), "python_version": platform.python_version(),
           "status": "started", "packet": str(Path(packet).resolve()) if packet else None}
    write_json(path / "run.json", run)
    return path, run


def parse_response(path):
    text = Path(path).read_text(encoding="utf-8-sig").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].lower() not in {"```", "```json"} or lines[-1] != "```":
            raise ValueError("Response code fence is malformed; save one complete JSON object.")
        text = "\n".join(lines[1:-1])
    return json.loads(text, object_pairs_hook=unique_object,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f"Non-finite JSON: {v}")))
