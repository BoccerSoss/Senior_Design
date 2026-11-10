# Spring handoff

The current interface is rendered PNG/JPEG pixels plus provenance, followed by equipment observations with uncertainty and immediate undirected adjacency. Reference answers enter only evaluation. Local legends govern interpretation; new conventions need versioned annotations and tests.

| Priority | Owner/interface | Next deliverable and acceptance evidence |
| --- | --- | --- |
| 1 | Software and reference reviewers | Accept or redraw the synthetic example, independently check its reference, and preserve the first fresh-session image response with actual model/settings. Keep original and corrected results separate. |
| 2 | Collection contributor with software owner | Supply a small permitted public-source catalog with document URL/ID, exact page, drawing ID/type, access date, SHA-256, quality and access notes. Demonstrate reproducible ingestion and duplicate/error handling. |
| 3 | Symbol/reference contributor with software owner | Supply source-linked definitions, drawing legends, tags/types, original-pixel boxes, direct edges and unresolved notes. Link every reference version to its image hash and retain reviewer disagreements. |
| 4 | Literature contributor with symbol reviewer | Compare candidate methods using verified original citations, data/scoring scope, limitations and transferability. Separate published results from measured project results. |
| 5 | Team evaluation | Freeze the schema, normalization, prompt and matching rules before held-out testing. Split development and evaluation drawings; retain all attempts, invalid outputs and controls. |
| 6 | Interpretation implementation | Add OCR or an authorized image-capable inference adapter, then test tag/type recognition and direct connectivity separately. Preserve uncertainty and prohibit truth-based correction in automatic output. |
| 7 | Expanded drawing coverage | Agree branch/crossing/instrument conventions before adding them. Test broken scans, image degradation, tag variation and ambiguous contacts. |

Carry forward the honest baseline result: clean 4 candidates/3 contacts, blank 0/0 and cut-pipe 4/2, with all tags null and types unknown. Tagged component and edge F1 are both zero and provisional. OCR and semantic identification remain open work.

Each contributor should run setup/tests locally and use their own authorized inference access. Account capability and API availability must be demonstrated rather than inferred from an interactive login. Review changes through pull requests in [the project repository](https://github.com/BoccerSoss/Senior_Design).

Keep scalable scraping, training, full DEXPI export and GraphRAG optional until a concrete requirement and evidence justify them. The small project JSON interface is not a DEXPI-conformance claim. Preserve fall attempts when producing improved spring results.
