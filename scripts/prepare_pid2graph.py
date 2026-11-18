"""Prepare evaluator-only GraphML references for three complete PID2Graph OPEN100 sheets."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image


SOURCE = "https://zenodo.org/records/14803338"
EQUIPMENT = {"general": "unknown", "instrumentation": "instrument", "pump": "pump",
             "tank": "tank", "valve": "valve"}
LINE_MARKS = {"connector", "arrow", "crossing"}


def convert(path, size):
    root = ET.parse(path)
    keys = {key.get("id"): key.get("attr.name") for key in root.findall("{*}key")}

    def fields(element):
        return {keys[value.get("key")]: value.text for value in element}

    nodes = {n.get("id"): fields(n) for n in root.findall(".//{*}node")}
    solid = defaultdict(set)
    for edge in root.findall(".//{*}edge"):
        if fields(edge)["edge_label"] == "solid":
            a, b = edge.get("source"), edge.get("target")
            solid[a].add(b)
            solid[b].add(a)
    equipment = []
    pairs = set()
    for start, node in nodes.items():
        if node["label"] not in EQUIPMENT:
            continue
        box = [rounder(float(node[key])) for key, rounder in (
            ("xmin", math.floor), ("ymin", math.floor), ("xmax", math.ceil), ("ymax", math.ceil))]
        equipment.append({"id": start, "type": EQUIPMENT[node["label"]], "bbox": box})
        for neighbor in solid[start]:
            previous, current = start, neighbor
            seen = {start}
            while current not in seen:
                seen.add(current)
                kind = nodes[current]["label"]
                if kind in EQUIPMENT:
                    pairs.add(tuple(sorted((start, current))))
                    break
                # Only pass through a marking with exactly one onward solid line.
                # A branch or ambiguous crossing cannot connect extra pairs by inference.
                if kind not in LINE_MARKS or len(solid[current]) != 2:
                    break
                previous, current = current, next(i for i in solid[current] if i != previous)
    return {"image": {"width": size[0], "height": size[1]},
            "observations": {"components": equipment,
                             "connections": [{"source": a, "target": b, "kind": "direct_undirected"}
                                             for a, b in sorted(pairs)]}}


def prepare(source, output):
    output.mkdir(parents=True, exist_ok=True)
    sheets = []
    for number in range(3):
        image = source / f"{number}.png"
        graph = source / f"{number}.graphml"
        with Image.open(image) as drawing:
            reference = convert(graph, drawing.size)
        reference_file = f"open100-{number}.json"
        (output / reference_file).write_text(json.dumps(reference, indent=2), encoding="utf-8")
        sheets.append({"id": f"open100-{number}", "image": image.name,
                       "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                       "graphml_sha256": hashlib.sha256(graph.read_bytes()).hexdigest(),
                       "reference": reference_file})
    manifest = {"cohort": "PID2Graph OPEN100 whole-sheet pilot", "source_url": SOURCE,
                "review_status": "Independent published GraphML; derived undirected solid-line equipment pairs. "
                                 "Degree-two line marks are transparent. Branches and sheet boundaries stop a trace. "
                                 "The source has no tag truth or process/signal semantics for solid lines.",
                "sheets": sheets}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Extracted PID2Graph/Complete/PID2Graph OPEN100 folder")
    parser.add_argument("output", type=Path, help="Directory for converted evaluator references")
    args = parser.parse_args()
    prepare(args.source, args.output)
