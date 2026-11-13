"""Fresh-process inference entry: only a packet directory is accepted."""
import json
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aip.baseline import extract_image
from aip.boundary import install_read_boundary

packet = Path(sys.argv[1]).resolve()
method = sys.argv[2] if len(sys.argv)>2 else "baseline"
if method == "classical":
    from aip.classical import extract_image as classical_extract
    scratch = Path(sys.argv[3]).resolve()
    install_read_boundary(packet,scratch)
    result = classical_extract(packet / "image.png",scratch)
elif method == "baseline":
    install_read_boundary(packet)
    result = extract_image(packet / "image.png")
else:
    raise ValueError("Worker method must be baseline or classical")
print(json.dumps(result, allow_nan=False))
