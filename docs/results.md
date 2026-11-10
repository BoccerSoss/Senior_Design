# Initial local results

The November 10, 2026 development run exercised the full local workflow on the synthetic image in `data/input/four_component.png`. The baseline consumed image pixels in a fresh worker process; no live VLM response was obtained.

| Image | Geometric candidates | Horizontal contacts | Observed tags | Equipment types |
| --- | ---: | ---: | --- | --- |
| Clean synthetic image | 4 | 3 | All null | All unknown |
| Equal-size blank control | 0 | 0 | None | None |
| Image with an observed pipe segment removed | 4 | 2 | All null | All unknown |

The cut location came from baseline line evidence rather than a reference edge. These controls show that the local method consumes and responds to image changes. Candidate/contact counts do not establish correct equipment recognition.

Against the separate synthetic reference, clean-image **tagged component F1 = 0.0** and **direct-edge F1 = 0.0**. All four candidate identities and their three contacts remain unresolved by tag, so they cannot match the tagged reference graph. The missed reference items remain false negatives. Component types also remain unresolved. Geometry counts are reported separately from tagged-graph accuracy.

Reference acceptance and independent human verification are pending. All scores remain provisional. A tiny synthetic development example does not establish general P&ID performance or a broader F1 target. No operating or isolation decision is supported by these results.

## Software validation

Before publication, **40 automated local tests** and **11 public-workflow CLI checks** passed. The checks cover normalization, graph evaluation, malformed/dangling responses, exact raw-response preservation, separate reviewed runs, packet hashes and allowlisting, metadata stripping, image transforms, actual worker input, accidental truth-read denial, and blank/disconnected controls. These are local results; they do not assert that public CI has run or passed.

The tests using known predictions exercise evaluator plumbing. Their expected scores are not image-inference results. Every failed or pending workflow attempt is preserved separately from scored output when rerunning the CLI.

## Remaining interpretation work

No OCR, semantic equipment classifier or live ChatGPT adapter is configured. Account capabilities and models are unverified. Assisted inference requires a fresh authorized session, image upload, recorded model/settings and verbatim response transfer. An account login is not API authentication.

The baseline supports large linework regions and uninterrupted horizontal contacts. Bends, branches, crossings, connected text and unfamiliar symbols can produce missing or spurious candidates. Unknowns and uncertainties must be reviewed rather than replaced with the reference answer.

Reproduce local artifacts with:

```powershell
.\.venv\Scripts\python.exe scripts/test.py
.\.venv\Scripts\python.exe scripts/aip.py demo --out runs/reproduce-001
```

Generated runs and packets remain gitignored. The demo writes raw outputs, input/prompt hashes, provenance, exports, overlays, evaluation and control summaries. Timing covers local processing/import only; remote inference and human time are unobserved. Cost is null when unobserved.
