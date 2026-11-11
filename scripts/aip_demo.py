"""Launch the P&ID Matrix Builder from a checkout."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if __name__ == "__main__":
    from aip.desktop import main

    main()
