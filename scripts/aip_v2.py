"""Launch the local V2 drawing workbench from an extracted project folder."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    from aip.v2.server import main

    main()
