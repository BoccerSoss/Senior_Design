# AIP drawing extraction prototype

This research CLI converts raster process drawings into equipment and immediate undirected connectivity observations, validates and exports those records, and evaluates them against a separate reference. It includes a local geometry baseline and an assisted ChatGPT response importer. The prototype does not determine operating or isolation actions.

The initial local image test produced **4 geometric candidates and 3 contacts**, with tags unresolved and types unknown. Tagged component and connection **F1 were both 0.0**. Blank and disconnected-image controls behaved differently from the clean image. These are provisional development results; independent human reference review is pending. See [results](docs/results.md).

## Setup and demo

Supported environment: **Windows x64, CPython 3.12.x**. The sole runtime dependency is Pillow 12.0.0; [requirements.lock](requirements.lock) locks its CPython 3.12 Windows x64 wheel by SHA-256. Run these commands in PowerShell:

```powershell
git clone https://github.com/BoccerSoss/Senior_Design.git
Set-Location Senior_Design
powershell -NoProfile -File scripts/setup.ps1
.\.venv\Scripts\python.exe scripts/aip.py doctor
.\.venv\Scripts\python.exe scripts/test.py
.\.venv\Scripts\python.exe scripts/aip.py demo --out runs/demo-001
```

Use a new demo output directory each time. The demo generates clean, blank and disconnected attempts, preserves their raw outputs, exports review artifacts, evaluates the clean attempt, and records the pending manual route. Runs and packets are gitignored. Setup installs with `--require-hashes --only-binary=:all:`; the drawing generator has a separate environment and dependency lock within its package.

## Use the workflow

[Workflow instructions](docs/workflow.md) cover `prepare-input`, `extract`, `import-response`, `validate`, `export`, and `evaluate`, including exact commands and exit codes. Input support is PNG/JPEG. PDF, animation and images above 30 million pixels are explicitly unsupported.

For assisted interpretation, use a **new ChatGPT session** with only the prepared `image.png` and generic `prompt.txt`. Keep reference answers, source vectors, generator code, manifests and prior generation history outside the session. Preserve the original response before corrections. Live account capabilities have not been verified, and no API integration is configured. Image upload and response transfer are manual stages.

The local baseline consumes pixels but has no OCR or semantic equipment classifier. It reports provisional geometric candidates and uninterrupted horizontal contacts. A fresh worker process, packet allowlist, hashes and a Python file-open guard reduce accidental reference access in trusted code; the guard is not an adversarial OS sandbox.

## Files and conventions

- [Primary synthetic image](data/input/four_component.png) and [separate reference](data/reference/four_component.reference.json).
- [Canonical schema](schemas/extraction-v1.schema.json) and [software examples](data/examples/). Example predictions test plumbing; they are not inference results.
- [Drawing package](output/aip_four_component/README.txt), including editable SVG, deterministic generator, source citations and reference annotations.
- [Generic extraction prompt](prompts/extract_from_image.txt), [results and limits](docs/results.md), and [spring handoff](docs/spring_handoff.md).

Edges mean immediate undirected equipment adjacency. A route through another component is not a direct edge. Tags are normalized deterministically without reference-based OCR repair. Unknowns and invalid outputs remain visible. Boxes use original-image pixels, a top-left origin and half-open `[xmin,ymin,xmax,ymax]` bounds. See the workflow for matching rules and metrics.

The simplified tank, valve and pump shapes derive from public DOE handbook figures linked in the drawing package. No ISA, DOE, DEXPI or plant-standard compliance is claimed. Implementation changes are reviewed through pull requests in [BoccerSoss/Senior_Design](https://github.com/BoccerSoss/Senior_Design).
