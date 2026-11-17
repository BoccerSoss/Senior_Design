# V2 project scope and remaining work

Revised 17 November 2026 to restore the original project design: **read a P&ID image, identify equipment and immediate connections, and display/export the connectivity matrix**. The browser interface makes that workflow easier to demonstrate. Its three main tabs are **Drawing**, **Connectivity matrix** and the separately requested **Grid**.

| Project requirement | Current deliverable | Limit or review needed |
| --- | --- | --- |
| Image input | PNG/JPEG input; PDF pages converted to PNG before extraction. [Importer](../src/aip/v2/ingest.py) | Newly rendered or unseen images may need corrections. |
| Equipment tags and types | Image marks, name/type records and component review. Existing local OCR and source-checked beginner geometry are reused. | Source geometry contains no expected names; unread labels remain explicit. Recognition accuracy remains separate from software completeness. |
| Direct equipment connections | Endpoint records, supporting paths, line kinds and connection review. | Crossings and unsupported branches are not silently resolved. Signals and unknown/rejected records remain distinguishable. |
| Connectivity matrix | Visible labeled 0/1 table, explicit observation order, symmetric immediate adjacency, zero diagonal, direct edge list and CSV/JSON export. [Matrix code](../src/aip/v2/matrix.py) | Zero means no included recorded direct edge; diagnostics can still identify incomplete evidence. No path through another equipment becomes a shortcut edge. |
| Visual review and correction | Zoom/pan, marked symbols and connections, equipment/connection editing and stored corrections. | Ambiguous reconciliation after reanalysis requires review. |
| Connected documents | Link review, connector comparison and destination navigation inside **Drawing → Connections**. | Suggestions require confirmation. Off-page flags remain navigation/topology evidence rather than equipment rows or cross-sheet shortcut edges in a selected-sheet matrix. |
| Coordinates | Separate **Grid** overlay on the original drawing. Vertical/horizontal axes pass through exact symbol-center positions; their sorted one-based indexes give named coordinates and matching JSON. [Grid code](../src/aip/v2/grid.py) | Counts are derived and read-only. Spacing can vary and near-center lines can be dense. Exact duplicate centers share a point with a warning; missing boxes remain unplaced. Source positions, pipes and matrix adjacency stay unchanged. |
| Validation and evaluation | Original CLI validation/evaluation and preserved run evidence; matrix diagnostics are shown beside its output. [Original results](results.md), [verification record](V2_VERIFICATION.md) | The handmade/reference acceptance and independent student check remain human steps. Development fixtures do not establish held-out performance. |
| Reproducible local use | Windows launcher, locked dependencies, local data, public source package and tests. [Guide](V2_WORKBENCH.md) | Teammates must test their own setup; their access/signoff is not inferred. |

The four-component PNG is at `data/input/four_component.png`; its generator and reference answer are under `output/aip_four_component/source` and `output/aip_four_component/truth`. Extraction uses rendered image evidence, while reference answers are reserved for evaluation.

Grid axes come from the exact distinct center coordinates of valid source boxes. The overlay neither shifts equipment nor clusters nearby centers. Axis index coordinates can recompute when the observation set changes. Labels, the coordinate list and JSON export share the grid API's result. Drawing and Connectivity matrix retain the source and direct-connectivity evidence.

## Optional backend research

Earlier V2 work explored graph queries, candidate isolation and REST/MCP access after reviewing [Nuclearn's public LINK description](https://nuclearn.ai/products/link-for-pids/). That comparison is research context. Linked-document review and navigation remain part of the Drawing view in support of the core extraction/matrix workflow. The AI Assistant, Audit and Facility graph pages are removed from the primary workflow; saved analysis/correction history remains available in local records.

Optional provider adapters, read-only tools and candidate-isolation functions remain backend experiments. No live external AI request has been verified. Approved API access is separate from a ChatGPT subscription; see [OpenAI's billing guidance](https://help.openai.com/en/articles/9039756-managing-billing-for-chatgpt-and-the-api-platform). DGN conversion still requires an external configured converter. Institutional authentication, multiuser hosting, large-library validation and site-specific isolation rules are future work.

The original project supports research toward isolation planning. Its current deliverable is equipment and direct connectivity evidence, not an approved clearance or operating decision.
