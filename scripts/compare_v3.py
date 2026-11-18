"""Compare blind V2 and V3 connections on the same complete, annotated drawing images."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import partial
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import mkdtemp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aip.desktop_analysis import analyze_drawing
from aip.io import digest, image_info, load_json, write_json
from aip.v2.ingest import analysis_objects
from aip.v3 import VERSION
from aip.v3.scoring import IOU_THRESHOLD, TARGET, micro_average, score_graph


def json_hash(value):
    """Stable content fingerprint across OS line endings or JSON pretty printing."""
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def validate_cases(manifest, images):
    result = []
    seen_ids, seen_inputs = set(), set()
    for entry in manifest["sheets"]:
        path = images / entry["image"]
        input_hash = digest(path)
        if entry["id"] in seen_ids or input_hash in seen_inputs:
            raise ValueError("Duplicate drawing ID or input image in benchmark manifest.")
        if input_hash != entry["image_sha256"]:
            raise ValueError(f"Image SHA-256 disagrees with the manifest for {entry['id']}.")
        seen_ids.add(entry["id"])
        seen_inputs.add(input_hash)
        result.append((entry, path))
    if not result:
        raise ValueError("Benchmark needs at least one full drawing.")
    return result


def read_truth(entry, directory, size):
    source = directory / entry["reference"]
    record = load_json(source)
    annotation_hash = json_hash(record)
    metadata = record["image"]
    if (metadata["width"], metadata["height"]) != size:
        raise ValueError("Reference dimensions disagree with image.")
    if metadata.get("source_sha256") and metadata["source_sha256"] != entry["image_sha256"]:
        raise ValueError("Reference belongs to a different input hash.")
    if "observations" in record:
        graph = record["observations"]
    else:
        if metadata.get("sha256") != entry["image_sha256"] or record.get("directed", False):
            raise ValueError("Expected a matching image-bound, undirected reference graph.")
        graph = {"components": record["components"],
                 "connections": [{**item, "kind": "direct_undirected"} for item in record["edges"]]}
    if "tags" in entry:
        tags = entry["tags"]
        if set(tags) != {item["id"] for item in graph["components"]}:
            raise ValueError("Tag crosswalk does not match the reference component IDs.")
        graph["components"] = [{**item, "tag": tags[item["id"]]} for item in graph["components"]]
    return graph, annotation_hash


def _baseline(image, output, *, label_records=None):
    result = analyze_drawing(image, output, use_reference=False)
    components, edges, provenance = analysis_objects(result)
    if provenance["method"] != "classical" or result.reference_citation:
        raise ValueError("Source-assisted extraction cannot be used for this comparison.")
    if label_records is not None:
        evidence = result.directory / "ocr_evidence.json"
        if evidence.is_file():
            label_records.extend(load_json(evidence).get("ocr", {}).get("ink_label_readings", ()))
    return {"components": components, "connections": edges, "arrows": provenance.get("arrows", []), "model": "V2 classical OCR and geometry"}


def _ai(image, baseline, settings, mode, size, label_records):
    if mode in ("local-ml", "subscription-crops"):
        from aip.v3.vision import extract_local_connections
        graph = extract_local_connections(image, baseline.get("components"), baseline.get("arrows"), label_records=label_records)
        if mode == "subscription-crops":
            from aip.v3.label_crops import read_crop_tags
            with image_info(image) as pixels:
                added = read_crop_tags(pixels, graph["components"], settings["_plan_generate"])
            graph.update(provider="chatgpt-plan", model=settings["model"], hosted_labels=added,
                         hosted_method="focused_label_crops")
        return graph
    from aip.v3.vision import extract_connections
    return extract_connections(settings, image.read_bytes(), "image/png", *size)


def run(args):
    manifest = load_json(args.manifest)
    cases = validate_cases(manifest, args.images)
    args.evidence.mkdir(parents=True, exist_ok=True)
    work = Path(mkdtemp(prefix="comparison-", dir=args.evidence))
    sheets, models = [], set()
    settings = {}
    if args.method == "subscription-crops":
        from aip.v3.plan import PlanClient
        client = PlanClient()
        status = client.status()
        model = args.model or status["model"]
        if not status["ready"]:
            raise ValueError(status["error"] or "Sign in from the workbench to use the connected ChatGPT subscription.")
        if model not in {item["slug"] for item in status["models"]}:
            raise ValueError("Select a model available to the connected ChatGPT subscription.")
        settings = {"model": model, "_plan_generate": partial(client.generate, model=model)}
    elif args.method == "vision-api":
        from aip.v3.server import profile
        settings = profile(args.provider) if args.provider != "local" else {"provider": "local"}
        if args.base_url and args.provider not in ("local", "compatible"):
            raise ValueError("Choose compatible when specifying a gateway URL.")
        if args.base_url:
            settings["base_url"] = args.base_url
        if args.model:
            settings["model"] = args.model
    print(f"Evidence: {work}", flush=True)
    for ordinal, (case, original) in enumerate(cases, 1):
        print(f"[{ordinal}/{len(cases)}] {case['id']}", flush=True)
        folder = work / f"page-{ordinal:03}"
        folder.mkdir()
        # Both methods receive identical, metadata-free PNG pixels under a neutral name.
        image = folder / "image.png"
        with image_info(original) as pixels:
            size = pixels.size
            pixels.save(image, compress_level=9)
        row = {"id": case["id"], "image_sha256": case["image_sha256"], "inference_sha256": digest(image),
               "methods": {}, "failures": {}}
        if "graphml_sha256" in case:
            row["graphml_sha256"] = case["graphml_sha256"]
        graphs = {}
        label_records = []
        try:
            graphs["baseline"] = _baseline(image, folder / "v2", label_records=label_records)
        except (ValueError, OSError, RuntimeError) as exc:
            row["failures"]["baseline"] = type(exc).__name__
            (folder / "baseline_error.txt").write_text(str(exc), encoding="utf-8")
        try:
            graphs["ai"] = _ai(image, graphs.get("baseline", {}), settings, args.method, size, label_records)
            if graphs["ai"].get("model"):
                models.add(graphs["ai"]["model"])
        except (ValueError, OSError, RuntimeError) as exc:
            row["failures"]["ai"] = type(exc).__name__
            (folder / "ai_error.txt").write_text(str(exc), encoding="utf-8")

        # Read annotation geometry and the evaluator-only tag crosswalk after inference.
        truth, annotation_hash = read_truth(case, args.manifest.parent, size)
        row["annotation_sha256"] = annotation_hash
        for method, graph in graphs.items():
            write_json(folder / f"{method}_prediction.json", graph)
            scores, details = score_graph(graph, truth)
            row["methods"][method] = scores
            write_json(folder / f"{method}_edge_errors.json", details)
        sheets.append(row)
    methods = {name: micro_average([row["methods"].get(name) for row in sheets]) for name in ("baseline", "ai")}
    metrics = [methods[name]["named_edges"] for name in ("baseline", "ai")]
    delta = (metrics[1]["f1"] - metrics[0]["f1"]
             if all(value is not None and value["f1"] is not None for value in metrics) else None)
    pairs = sum(len(row["methods"]) == 2 for row in sheets)
    warnings = [] if pairs == len(sheets) else ["Some methods failed. Full-cohort metrics for an incomplete method are unavailable."]
    if not all(row["methods"].get("baseline", {}).get("named_edges") for row in sheets):
        warnings.append("Equipment-name scoring requires complete reference transcriptions and a completed inference run.")
    report = {
        "schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(), "cohort": manifest["cohort"],
        "source_url": manifest.get("source_url"), "review_status": manifest["review_status"], "workbench_version": VERSION,
        "manifest_sha256": json_hash(manifest), "ai_method": args.method, "model": ", ".join(sorted(models)) or None,
        "baseline_method": "V2 Windows OCR and geometry with source-specific lookup disabled",
        "target_f1": TARGET, "drawing_count": len(sheets), "completed_pairs": pairs,
        "metric": "Micro-F1 for direct undirected equipment edges. Both endpoints must match symbol boxes at IoU >= 0.50 and their normalized printed tags for named_edges.",
        "iou_threshold": IOU_THRESHOLD, "methods": methods, "delta_f1": delta, "sheets": sheets, "warnings": warnings,
    }
    write_json(work / "comparison.json", report)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.report, report)
    print(f"{pairs}/{len(sheets)} pairs completed. Report: {args.report or work / 'comparison.json'}", flush=True)
    return 0 if pairs == len(sheets) else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, required=True, help="Directory containing the image filenames in the manifest")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/evaluation/nureg-cr5745-beginner.json")
    parser.add_argument("--evidence", type=Path, default=ROOT / "runs/v3-benchmark")
    parser.add_argument("--report", type=Path, help="Also write the comparison to this path")
    parser.add_argument("--method", choices=("local-ml", "vision-api", "subscription-crops"), default="local-ml")
    parser.add_argument("--provider", choices=("openai", "anthropic", "compatible", "local"), default="local")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()
    try:
        return run(args)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"Benchmark could not complete: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
