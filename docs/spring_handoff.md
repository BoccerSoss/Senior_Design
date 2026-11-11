# Spring handoff

The current interface is rendered PNG/JPEG pixels plus provenance, followed by equipment observations with uncertainty and immediate undirected adjacency. Reference answers enter only evaluation. Local legends govern interpretation; new conventions need versioned annotations and tests.

| Priority | Owner/interface | Next deliverable and acceptance evidence |
| --- | --- | --- |
| 1 | Software and reference reviewers | Accept or redraw the synthetic example, independently check its reference, and preserve the first fresh-session image response with actual model/settings. Keep original and corrected results separate. |
| 2 | Collection contributor with software owner | Supply a small permitted public-source catalog with document URL/ID, exact page, drawing ID/type, access date, SHA-256, quality and access notes. Demonstrate reproducible ingestion and duplicate/error handling. |
| 3 | Symbol/reference contributor with software owner | Supply source-linked definitions, drawing legends, tags/types, original-pixel boxes, direct edges and unresolved notes. Link every reference version to its image hash and retain reviewer disagreements. |
| 4 | Literature contributor with symbol reviewer | Compare candidate methods using verified original citations, data/scoring scope, limitations and transferability. Separate published results from measured project results. |
| 5 | Team evaluation | Freeze the schema, normalization, prompt and matching rules before held-out testing. Split development and evaluation drawings; retain all attempts, invalid outputs and controls. |
| 6 | Interpretation implementation | Extend the working Windows OCR/shape method beyond simplified families, or compare an authorized image-capable VLM adapter. Test tags, types and connectivity separately; preserve uncertainty and prohibit truth-based automatic correction. |
| 7 | Expanded drawing coverage | Agree branch/crossing/instrument conventions before adding them. Test broken scans, image degradation, tag variation and ambiguous contacts. |

Carry forward both methods and their history. The original geometry-only baseline produced clean 4 candidates/3 contacts with unresolved tags/types and tagged component/edge F1 of zero. The newer local classical method recognized all 4 tags/types and 3 edges on the same known development image: component, type and edge precision/recall/F1 were all 1.0, still provisional pending independent reference review.

Blank 0/0, cut-pipe 4/2, arbitrary-tag replacement and erased-label controls passed. Native full-image OCR errors remain preserved alongside separator-derived suffix crop readings at 1x/3x; crop agreement is correlated evidence with confidence unknown. The implemented shape vocabulary is limited to simplified tank, valve and stroked circular-pump outlines. Broader recognition remains open work, and these development results are not held-out general accuracy.

Each contributor should run setup/tests locally. Classical processing calls Windows OCR through the pinned PyWinRT dependencies and requires Windows 10/11 with an installed Windows OCR language. The geometry-only baseline remains available without OCR. Use individual authorized access for VLM comparisons. No live VLM response has been obtained and no API route is configured. Review changes through pull requests in [the project repository](https://github.com/BoccerSoss/Senior_Design).

Keep scalable scraping, training, full DEXPI export and GraphRAG optional until a concrete requirement and evidence justify them. The small project JSON interface is not a DEXPI-conformance claim. Preserve fall attempts when producing improved spring results.
