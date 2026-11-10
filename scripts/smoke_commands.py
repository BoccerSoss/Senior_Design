"""Exercise public commands, preserving failures and an explicit local replay.

Run once with a new work directory. This is software verification, not a live
ChatGPT extraction. Generate scientific experiment evidence separately with demo.
"""
import json
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aip.io import load_json, stamp, write_json

work = ROOT / ".work/command_smoke"
work.mkdir(parents=True, exist_ok=False)
packet = work / "packet"
runs = ROOT / "runs/command-checks"
log = []


def command(*args, expected=0):
    argv = [sys.executable, str(ROOT / "scripts/aip.py"), *map(str, args)]
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    log.append({"argv": argv, "expected_exit": expected, "actual_exit": proc.returncode,
                "stdout": proc.stdout, "stderr": proc.stderr})
    write_json(ROOT / "evidence/command_checks.json", {"timestamp": stamp(), "checks": log})
    assert proc.returncode == expected, log[-1]
    return proc.stdout


command("doctor")
command("prepare-input", ROOT / "data/input/four_component.png", "--packet", packet)
command("extract", "--packet", packet, "--runs", runs, "--method", "baseline")
baseline = next(p.parent for p in runs.glob("*/prediction.json"))
command("validate", baseline / "prediction.json")
command("export", baseline / "prediction.json", "--out", baseline / "reexport", "--image", packet / "image.png")
command("evaluate", baseline / "prediction.json", "--reference", ROOT / "data/reference/four_component.reference.json",
        "--out", baseline / "reevaluation.json")
command("import-response", baseline / "raw_response.txt", "--packet", packet, "--runs", runs,
        "--provider", "local baseline replay for software verification; not ChatGPT",
        "--model", "not-applicable-local-baseline")
command("extract", "--packet", packet, "--runs", runs, "--method", "interactive-manual", expected=3)
malformed = work / "malformed_response.txt"
malformed.write_text('{"components":', encoding="utf-8")
command("import-response", malformed, "--packet", packet, "--runs", runs, expected=2)
command("evaluate", malformed, "--reference", ROOT / "data/reference/four_component.reference.json",
        "--out", work / "invalid_unscored.json", expected=2)
unsupported = work / "not_supported.pdf"
unsupported.write_bytes(b"Not a real PDF")
command("prepare-input", unsupported, "--packet", work / "unsupported_packet", expected=2)
write_json(ROOT / "evidence/command_checks.json", {"timestamp": stamp(), "all_expected_outcomes": True,
    "scope": "Public command checks and explicit baseline-response replay; no live VLM test", "checks": log})
print(f"{len(log)} public command outcomes verified.")
