"""Run the existing extraction pipeline for the desktop drawing viewer."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp

from .cli import evaluate_files, process_run
from .drawing_reference import export_reference, matching_reference
from .io import ROOT, load_json, prepare_input, write_json
from .label_review import make_label_packet


@dataclass
class DesktopResult:
    directory: Path
    image: Path
    overlay: Path | None
    equipment: list[tuple[str, str]]
    connections: list[tuple[str, str]]
    matrix_labels: list[str]
    matrix: list[list[int]]
    evaluation: dict | None
    matrix_file: Path
    markings_file: Path | None = None
    markings: dict | None = None
    reference_citation: str | None = None
    reference_url: str | None = None
    vision_transcription: bool = False
    unread_labels: int = 0


class AnalysisError(RuntimeError):
    def __init__(self, diagnostic: str, directory: Path | None):
        self.diagnostic = diagnostic
        self.directory = directory
        message = diagnostic
        if directory is not None:
            message += f"\nEvidence saved in {directory}"
        super().__init__(message)


def _csv_records(path: Path, required_columns: set[str]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not required_columns.issubset(reader.fieldnames or []):
            raise ValueError(f"{path.name} is missing required columns.")
        rows = list(reader)
    if any(None in row or any(row[key] is None for key in required_columns) for row in rows):
        raise ValueError(f"{path.name} contains an incomplete or overlong record.")
    return rows


def _read_exports(exports: Path):
    matrix_file = exports / "adjacency.csv"
    with matrix_file.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    if not rows or not rows[0] or rows[0][0] != "component_id":
        raise ValueError("adjacency.csv is missing the component_id header.")
    labels = rows[0][1:]
    if any(not label.strip() for label in labels) or len(labels) != len(set(labels)):
        raise ValueError("adjacency.csv must have unique, nonempty column labels.")
    if len(rows) != len(labels) + 1:
        raise ValueError("adjacency.csv row count does not match its column labels.")
    matrix = []
    for label, row in zip(labels, rows[1:]):
        if len(row) != len(labels) + 1 or row[0] != label:
            raise ValueError("adjacency.csv row labels must match the column labels in order.")
        if any(value not in {"0", "1"} for value in row[1:]):
            raise ValueError("adjacency.csv must contain only 0 or 1 in the matrix cells.")
        matrix.append([int(value) for value in row[1:]])

    equipment_rows = _csv_records(exports / "equipment.csv", {"id", "type", "matrix_label"})
    labels_by_id = {}
    types_by_label = {}
    for row in equipment_rows:
        ident, label, equipment_type = row["id"], row["matrix_label"], row["type"]
        if not ident.strip() or not label.strip() or not equipment_type.strip():
            raise ValueError("equipment.csv contains a blank identity, matrix label or type.")
        if ident in labels_by_id or label in types_by_label:
            raise ValueError("equipment.csv contains duplicate identities or matrix labels.")
        labels_by_id[ident] = label
        types_by_label[label] = equipment_type
    if set(types_by_label) != set(labels):
        raise ValueError("equipment.csv labels do not match adjacency.csv.")

    connections = []
    pairs = set()
    for row in _csv_records(exports / "edges.csv", {"source", "target"}):
        if row["source"] not in labels_by_id or row["target"] not in labels_by_id:
            raise ValueError("edges.csv refers to equipment outside the exported identity list.")
        source, target = labels_by_id[row["source"]], labels_by_id[row["target"]]
        pair = frozenset((source, target))
        if len(pair) != 2 or pair in pairs:
            raise ValueError("edges.csv contains a self-loop or duplicate undirected connection.")
        pairs.add(pair)
        connections.append((source, target))
    for row, source in enumerate(labels):
        for column, target in enumerate(labels):
            if matrix[row][column] != int(frozenset((source, target)) in pairs):
                raise ValueError("adjacency.csv does not agree with the undirected connections in edges.csv.")
    equipment = [(label, types_by_label[label]) for label in labels]
    return equipment, connections, labels, matrix, matrix_file


def _failure_diagnostic(directory: Path, exit_code: int) -> str:
    diagnostic = f"Drawing analysis failed with exit code {exit_code}."
    failure = directory / "failure.json"
    if failure.is_file():
        report = load_json(failure)
        if isinstance(report, dict) and report.get("error"):
            diagnostic = str(report["error"])
    worker_stderr = directory / "worker_stderr.txt"
    if worker_stderr.is_file():
        lines = worker_stderr.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        if lines:
            diagnostic += f"\n{lines[-1]}"
    return diagnostic


def analyze_drawing(image_path: Path, output_root: Path = ROOT / "runs" / "desktop",
                    *, use_reference: bool = True, label_response: Path | None = None) -> DesktopResult:
    """Prepare, extract and read one drawing in a new preserved session directory."""
    directory = None
    try:
        output_root = Path(output_root).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        session = Path(mkdtemp(prefix="drawing-", dir=output_root))
        directory = session
        packet = session / "packet"
        metadata = prepare_input(image_path, packet)
        match = matching_reference(metadata["drawing"]) if use_reference else None
        citation = url = None
        if match is not None:
            directory = export_reference(packet, session / "runs", match, label_response)
            citation, url = match[1]["citation"], match[1]["source_url"]
        else:
            if label_response is not None:
                raise ValueError("A matching source geometry file is required to import label crops.")
            directory, exit_code = process_run(packet, session / "runs", method="classical")
            if exit_code != 0:
                raise AnalysisError(_failure_diagnostic(directory, exit_code), directory)

        image = packet / "image.png"
        exports = directory / "exports"
        equipment, connections, labels, matrix, matrix_file = _read_exports(exports)
        overlay = exports / "overlay.png"
        markings_file = exports / "markings.json"
        markings = load_json(markings_file) if markings_file.is_file() else None
        unread = sum(item.get("tag_bbox") is not None and item["label"].startswith("unresolved:")
                     for item in (markings or {}).get("components", []))
        evaluation = None
        reference = ROOT / "data" / "reference" / "four_component.reference.json"
        if match is None and reference.is_file():
            reference_hash = load_json(reference)["image"]["sha256"]
            if metadata["drawing"]["source_sha256"] == reference_hash:
                evaluation = evaluate_files(directory / "prediction.json", reference, directory / "evaluation.json")
        return DesktopResult(directory, image, overlay if overlay.is_file() else None,
                             equipment, connections, labels, matrix, evaluation, matrix_file,
                             markings_file if markings is not None else None, markings, citation, url,
                             label_response is not None, unread)
    except (OSError, ValueError, TypeError, KeyError, csv.Error) as exc:
        diagnostic = str(exc)
        if directory is not None:
            try:
                write_json(directory / "desktop_failure.json", {"error": diagnostic})
            except OSError:
                pass
        raise AnalysisError(diagnostic, directory) from exc


def prepare_label_review(result):
    """Build a packet for the same source-checked image already analyzed."""
    geometry = load_json(result.directory / "source_geometry.json")
    target = Path(mkdtemp(prefix="label-review-", dir=result.directory)) / "upload"
    return make_label_packet(result.image, geometry, target)
