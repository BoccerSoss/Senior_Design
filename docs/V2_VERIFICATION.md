# V2 verification record

## Matrix-focused revision — 17 November 2026

The revised interface restores the drawing-to-equipment-to-matrix workflow. Its primary tabs are **Drawing**, **Connectivity matrix** and **Grid**. The four-component image is the introductory example; the matrix is displayed directly with labeled rows/columns and direct-edge diagnostics. Linked-document review and navigation remain supporting features inside **Drawing → Connections**. Earlier assistant, audit and facility-graph pages are outside this primary workflow. The original extraction/evaluation convention remains immediate undirected adjacency.

Validation for this revision is recorded separately from the earlier build below:

- `python scripts/test.py`: **166 local tests passed**, including 11 new matrix tests, five new HTTP regressions and classification-correction continuity.
- The introductory four-component demo used the actual classical image pipeline and produced **4 equipment observations, 3 direct connections, no unread names and 4 grid coordinates**. Raw prediction evidence was preserved in the ignored `runs/matrix-focus-check` workspace.
- The matrix used the observed order **T-101, V-101, P-101, T-102** and matched the table below. The example is a known development drawing; this check does not establish blind or held-out recognition accuracy.
- A separate read-only check of all six beginner results confirmed **125 equipment rows, 133 direct edges and 104 nonempty names**. The matrix CSV matched the matrix records, symmetry and zero-diagonal checks passed, and **21 unread names remained explicit**.

| Equipment | T-101 | V-101 | P-101 | T-102 |
| --- | ---: | ---: | ---: | ---: |
| T-101 | 0 | 1 | 0 | 0 |
| V-101 | 1 | 0 | 1 | 0 |
| P-101 | 0 | 1 | 0 | 1 |
| T-102 | 0 | 0 | 1 | 0 |

Browser review verified the three primary tabs, the visible Figure A.1 matrix (20 equipment rows and 18 direct edges), matrix-to-source connection inspection, and linked-document navigation from RL-001 to RL-002. In the four-component example, setting Grid to four columns and one row displayed T-101 (1,1), V-101 (2,1), P-101 (3,1) and T-102 (4,1); its matrix retained the three direct connections. The earlier browser observations below belong to the earlier build.

## Earlier V2 build — 17 November 2026

The following checks were completed locally with Windows and the locked CPython 3.12 runtime before the matrix-focused interface revision.

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
