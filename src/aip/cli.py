"""Small Windows-friendly CLI. Inference and reference evaluation are separate stages."""
from __future__ import annotations
import argparse
import ast
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
import PIL
from PIL import Image, ImageDraw
from .io import ROOT, digest, image_info, load_json, new_run, parse_response, prepare_input, read_packet, stamp, write_json
from .core import ValidationError, normalize_response, validate_document
from .evaluation import evaluate
from .exporting import export_document


def doctor():
    result = {"python": platform.python_version(), "python_executable": sys.executable,
              "supported_python": "3.12.x", "pillow": PIL.__version__, "locked_pillow": "12.0.0",
              "runtime_ok": sys.version_info[:2] == (3, 12) and PIL.__version__ == "12.0.0",
              "api_credentials_configured": any(bool(os.getenv(n)) for n in
                  ("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY")),
              "supported_formats": ["PNG", "JPEG"], "pdf": "unsupported",
              "available_methods": ["classical", "baseline", "interactive-manual"],
              "classical_prerequisite": "Windows 10/11, Windows PowerShell 5.1, installed Windows OCR language; checked on extraction",
              "live_api_integration": "not configured; account login is not used as API authentication",
              "timezone": "America/New_York", "timestamp": stamp()}
    print(json.dumps(result, indent=2))
    return 0 if result["runtime_ok"] else 2


def process_run(packet, runs_dir="runs", method="baseline", response=None, model=None,
                provider=None, stage="original"):
    provider = provider or ("local" if method in {"baseline","classical"} else "ORNL ChatGPT (user-reported account)")
    path, run = new_run(runs_dir, method, provider, model, packet, stage)
    start = time.perf_counter()
    try:
        meta = read_packet(packet)
        run["prompt_version"] = meta["prompt_version"]
        run["prompt_sha256"] = meta["prompt_sha256"]
        run["input_sha256"] = meta["drawing"]["sha256"]
        write_json(path / "input_manifest.json", meta)
        # Preserve exact prompt used, outside the canonical extractor response.
        shutil.copyfile(Path(packet) / "prompt.txt", path / "prompt.txt")
        if response is not None:
            run["method"] = "interactive-manual"
            run["access_route"] = "user upload to fresh ChatGPT session and raw response transfer"
            run["manual_stages"] = ["image upload and fresh-session confirmation", "model selection", "raw response save/transfer"]
            run["settings"]["model_recorded_by_user"] = model is not None
            # Preserve original bytes before parsing, even when invalid.
            shutil.copyfile(Path(response), path / "raw_response.txt")
        elif method == "interactive-manual":
            run["status"] = "awaiting_manual_response"
            run["manual_stages"] = ["upload image.png with prompt.txt to a NEW ORNL ChatGPT session", "save exact response then import-response"]
            write_json(path / "manual_handoff.json", {"reason": "No live programmatic route configured; browser control was blocked by URL-confidence policy during setup.",
                "packet": str(Path(packet).resolve()), "upload_only": ["image.png", "prompt.txt"],
                "instructions": "Use a fresh session with no reference answer or generation conversation. Record displayed model and settings. Save original response verbatim."})
            return path, 3
        elif method in {"baseline","classical"}:
            from .baseline import baseline_info
            from .classical import classical_info
            run["settings"] = classical_info() if method == "classical" else baseline_info()
            native_paths = {"PATH","SYSTEMROOT","SYSTEMDRIVE","WINDIR","TEMP","TMP","USERPROFILE",
                            "LOCALAPPDATA","APPDATA","PROGRAMDATA","PROGRAMFILES","PROGRAMFILES(X86)",
                            "COMMONPROGRAMFILES","COMMONPROGRAMFILES(X86)","HOMEDRIVE","HOMEPATH"}
            env = {k:v for k,v in os.environ.items() if k.upper() in native_paths}
            argv = [sys.executable, "-I", str(ROOT / "scripts/baseline_worker.py"), str(Path(packet).resolve()),method]
            if method == "classical":
                scratch = path / "ocr_scratch"
                scratch.mkdir()
                argv.append(str(scratch))
            worker = subprocess.run(argv,cwd=packet, env=env, capture_output=True, timeout=180)
            (path / "raw_response.txt").write_bytes(worker.stdout)
            (path / "worker_stderr.txt").write_bytes(worker.stderr)
            run["boundary"] = "fresh isolated Python process; Python opens limited to packet/runtime/code allowlist and private OCR scratch; native OCR helper receives only an image path. Trusted-code guard, not an OS/native-process sandbox."
            if worker.returncode:
                raise ValueError(f"Image baseline worker failed (exit {worker.returncode}); see worker_stderr.txt.")
            # Native Windows OCR is outside Python's audit hook. Enforce packet
            # immutability again before accepting any worker observation.
            read_packet(packet)
        else:
            raise ValueError(f"Inference method unavailable: {method}. Use classical, baseline or interactive-manual.")
        raw = parse_response(path / "raw_response.txt")
        if method == "classical" and response is None:
            evidence = raw.get("method_evidence",{})
            write_json(path / "ocr_evidence.json",evidence)
            native = evidence.get("ocr",{}).get("native")
            if native:
                run["settings"].update({k:native.get(k) for k in ("engine","language","windows_version","powershell_version","max_image_dimension")})
        run["status"] = "validated"
        run["duration_seconds"] = round(time.perf_counter()-start, 6)
        doc = normalize_response(raw, meta["drawing"], run)
        if response is not None and model is None:
            doc["warnings"].append("Interactive model not recorded; provenance incomplete.")
        write_json(path / "prediction.json", doc)
        export_document(doc, path / "exports", image_path=Path(packet) / "image.png")
        return path, 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        run["status"] = "failed_validation_or_inference"
        write_json(path / "failure.json", {"status": "failed", "error": str(exc),
            "errors": getattr(exc, "errors", [str(exc)]), "scored": False,
            "metrics": None, "raw_response_preserved": (path / "raw_response.txt").exists()})
        print(f"Run failed: {exc}", file=sys.stderr)
        return path, 2
    finally:
        run["duration_seconds"] = round(time.perf_counter()-start, 6)
        write_json(path / "run.json", run)


