# V2 P&ID Matrix Builder

V2 provides a local interface for the original project workflow: **drawing image → equipment and direct connections → connectivity matrix**. The three main tabs are **Drawing**, **Connectivity matrix** and **Grid**. Document links are reviewed and followed inside **Drawing → Connections**. Setup is a small secondary control. The command-line extraction and evaluation tools remain available.

## Open the app

1. On GitHub, select `codex/aip-v2` while this revision is unmerged, then choose **Code → Download ZIP**. Choose **Extract All** in File Explorer and open the project folder inside the extracted folder. A Git checkout also works.
2. Use **64-bit Python 3.12 with pip** on Windows 10/11 and an installed Windows OCR language for local name reading.
3. Double-click **Open AIP V2.cmd**. First launch creates `.venv` and installs the hash-locked packages in `requirements-v2.lock`.
4. The app opens at **http://127.0.0.1:8765**. Keep its console open. Press **Ctrl+C in that console** to stop it.

The browser server runs on this computer. The default workspace is `runs/v2`, containing imported images, analysis evidence and saved corrections. Back up that entire directory while the app is stopped. Closing a browser tab leaves the server running; the app itself does not record the desktop.

For PowerShell setup:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements-v2.lock
.\.venv\Scripts\python.exe scripts/aip_v2.py
# Optional alternate port or workspace:
.\.venv\Scripts\python.exe scripts/aip_v2.py --port 8766 --data runs/v2-other --no-browser
```

## Demonstrate the original four-component example

1. Choose **Open example → Four-component example**. The app reads the bundled PNG through the extraction pipeline. The generator and answer key are separate assets, outside the extractor input.
2. In **Drawing**, inspect the marked components and printed names. Scroll or use **+ / −** to zoom, drag to pan, and select **Fit** to see the whole page.
3. Use the **Components** and **Connections** panels to compare observations with the drawing. **Analyze drawing** runs extraction again on the selected image.
4. Choose **View matrix** or **Connectivity matrix**. Inspect the labeled table and direct connection list, then use its CSV or JSON export if needed.

The four-component drawing is a computer-generated synthetic example awaiting independent human reference review. Its earlier classical run recognized four tags/types and three direct edges. That development result does not establish accuracy on unseen drawings. [Original results and scope](results.md)

For the supplied public test pages, choose **Open example → Beginner ISLOCA drawings** when their PNGs are available locally, or use **Import drawings**. Matching source geometry supplies reviewed symbol locations/types and connection geometry; OCR reads names from the image. An unread name remains visible rather than being filled from an expected-name list.

## Read the connectivity matrix

The matrix belongs to the selected drawing. Its labeled rows and columns use the same explicit saved-observation order, so each cell identifies a pair of equipment observations.

- **1** means an included direct undirected equipment connection is recorded between that pair.
- **0** means no included direct connection is recorded. Review diagnostics may still describe an unread label, unclassified line or invalid observation.
- The diagonal is zero and the matrix is symmetric. Reversed or repeated records for the same pair contribute one connection.
- A route through another equipment item does not create a direct edge. A connection from A to B and B to C does not create A to C.
- Signals, rejected records, unknown line kinds, self-connections and missing endpoints are reported separately from included direct connections.

Observed flow directions remain separate evidence; this matrix does not assert direction or operating state. Unread equipment names retain identifiable unresolved row/column labels. Duplicate printed names are disambiguated with observation IDs. The source drawing remains available by selecting a matrix label or a component in the direct connection list.

The matrix reflects the stored observations, including marked corrections. It does not improve an extraction score or imply that every displayed connection has been independently confirmed. Off-page flags support linked-document navigation; they do not become equipment rows or cross-sheet shortcut edges in the selected drawing's matrix. Unsupported junction/branch conventions stay explicit for later work rather than becoming inferred equipment-to-equipment shortcuts.

## Correct equipment and connections

In **Drawing**, compare a mark with the actual image and save a correction when needed:

- Select a component to edit its printed name, type and review state. **+ Equipment** adds a missed symbol box; **Move** adjusts its position.
- Select a connection to inspect its endpoints, line kind, recorded direction and review state. **+ Pipe** or **+ Signal** records two endpoints and any intermediate bends.
- Review immediate neighbors. A pipe crossing alone is insufficient evidence for a junction or connection.
- Use **Analyze drawing** to run the image reader again. Saved edits are reconciled by observed geometry; ambiguous matches remain pending for review.

Return to **Connectivity matrix** to inspect the updated rows, direct edges and diagnostics. Corrections and analysis history remain saved locally even though there is no separate Audit tab in the demo.

## Follow linked drawings

Choose **Drawing → Connections → Linked drawings** to review the current drawing's document links alongside its direct equipment connections. **Find connector candidates** suggests recorded matches; compare the source regions on both documents before confirming a link. Use the source connector and destination selectors to record a continuation, or open an existing linked destination to continue reviewing the drawing set.

Only confirmed links are followed as established continuations. Suggested or missing matches remain explicit, and links stay within the selected drawing set. Returning to **Connectivity matrix** shows the equipment and immediate relations for the currently selected sheet. Linking two documents does not combine their equipment into a transitive connection matrix.

**Open example → Linked drawing example** loads the generated two-sheet exercise for trying this navigation. It uses known demonstration records and does not measure automatic recognition accuracy.

## Use the separate Grid tool

Choose **Grid**, set **Columns** and **Rows**, and choose **Apply grid**. Cell **(1,1)** is at the top-left; columns increase to the right and rows downward. The center of each marked symbol determines its **(column,row)** cell. Coordinates appear below equipment and in the equipment locations list. **Export equipment coordinates** writes JSON.

The grid divides the actual drawing. It is independent of the matrix order and connection graph. A branch does not restart the grid, and several nearby items may share one cell. Include the drawing/sheet ID when reporting a coordinate. Increasing rows or columns changes the location references, not direct adjacency. Source pixel boxes remain internal placement evidence.

## Import drawings

PNG and JPEG are the primary project inputs. The importer also accepts TIFF, WebP, BMP, PDF pages and ZIP collections. **PDF pages are converted to PNG before extraction**. A newly rendered PDF can differ from a source-checked PNG and use the general detector; see the [measured PNG/PDF results](V2_VERIFICATION.md).

An optional UTF-8 CSV index can provide `file,page,drawing,sheet,revision,title` columns. Upload limits are 150 MB per request/file, 600 MB expanded ZIP contents and 30 million pixels per raster image. Progress and per-file errors appear beside the drawing list. Importing the same source page into the same drawing set reuses its existing record.

## Optional adapters and future research

The original CLI supports an assisted ORNL ChatGPT route: prepare an image-only packet with a generic prompt, use a fresh image-capable session, save its original response, then import and validate it locally. Upload and response transfer are manual stages. [CLI setup and commands](../README.md#setup-and-demo)

Programmatic OpenAI, Anthropic and compatible endpoint adapters remain optional backend facilities. They require approved model/endpoint access and supported credentials; a ChatGPT subscription sign-in is separate from API authentication. Live external requests have not been verified. They are not required to demonstrate the drawing, matrix or grid.

Linked-document review and navigation remain supporting features in **Drawing → Connections**. Candidate-isolation and graph-query experiments remain backend research work. The current interface centers equipment extraction and direct connectivity, with no AI Assistant, Audit, Facility graph or isolation workflow in the primary navigation. It does not authorize equipment operation. [Project scope and remaining work](V2_CAPABILITY_MAP.md)
