"""Fresh-process inference entry: only a packet directory is accepted."""
import json
import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aip.baseline import extract_image
from aip.boundary import install_read_boundary

packet = Path(sys.argv[1]).resolve()
install_read_boundary(packet)
print(json.dumps(extract_image(packet / "image.png"), allow_nan=False))
