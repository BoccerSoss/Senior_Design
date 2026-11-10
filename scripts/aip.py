"""Source-checkout CLI launcher; no package installation required."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aip.cli import main
raise SystemExit(main())
