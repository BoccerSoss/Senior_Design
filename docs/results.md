# Local development results

The November 10, 2026 classical run exercised automatic local interpretation on `data/input/four_component.png`. It combined Windows.Media.Ocr with raster shape rules and immediate horizontal-contact tracing. The worker received raster pixels rather than source vectors or reference answers. No live VLM response was obtained. The [portable classical summary](../reports/classical_summary.json) records the result and controls without host-specific paths.

| Clean-image method | Candidates / contacts | Correct tagged components | Correct tag/type pairs | Correct direct edges | Component / type / edge F1 |
| --- | --- | ---: | ---: | ---: | --- |
| Earlier geometry-only baseline | 4 / 3 | 0; all tags null | 0; all types unknown | 0 scored by tag | 0.0 / 0.0 / 0.0 |
| Automatic classical OCR/shape method | 4 / 3 | 4 | 4 | 3 | 1.0 / 1.0 / 1.0 |

The classical result has no false positives, false negatives or unresolved identities on this image. Precision and recall are also 1.0 for all three scoring populations. The earlier baseline remains preserved as a distinct method and result; unresolved observations were disclosed rather than filled from truth.

## Pixel and label controls

| Changed image | Candidates / contacts | Observed behavior |
| --- | --- | --- |
| Equal-size blank | 0 / 0 | No equipment or memorized graph returned |
| Observed pipe segment removed | 4 / 2 | The visible disconnection removed a contact |
| One observed tag replaced with `ZX-742` | 4 / 3 | New tag read; old tag absent |
| All observed equipment labels erased | 4 / 3 | All four tags null; supported shape types still classified |

The pipe cut and label-edit locations came from observed line/text evidence. Expected control behavior lives in an evaluator-side script and is not passed to extraction. Changed images were not scored against the clean reference, because their hashes differ. These are development robustness checks, not held-out accuracy tests.

## OCR evidence and limitations

The full-image Windows OCR initially read `T-IOI` and `P-IOI`. Those original errors remain in raw evidence. The generic tag procedure located visible separator ink and reread the numeric suffix pixels at native and 3x scale. Both suffix views read `101`, producing the selected `T-101` and `P-101` without reference-based substitution. All readings and crop boxes are retained. These are correlated passes of the same engine over related pixels, not independent verification or calibrated confidence. The engine exposes no confidence; confidence remains null.

Shape classification uses candidate pixels only: tank outlines, opposed-triangle valves and near-circular pumps with center-entry and tangent-outlet strokes. A plain circle and incomplete pump strokes remain unknown. Tests vary supported geometry size and position; tag prefixes do not determine equipment type.

Reference acceptance and independent human verification are pending. All scores remain provisional. The rules and OCR crop procedure were developed on this known synthetic example; its perfect score does not establish general P&ID performance or a broader F1 target. No operating or isolation decision is supported by these results.

## Software validation

The expanded local suite passed **63 tests in 9.968 seconds, with no skips**. It covers Windows OCR, original-coordinate word boxes, tag association, supported/ambiguous shape rules and retained crop readings as well as validation, exports, evaluation and input isolation. The earlier baseline version passed 40 local tests and 11 public-workflow CLI checks. These are local results; they do not assert that CI has run on the new commit.

Run `scripts/test.py` to obtain results on a new machine. Native OCR pixel tests skip when the optional Windows recognizer is unavailable. CI uses an explicit geometry-baseline demo rather than assuming OCR languages are installed.

An earlier classical development attempt produced the clean extraction but failed subsequent packet validation when native OCR created a cache directory in the packet. OCR now runs with private scratch storage. The verified rerun passed packet allowlist/hash checks before and after inference; its separate manual route returned the expected exit code 3. Earlier attempts remain preserved in ignored local runs.

The tests using known predictions exercise evaluator plumbing. Their expected scores are not image-inference results. Every failed or pending workflow attempt is preserved separately from scored output when rerunning the CLI.

## Remaining interpretation work

The local automatic route works for the documented simplified families. It calls Windows OCR through the pinned PyWinRT dependencies and requires Windows 10/11 with an installed OCR language. English (United States) was verified locally. No live ChatGPT response or API adapter is configured. Assisted VLM inference still requires a fresh authorized session, image upload, recorded model/settings and verbatim response transfer. An interactive account login is not API authentication.

Both local methods support large linework regions and uninterrupted horizontal contacts. Bends, branches, crossings, connected text and unfamiliar symbols can produce missing or spurious candidates. OCR can misread labels or associate a word with the wrong symbol. Unknowns and uncertainties require review.

Reproduce local artifacts with:

```powershell
.\.venv\Scripts\python.exe scripts/test.py
.\.venv\Scripts\python.exe scripts/aip.py demo --method classical --out runs/reproduce-001
.\.venv\Scripts\python.exe scripts/check_classical_controls.py --demo runs/reproduce-001 --out runs/reproduce-001-label-controls
```

Use `demo --method baseline` to reproduce the earlier geometry-only behavior without OCR. Generated runs and packets remain gitignored. The demo writes raw outputs, OCR words/crops, input/prompt hashes, provenance, exports, overlays, evaluation and control summaries. Timing covers local processing/import only; remote inference and human time are unobserved. Cost is null when unobserved.