def evaluate_files(prediction, reference, out):
    try:
        doc = validate_document(load_json(prediction))
        ref = load_json(reference)
        report = evaluate(doc, ref)
        report["prediction_path"] = str(Path(prediction).resolve())
        report["prediction_sha256"] = digest(prediction)
        report["reference_path"] = str(Path(reference).resolve())
        report["reference_sha256"] = digest(reference)
        report["evaluated_at"] = stamp()
        write_json(out, report)
        lines = ["AIP reference evaluation", "", f"Status: {report.get('scoring_status', report.get('review_status', 'provisional'))}"]
        for name in ("components", "component_types", "connections"):
            m = report[name]
            lines.extend(["", name, json.dumps(m, indent=2)])
        lines.extend(["", "Unresolved/unscored observations:", json.dumps(report.get("unresolved", {}), indent=2)])
        Path(out).with_suffix(".txt").write_text("\n".join(lines)+"\n", encoding="utf-8")
        return report
    except (ValueError, OSError, KeyError, TypeError) as exc:
        write_json(out, {"status": "invalid_unscored", "error": str(exc), "metrics": None})
        raise


def demo(out, method="classical"):
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Demo output must be new or empty; existing attempts are preserved.")
    out.mkdir(parents=True, exist_ok=True)
    clean = ROOT / "data/input/four_component.png"
    ref = ROOT / "data/reference/four_component.reference.json"
    controls = out / "control_images"
    controls.mkdir()
    image = image_info(clean)
    Image.new("RGB", image.size, "white").save(controls / "blank.png")
    reports, observations = {}, {}
    for name, source in (("clean", clean), ("blank", controls / "blank.png")):
        packet = out / "packets" / name
        prepare_input(source, packet)
        run, code = process_run(packet, out / "runs",method=method)
        if code:
            raise ValueError(f"Demo {name} baseline failed; evidence preserved in {run}")
        report = evaluate_files(run / "prediction.json", ref, run / "evaluation.json") if name == "clean" else None
        raw = parse_response(run / "raw_response.txt")
        observations[name] = raw
        reports[name] = {"run": str(run), "candidates": len(raw["components"]),
                         "contacts": len(raw["connections"]), "evaluation": report}
    # Control change uses a detected stroke location, never a reference edge.
    cut = image.copy()
    contacts = observations["clean"]["connections"]
    removed = None
    if contacts:
        match = re.search(r"\[[\d, ]+\]", contacts[0].get("evidence", ""))
        if match:
            x0, y0, x1, y1 = ast.literal_eval(match.group())
            x, y = (x0+x1)//2, (y0+y1)//2
            removed = [x-12, y-9, x+12, y+9]
            ImageDraw.Draw(cut).rectangle(removed, fill="white")
    if removed is None:
        raise ValueError("Baseline found no supported pipe evidence for a disconnected control; cannot claim control passed.")
    cut.save(controls / "disconnected.png")
    packet = out / "packets/disconnected"
    prepare_input(controls / "disconnected.png", packet)
    run, code = process_run(packet, out / "runs",method=method)
    if code:
        raise ValueError(f"Disconnected control failed; inspect {run}")
    raw = parse_response(run / "raw_response.txt")
    reports["disconnected"] = {"run": str(run), "candidates": len(raw["components"]), "contacts": len(raw["connections"]),
                                "erased_pixel_rectangle": removed}
    controls_ok = (reports["blank"]["candidates"] == reports["blank"]["contacts"] == 0 and
                   reports["disconnected"]["contacts"] < reports["clean"]["contacts"])
    # Record the unavailable interactive route as its own attempt.
    manual, code = process_run(out / "packets/clean", out / "runs", method="interactive-manual")
    controls_ok = controls_ok and code == 3
    summary = {"timestamp": stamp(), "method":method, "runs": reports, "control_checks_pass": controls_ok,
               "manual_attempt": str(manual), "manual_exit_code": code,
               "interpretation": "Automatic local OCR/outline development run; no live VLM response obtained. Metrics remain provisional on this known synthetic image." if method=="classical" else "Geometry observations only: no OCR/types; no live VLM response obtained.",
               "human_review_status": "pending independent human review"}
    write_json(out / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0 if controls_ok else 2


def main(argv=None):
    parser = argparse.ArgumentParser(prog="aip", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    p = sub.add_parser("prepare-input")
    p.add_argument("image", type=Path); p.add_argument("--packet", type=Path, required=True)
    p = sub.add_parser("extract")
    p.add_argument("--packet", type=Path, required=True); p.add_argument("--runs", type=Path, default=Path("runs"))
    p.add_argument("--method", choices=["classical", "baseline", "interactive-manual"], default="classical")
    p = sub.add_parser("import-response")
    p.add_argument("response", type=Path); p.add_argument("--packet", type=Path, required=True)
    p.add_argument("--runs", type=Path, default=Path("runs")); p.add_argument("--model"); p.add_argument("--provider")
    p.add_argument("--stage", choices=["original", "reviewed"], default="original")
    p = sub.add_parser("validate"); p.add_argument("prediction", type=Path)
    p = sub.add_parser("export"); p.add_argument("prediction", type=Path)
    p.add_argument("--out", type=Path, required=True); p.add_argument("--image", type=Path)
    p = sub.add_parser("evaluate"); p.add_argument("prediction", type=Path)
    p.add_argument("--reference", type=Path, required=True); p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("demo"); p.add_argument("--out", type=Path, required=True)
    p.add_argument("--method",choices=["classical","baseline"],default="classical")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor": return doctor()
        if args.command == "prepare-input":
            print(json.dumps(prepare_input(args.image, args.packet), indent=2)); return 0
        if args.command == "extract":
            path, code = process_run(args.packet, args.runs, args.method)
            print(f"Run evidence: {path}"); return code
        if args.command == "import-response":
            path, code = process_run(args.packet, args.runs, "interactive-manual", args.response,
                                     args.model, args.provider, args.stage)
            print(f"Run evidence: {path}"); return code
        if args.command == "validate":
            doc = validate_document(load_json(args.prediction))
            print(f"Valid schema {doc['schema_version']}; {len(doc['components'])} observations, {len(doc['connections'])} direct connections."); return 0
        if args.command == "export":
            print(export_document(validate_document(load_json(args.prediction)), args.out, args.image)); return 0
        if args.command == "evaluate":
            print(json.dumps(evaluate_files(args.prediction, args.reference, args.out), indent=2)); return 0
        if args.command == "demo": return demo(args.out,args.method)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr); return 2
    return 2
