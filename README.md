# AIP drawing extraction prototype

## V3 equipment connections and drawing questions

Download the `codex/aip-v3` branch, extract the ZIP and double-click [Open AIP V3.cmd](Open%20AIP%20V3.cmd). Open **Four-component example** and select **V3** in **Method**. Choose **Find connections with AI → Local connection model → Trace drawing**, then **View matrix**. Switch between V2 and V3 to compare the routes on the original image. **Ask about drawing** accepts questions such as “What connects to V-101?” and links each answer to the recorded evidence.

**Open example → Beginner ISLOCA drawings** opens six bundled full-page PNGs. Select an item to see the text region used for its printed name. Select a connection to inspect its direction and the location of the arrowhead that supports it. Repeating a local trace of the same image reopens the saved result.

The left-hand **Drawing library** lists all saved drawings by collection. Click a drawing to switch while staying on **Drawing**, **Connectivity matrix** or **Grid**. Search covers all collections.

On matching beginner PNGs, **Read with symbol reference** uses source-checked symbol types and locations and reads the names from the drawing. The image-only AI trace is available separately.

Open **Grid** for a square grid over the original drawing. Coordinates display automatically there and in the Drawing tab’s component list. An equipment coordinate such as **(3, 2)** identifies the nearest intersection in column 3, row 2, counting from the upper left. Select the equipment to inspect its position. Choose **Equipment centers** to draw lines through every marked center. **Export JSON** and drawing questions use the selected layout.

**Connectivity matrix → Benchmark results** shows V3.3 image-only scores of **0.296 F1 for localized direct connections** and **0.143 F1 for correctly named direct connections** across the six provisional beginner sheets. The four-component synthetic control scored **1.000** on both measures. The project target is **0.90**. The panel also retains the earlier independent OPEN100 benchmark.

