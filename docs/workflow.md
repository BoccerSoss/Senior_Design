# Run the extraction and evaluation workflow

Run commands from the checkout root in PowerShell after completing README setup. CLI flags control behavior; `config.example.json` documents defaults and contains no credentials. Runtime support is Windows x64 with CPython 3.12.x and the SHA-locked Pillow 12.0.0 wheel.

## Prepare a packet and consume the image

```powershell
.\.venv\Scripts\python.exe scripts/aip.py prepare-input data/input/four_component.png --packet packets/clean-001
.\.venv\Scripts\python.exe scripts/aip.py extract --packet packets/clean-001 --method baseline --runs runs
```

Preparation applies EXIF orientation, composites transparency onto white, converts to RGB PNG and removes metadata without resizing. The packet contains exactly `image.png` and `prompt.txt`. A sibling manifest records hashes, dimensions and source provenance; it is not an inference input. Packet allowlisting and hashes are checked before baseline execution.

The local baseline reads raster pixels and traces supported horizontal contacts. It leaves all tags null and types unknown. It supports neither semantic symbol recognition nor arbitrary bends, branches and crossings. Its raw observations are useful for testing input consumption, validation, controls and exports.

## Assisted ChatGPT interpretation

```powershell
.\.venv\Scripts\python.exe scripts/aip.py extract --packet packets/clean-001 --method interactive-manual --runs runs
```

This records a pending manual attempt and intentionally returns exit code **3**. Account capabilities and a supported programmatic inference route are unverified; no API integration is configured. A signed-in interactive account does not establish API access.

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
.\.venv\Scripts\python.exe scripts/aip.py demo --out runs/demo-002
```

The demo uses the actual clean raster, an equal-size blank image and a copy with a pipe segment removed at a location derived from observed line evidence. It preserves each attempt and writes `summary.json`. It also records the unavailable manual route separately. Fixture predictions in `data/examples/` test software only; they do not count as drawing interpretation.

Exit codes: **0** success, **2** invalid input/validation/inference failure, **3** manual response required. Cost remains null when unobserved. Recorded CLI duration covers local processing or import, not human upload or remote inference time. Preserve failed attempts and avoid comparing only the best development run.
