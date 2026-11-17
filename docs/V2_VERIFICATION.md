# V2 verification record

Verified locally on 17 November 2026 with Windows and the locked CPython 3.12 runtime.

- `python scripts/test.py`: **149 tests passed**. This includes real loopback HTTP/MCP requests, correction identity changes, parallel routes, missing/rejected connections, facility boundaries, provider response validation and grid independence.
- `node --check src/aip/v2/static/app.js`: passed.
- `python scripts/smoke_commands.py`: **11 expected CLI outcomes verified**.
- `Open AIP V2.cmd --help`: first-launch virtual environment creation, hash-locked dependency installation, runtime imports and entry point passed.
- Browser review: loaded the two-sheet example, inspected the separate Grid tab, changed 14×10 cells to 12×8, checked equipment coordinates, and inspected candidate isolation, graph exports and provider settings. The grid change left graph connections and isolation results unchanged.
- Handoff ZIP integrity and manifest hashes are verified by `scripts/package_handoff.py`.

## Supplied beginner PNGs

All six files imported through the V2 image pipeline into a separate local verification workspace. Source geometry was selected by exact prepared-image matching; names were read from the image. No external provider was called.

| NUREG/CR-5745 PDF page represented by PNG | Equipment observations | Nonempty OCR names | Process edges | Grid coordinates |
| --- | ---: | ---: | ---: | ---: |
| 65 / Figure A.1 | 20 | 20 | 18 | 20 |
| 66 / Figure A.2 | 29 | 24 | 34 | 29 |
| 67 / Figure A.3 | 39 | 27 | 44 | 39 |
| 68 / Figure A.4 | 8 | 8 | 7 | 8 |
| 69 / Figure A.5 | 9 | 9 | 8 | 9 |
| 70 / Figure A.6 | 20 | 16 | 22 | 20 |
| **Total** | **125** | **104** | **133** | **125** |

The source-checked reference records contain geometry and types, not expected equipment names. Twenty-one observations have no readable OCR name. Every imported observation remains unreviewed. These counts measure pipeline output and completeness; they are not independently scored recognition accuracy or evidence of generalization to unseen drawings. [Source report](https://www.osti.gov/servlets/purl/10145659)

## Supplied page-65 PDF

The actual single-page PDF converted to a 2791×3600 PNG before extraction and imported successfully. Its raster differs from the supplied PNG, so it correctly did not receive that PNG's exact geometry match. General classical extraction produced **19 observations, 3 nonempty names and 20 unclassified connections**. The supplied PNG produced **20 observations, 20 nonempty names and 18 source-checked process edges**.

PDF import success therefore does not establish equivalent recognition. Prefer the supplied, source-checked beginner PNGs for the current demonstration. A newly rendered or unseen drawing needs review and may benefit from the optional image AI workflow.

## Outstanding validation

Provider transports were tested with mocked responses; no live OpenAI, Anthropic or gateway request was performed. Approved credentials, model permissions and data authorization remain necessary for that step. A ChatGPT subscription sign-in is separate from API access. DGN conversion, large-library performance, institutional authentication and site-specific clearance rules have not been validated. See [capabilities and remaining work](V2_CAPABILITY_MAP.md).

Runtime SQLite databases, uploaded drawings, detailed OCR evidence and local reports remain outside the public source package. This record is software validation, not an update to the course AI prompt log.