[V3 demo and model setup](docs/V3_WORKBENCH.md) · [Evaluation results and reproduction](docs/V3_EVALUATION.md) · [Independent annotation source](https://zenodo.org/records/14803338)

**Have a ChatGPT subscription?** In V3.1, open **Ask about drawing → ChatGPT subscription → Continue with ChatGPT**. OpenAI lists the models your account can use. If your workspace does not offer plan sharing, select **ChatGPT in your browser** to copy the question and its matrix records. [Update an existing copy and connect ChatGPT](docs/V3_WORKBENCH.md#update-an-existing-copy)

## V2 P&ID Matrix Builder

Extract the repository ZIP and double-click [Open AIP V2.cmd](Open%20AIP%20V2.cmd). It opens locally at `http://127.0.0.1:8765`. Choose **Open example → Four-component example** or import a drawing image. Review the marked **Components** and **Connections**, then choose **View matrix**.

The three main tabs are **Drawing**, **Connectivity matrix** and **Grid**. The matrix displays labeled rows and columns, immediate undirected equipment connections, review diagnostics and CSV/JSON exports. Corrections to equipment or connections update the matrix. **Drawing → Connections** also supports reviewing and following links between documents. The separate **Grid** tab has the same **Regular grid** and **Equipment centers** layouts as V3. PDFs are converted to PNG before extraction.

This follows the original drawing-to-equipment-to-connectivity deliverable. [V2 instructions](docs/V2_WORKBENCH.md) · [Project scope](docs/V2_CAPABILITY_MAP.md) · [Verification record](docs/V2_VERIFICATION.md)

The earlier source-aligned grid revision passed **180 local tests**. All 125 marked equipment centers across the six beginner PNGs were checked against their exact grid intersections. That check applies to the **Equipment centers** layout.

The earlier matrix-focused revision passed **166 local tests**. The four-component example ran through the actual image extractor and produced **4 equipment records, 3 direct connections and the labeled 4×4 matrix**, with its raw prediction preserved. This is a known development example, not a blind accuracy test. The [verification record](docs/V2_VERIFICATION.md) separates that result from the newer source-aligned grid revision.

The earlier six-beginner-PNG import check produced coordinates for all 125 equipment observations and 104 nonempty OCR names; 21 names remained unread. These are completeness counts, not independently scored accuracy. Optional external AI remains separately configured and untested with live requests.

## Original desktop prototype (V1)

The P&ID Matrix Builder desktop demo and command-line tools interpret simplified raster process symbols and tags and export a direct connection matrix. The default local method combines Windows OCR with pixel-based shape rules. A geometry-only baseline and an assisted ChatGPT response importer are also available.

### Desktop demo

1. On GitHub, select the `codex/aip-prototype` branch and choose **Code → Download ZIP**. In File Explorer, choose **Extract All**, then open the project folder inside the extracted folder. You can also use a Git checkout.
2. Double-click `Open AIP Demo.cmd`. The app opens with the synthetic drawing loaded.
3. Select **Analyze drawing**. Scroll over the drawing or use **+** and **−** to zoom. Drag to pan and select **Fit** to see the whole page. Click a green arrow or blue route for its coordinates, direction and equipment sequence. Choose **Samples → Flow arrows** to try the arrow example.
4. Use **Show detections** to view the overlay. Select **Save markings…** for `markings.json`, **Save matrix…** for the adjacency CSV, or **Open results folder** for all run files.
5. Select **Symbol reference** for the visual guide. Use **Open drawing…** for a PNG or JPEG. Convert PDF pages to PNG before opening them. [Symbol reference](docs/symbol_reference.html)

For the six beginner ISLOCA PNGs, leave **Use matching drawing reference** enabled. The app checks the image hash and uses source-checked symbol types, label regions, pipes, and arrows. OCR reads the equipment names from those image regions. Select **Source figure** to compare the result with the report. Clear the checkbox and analyze again for fully automatic detection. Other drawings use automatic detection when there is no matching geometry.

Grid coordinates appear below equipment and in the equipment table. Each pump-led route starts at column 1, and the row number increases from the top pump to the bottom. Click a mark for grid and pixel coordinates.

For difficult printed labels, select **Prepare AI crops**. Upload the resulting `label-crops.png` to a fresh image-capable ChatGPT session with `prompt.txt`. Save its JSON reply and use **Import labels…** to view the transcription. The app keeps the local OCR run and the imported run in their own result folders.

The app requires Windows 10 or 11, 64-bit Python 3.12 with Tcl/Tk, and an installed Windows OCR language. The launcher creates the virtual environment and installs the pinned Pillow and PyWinRT packages if needed. The synthetic comparison remains provisional pending independent human review of the reference.

The setup section below covers command-line use.

The local classical method recognized **4 tags/types and 3 direct edges** on the clean synthetic development image. Component, component-type and edge **precision, recall and F1 were all 1.0**. Blank, disconnected, changed-tag and erased-label controls passed. See [results and scope](docs/results.md) and the [portable classical summary](reports/classical_summary.json).

`markings.json` records symbol and tag boxes, traced pipe points, arrowheads and flow routes. Coordinates use the prepared image's top-left pixel origin. Each route lists its equipment candidates in flow order and ends at the visible arrow tip. The matrix shows direct adjacency. The detector supports horizontal, vertical and shallow diagonal arrowheads. It traces supported horizontal runs through connected symbols and stops at unresolved branches or gaps. Dense engineering drawings can produce incomplete or incorrect observations.

The **Flow arrows** sample adds two chevrons to the original four-equipment drawing. It shows T-101 → V-101 and P-101 → V-101. The P-101–T-102 connection has no arrow and reports **Unknown**.

## Setup and demo

Use **Windows x64 and CPython 3.12.x**. The dependency lock pins the Pillow and PyWinRT wheels by SHA-256. Run these commands in PowerShell. [Dependency lock](requirements.lock)

```powershell
git clone https://github.com/BoccerSoss/Senior_Design.git
Set-Location Senior_Design
# While the implementation pull request is unmerged:
git switch codex/aip-prototype
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements.lock
.\.venv\Scripts\python.exe scripts/aip.py doctor
.\.venv\Scripts\python.exe scripts/test.py
.\.venv\Scripts\python.exe scripts/aip.py demo --method classical --out runs/demo-001
.\.venv\Scripts\python.exe scripts/check_classical_controls.py --demo runs/demo-001 --out runs/demo-001-label-controls
```

The classical method calls Windows OCR directly through PyWinRT. English (United States) OCR was verified locally. If the Windows OCR language is unavailable, use `demo --method baseline` for geometry-only processing. OCR pixel tests skip when the recognizer is unavailable. CI uses the baseline method for the separate CLI demo.

Use a new output directory for each demo and label-control check. The demo preserves clean, blank and disconnected attempts, raw outputs, OCR evidence, exports and evaluation. It records the pending manual route separately. Runs and packets are gitignored. Setup uses `--require-hashes --only-binary=:all:`; the drawing generator has its own separate pinned environment.

## Use the workflow

[Workflow instructions](docs/workflow.md) cover `prepare-input`, `extract`, `import-response`, `validate`, `export`, and `evaluate`, including exact commands and exit codes. Input support is PNG/JPEG. PDF, animation and images above 30 million pixels are explicitly unsupported.

For assisted VLM interpretation, use a **new ChatGPT session** with only the prepared `image.png` and generic `prompt.txt`. Keep reference answers, source vectors, generator code, manifests and prior generation history outside the session. Preserve the original response before corrections. No live VLM response has been obtained, and no API integration is configured. Image upload and response transfer remain manual stages.

The classical method reads tag pixels, associates text above symbol boxes, and classifies supported tank, valve and pump outlines. It preserves original OCR errors and suffix-crop readings; agreement between repeated views is correlated evidence, with confidence unknown. It never repairs a label from the reference answer. `--method baseline` leaves tags null and types unknown.

A fresh worker, two-file packet allowlist, pre/post hashes and a Python file-open guard reduce accidental reference access in trusted code. Windows OCR receives decoded image pixels. The worker keeps crop evidence in separate scratch storage. The guard applies to Python file opens in the trusted worker.

## Files and conventions

- [Primary synthetic image](data/input/four_component.png) and [separate reference](data/reference/four_component.reference.json).
- [Canonical schema](schemas/extraction-v1.schema.json) and [software examples](data/examples/). Example predictions test plumbing; they are not inference results.
- [Drawing package](output/aip_four_component/README.txt), including editable SVG, deterministic generator, source citations and reference annotations.
- [Generic extraction prompt](prompts/extract_from_image.txt), [results and limits](docs/results.md), and [spring handoff](docs/spring_handoff.md).

Edges mean immediate undirected equipment adjacency. A route through another component is not a direct edge. Tags are normalized deterministically without reference-based OCR repair. Unknowns and invalid outputs remain visible. Boxes use original-image pixels, a top-left origin and half-open `[xmin,ymin,xmax,ymax]` bounds. See the workflow for matching rules and metrics.

The simplified tank, valve and pump shapes derive from public DOE handbook figures linked in the drawing package. No ISA, DOE, DEXPI or plant-standard compliance is claimed. Implementation changes are reviewed through pull requests in [BoccerSoss/Senior_Design](https://github.com/BoccerSoss/Senior_Design).
