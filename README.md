# AIP drawing extraction prototype

The P&ID Matrix Builder desktop demo and command-line tools interpret simplified raster process symbols and tags and export a direct connection matrix. The default local method combines Windows OCR with pixel-based shape rules. A geometry-only baseline and an assisted ChatGPT response importer are also available.

## Desktop demo

1. Double-click `Open AIP Demo.cmd` in the project folder. The app opens with the synthetic drawing loaded.
2. Select **Analyze drawing** to view the equipment, connections, and labeled matrix. Click a matrix cell to see what its value means. Use **Show detections** to toggle the overlay.
3. Select **Save matrix…** to save the exact matrix CSV or **Open results folder** to inspect the run evidence. Use **Open drawing…** to analyze another PNG or JPEG.

The app requires Windows 10 or 11, 64-bit Python 3.12 with Tcl/Tk, and an installed Windows OCR language. The launcher sets up the virtual environment and installs the locked Pillow version if needed. The synthetic comparison remains provisional pending independent human review of the reference.

The setup section below covers command-line use.

The local classical method recognized **4 tags/types and 3 direct edges** on the clean synthetic development image. Component, component-type and edge **precision, recall and F1 were all 1.0**. Blank, disconnected, changed-tag and erased-label controls passed. **67 local automated tests passed**. The earlier geometry-only baseline had a tagged-graph F1 of 0.0. See [results and scope](docs/results.md) and the [portable classical summary](reports/classical_summary.json).

## Setup and demo

Supported environment: **Windows x64, CPython 3.12.x**. The sole runtime dependency is Pillow 12.0.0; [requirements.lock](requirements.lock) locks its CPython 3.12 Windows x64 wheel by SHA-256. Run these commands in PowerShell:

```powershell
git clone https://github.com/BoccerSoss/Senior_Design.git
Set-Location Senior_Design
# While the implementation pull request is unmerged:
git switch codex/aip-prototype
powershell -NoProfile -File scripts/setup.ps1
.\.venv\Scripts\python.exe scripts/aip.py doctor
.\.venv\Scripts\python.exe scripts/test.py
.\.venv\Scripts\python.exe scripts/aip.py demo --method classical --out runs/demo-001
.\.venv\Scripts\python.exe scripts/check_classical_controls.py --demo runs/demo-001 --out runs/demo-001-label-controls
```

The classical method requires **Windows 10/11, Windows PowerShell 5.1 and an installed Windows OCR language**. English (United States) OCR was verified locally. These are OS capabilities; no extra pip dependency or account login is needed. If unavailable, use `demo --method baseline` for geometry-only processing. OCR pixel tests skip when the recognizer is unavailable; CI uses an explicit baseline demo so it does not depend on an optional OCR language.

Use a new output directory for each demo and label-control check. The demo preserves clean, blank and disconnected attempts, raw outputs, OCR evidence, exports and evaluation. It records the pending manual route separately. Runs and packets are gitignored. Setup uses `--require-hashes --only-binary=:all:`; the drawing generator has its own separate pinned environment.

## Use the workflow

[Workflow instructions](docs/workflow.md) cover `prepare-input`, `extract`, `import-response`, `validate`, `export`, and `evaluate`, including exact commands and exit codes. Input support is PNG/JPEG. PDF, animation and images above 30 million pixels are explicitly unsupported.

For assisted VLM interpretation, use a **new ChatGPT session** with only the prepared `image.png` and generic `prompt.txt`. Keep reference answers, source vectors, generator code, manifests and prior generation history outside the session. Preserve the original response before corrections. No live VLM response has been obtained, and no API integration is configured. Image upload and response transfer remain manual stages.

The classical method reads tag pixels, associates text above symbol boxes, and classifies supported tank, valve and pump outlines. It preserves original OCR errors and suffix-crop readings; agreement between repeated views is correlated evidence, with confidence unknown. It never repairs a label from the reference answer. `--method baseline` leaves tags null and types unknown.

A fresh worker, two-file packet allowlist, pre/post hashes and a Python file-open guard reduce accidental reference access in trusted code. Native OCR receives only raster paths and uses separate scratch storage. The guard is not an adversarial OS or native-process sandbox.

## Files and conventions

- [Primary synthetic image](data/input/four_component.png) and [separate reference](data/reference/four_component.reference.json).
- [Canonical schema](schemas/extraction-v1.schema.json) and [software examples](data/examples/). Example predictions test plumbing; they are not inference results.
- [Drawing package](output/aip_four_component/README.txt), including editable SVG, deterministic generator, source citations and reference annotations.
- [Generic extraction prompt](prompts/extract_from_image.txt), [results and limits](docs/results.md), and [spring handoff](docs/spring_handoff.md).

Edges mean immediate undirected equipment adjacency. A route through another component is not a direct edge. Tags are normalized deterministically without reference-based OCR repair. Unknowns and invalid outputs remain visible. Boxes use original-image pixels, a top-left origin and half-open `[xmin,ymin,xmax,ymax]` bounds. See the workflow for matching rules and metrics.

The simplified tank, valve and pump shapes derive from public DOE handbook figures linked in the drawing package. No ISA, DOE, DEXPI or plant-standard compliance is claimed. Implementation changes are reviewed through pull requests in [BoccerSoss/Senior_Design](https://github.com/BoccerSoss/Senior_Design).
