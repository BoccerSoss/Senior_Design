"""Install the pinned OCR weights into the active project's virtual environment."""
from hashlib import sha256
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aip.ctc_ocr import MODEL_BYTES, MODEL_SHA256, default_model_path


MODEL_URL = ("https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/"
             "onnx/PP-OCRv6/rec/PP-OCRv6_rec_small.onnx")


def main():
    target = default_model_path()
    if sys.prefix == sys.base_prefix:
        print("Set up the OCR model with the project's virtual environment.", file=sys.stderr)
        return 1
    staged = None
    try:
        if target.is_file() and target.stat().st_size == MODEL_BYTES and sha256(target.read_bytes()).hexdigest() == MODEL_SHA256:
            return 0
        print("Downloading the local equipment-label model (21 MB)...", flush=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        request = Request(MODEL_URL, headers={"User-Agent": "AIP-workbench/3"})
        with urlopen(request, timeout=20) as response:
            payload = response.read(MODEL_BYTES + 1)
        if len(payload) != MODEL_BYTES or sha256(payload).hexdigest() != MODEL_SHA256:
            print("The OCR download did not match the expected model.", file=sys.stderr)
            return 1
        with NamedTemporaryFile(dir=target.parent, suffix=".onnx.download", delete=False) as stream:
            staged = Path(stream.name)
            stream.write(payload)
        staged.replace(target)
        return 0
    except OSError as error:
        print(f"The local OCR model could not be installed ({type(error).__name__}).", file=sys.stderr)
        return 1
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
