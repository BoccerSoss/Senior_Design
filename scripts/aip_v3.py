"""Start the V3 drawing workbench."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    from aip.v3.server import main
    main()
