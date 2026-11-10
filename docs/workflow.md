# Run the extraction and evaluation workflow

Run commands from the checkout root in PowerShell after completing README setup. CLI flags control behavior; `config.example.json` documents defaults and contains no credentials. Runtime support is Windows x64 with CPython 3.12.x and the SHA-locked Pillow 12.0.0 wheel. Automatic classical OCR additionally requires Windows 10/11, Windows PowerShell 5.1 and an installed Windows OCR language. English (United States) was verified locally; OCR adds no pip dependency.

## Prepare a packet and consume the image

```powershell
.\.venv\Scripts\python.exe scripts/aip.py prepare-input data/input/four_component.png --packet packets/clean-001
.\.venv\Scripts\python.exe scripts/aip.py extract --packet packets/clean-001 --method classical --runs runs
```

Preparation applies EXIF orientation, composites transparency onto white, converts to RGB PNG and removes metadata without resizing. The packet contains exactly `image.png` and `prompt.txt`. A sibling manifest records hashes, dimensions and source provenance; it is not an inference input. Packet allowlisting and hashes are checked before execution and again before accepting worker output.

`classical` is the default for CLI extraction and demo. It detects linework candidates and contacts, reads visible tags with Windows.Media.Ocr, associates observed text above symbol boxes, and classifies supported tank, opposed-triangle valve and circular-pump outlines from pixels. It reads no source vectors, expected labels, component count or reference graph. A plain circle remains unknown unless supported pump entry/outlet strokes are visible.

Generic separator-derived suffix crops at 1x and 3x can resolve native OCR ambiguities using the actual suffix pixels. Original full-image errors and every crop reading remain in `raw_response.txt` and `ocr_evidence.json`. Same-engine crop agreement is correlated evidence; it supplies no calibrated confidence or human verification. Ambiguous text/associations stay unresolved.

For geometry-only processing, pass `--method baseline`; it leaves tags null and types unknown. Both methods support only documented simplified linework and horizontal contacts, with uncertainties for bends, branches and crossings. If the optional OCR engine/language is unavailable, classical extraction records an explicit failed attempt; it does not silently substitute the reference or a prediction fixture.

The worker starts in a fresh Python process with a trusted-code file-open guard. Native OCR receives only raster paths and writes working data to separate scratch storage. Python audit hooks do not sandbox native processes or malicious code; packet checks and code review protect this prototype's intended extraction boundary.

## Assisted ChatGPT interpretation

```powershell
.\.venv\Scripts\python.exe scripts/aip.py extract --packet packets/clean-001 --method interactive-manual --runs runs
```

This records a pending manual attempt and intentionally returns exit code **3**. The VLM workflow has not produced a live response; no API integration is configured. A signed-in interactive account does not establish API access. Local Windows OCR success is separate from live VLM access.

1. Open a **new session** in an authorized image-capable ChatGPT account. Upload only `packets/clean-001/image.png` and paste `packets/clean-001/prompt.txt`.
2. Keep the repository, truth files, source vector, generator, reference manifests and generation conversation out of that session. Record the actual displayed model and relevant settings.
3. Save the complete original response verbatim as `response.txt`. Import it before editing any labels or connections:

```powershell
.\.venv\Scripts\python.exe scripts/aip.py import-response response.txt --packet packets/clean-001 --runs runs --provider 'ACTUAL PROVIDER' --model 'ACTUAL DISPLAYED MODEL'
```

The importer accepts one JSON object, optionally enclosed in one complete JSON code fence. It copies exact raw bytes before parsing. Invalid JSON, duplicate keys, dangling endpoints, self-loops and schema errors create failed/unscored records with diagnostics. A missing model is disclosed as incomplete provenance. Human upload, model selection and response transfer remain manual.

Import corrections as a new response file with `--stage reviewed`. Preserve the original run, record the reviewer and link the original and corrected run IDs in your review record. Score original and reviewed results separately.

## Validate, export and evaluate

The extraction/import command prints its new run directory. Assign that printed directory to `$runDir`; the placeholder below must be replaced:

```powershell
$runDir = 'runs/REPLACE_WITH_PRINTED_RUN_ID'
.\.venv\Scripts\python.exe scripts/aip.py validate "$runDir/prediction.json"
.\.venv\Scripts\python.exe scripts/aip.py export "$runDir/prediction.json" --out "$runDir/reexport" --image packets/clean-001/image.png
.\.venv\Scripts\python.exe scripts/aip.py evaluate "$runDir/prediction.json" --reference data/reference/four_component.reference.json --out "$runDir/evaluation.json"
```

Exports include equipment JSON/CSV, direct-edge JSON/CSV, labeled adjacency CSV and a readable connection list. An overlay is produced only for supplied image-supported boxes; connector coordinates are not invented. The overlay image must match recorded input provenance. Unknown identities remain explicit in matrix labels.

Tag normalization uses Unicode NFKC, uppercase, trimming, normalized dashes and removal of spaces beside `-`, `/` and `:`. It never inserts missing punctuation or repairs a tag using truth. Reversed/duplicate undirected edges normalize to one unordered pair with a warning, while the original response remains intact. Instrument signals and directed links are outside this contract.

Equipment tags, `(tag,type)` pairs and unordered direct-edge tag pairs are scored separately. Each metric reports correct/missing/extra items and TP, FP, FN, precision, recall and F1:

- Precision = TP / (TP + FP); recall = TP / (TP + FN).
- F1 = 2TP / (2TP + FP + FN).
- Undefined denominators produce null. Empty predictions against nonempty truth produce recall and F1 of zero.

Unresolved observations are listed outside tag matching; missed reference objects still count as FN. Invalid results remain unscored rather than receiving a success metric. Evaluation checks the reference image hash, so a changed control image cannot silently use clean-image truth. Reference review is pending, so current scores are provisional.

## Tests, controls and exit codes

```powershell
.\.venv\Scripts\python.exe scripts/test.py
.\.venv\Scripts\python.exe scripts/aip.py demo --method classical --out runs/demo-002
.\.venv\Scripts\python.exe scripts/check_classical_controls.py --demo runs/demo-002 --out runs/demo-002-label-controls
```

The demo uses the actual clean raster, an equal-size blank image and a copy with a pipe segment removed at a location derived from observed line evidence. It preserves each attempt and writes `summary.json`. The label-control script changes one observed tag to an arbitrary tag and separately erases all observed labels, checking that OCR follows changed pixels and retains missing labels as null. It does not score changed images against clean-image truth. Every output directory must be new.

The demo records the pending VLM route separately. Fixture predictions in `data/examples/` test software only; they do not count as drawing interpretation. Use `demo --method baseline` to reproduce the geometry-only comparison. Native OCR pixel tests skip when the optional recognizer is unavailable; CI's demo explicitly selects baseline processing.

Exit codes: **0** success, **2** invalid input/validation/inference failure, **3** manual response required. Cost remains null when unobserved. Recorded CLI duration covers local processing or import, not human upload or remote inference time. Preserve failed attempts and avoid comparing only the best development run.
