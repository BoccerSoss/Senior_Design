# AIP V2 drawing workbench

AIP V2 is a local browser interface for importing P&IDs, reviewing equipment and piping, assigning visible sheet grid coordinates, following confirmed connections between sheets, and exploring candidate isolation boundaries. It reuses the project's image extraction pipeline and adds a local SQLite workspace. The original `Open AIP Demo.cmd` remains available separately.

## Open the workbench

1. In the GitHub repository, select the branch containing V2 (`codex/aip-v2` while unmerged), then choose **Code → Download ZIP**. In File Explorer, choose **Extract All** and open the project folder inside the extracted folder. A Git checkout also works.
2. Install **64-bit Python 3.12** with pip if it is not already installed. Windows 10/11 with an installed Windows OCR language is the supported local extraction platform.
3. Double-click **Open AIP V2.cmd**. First launch creates `.venv` if needed and installs the hash-locked packages in `requirements-v2.lock`. This setup step needs package download access.
4. The workbench opens at **http://127.0.0.1:8765**. Keep its console window open. To stop the server, press **Ctrl+C in that console**. Closing only the browser tab leaves the server running.

This launcher runs the repository's Python server and opens a normal browser tab. It does not control or record the desktop. The server binds only to this computer's loopback interface; the project does not publish or host a website.

For PowerShell setup or a different port:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements-v2.lock
.\.venv\Scripts\python.exe scripts/aip_v2.py --port 8765
# Run without automatically opening a browser:
.\.venv\Scripts\python.exe scripts/aip_v2.py --port 8766 --no-browser
```

The default local data directory is `runs/v2`. The `--data PATH` option selects another workspace. Store files, imported images, extraction evidence, AI responses and local correction/audit history are runtime data and are excluded from Git. Keep a backup of the entire workspace directory while the application is stopped.

## Demonstrate the connected system

1. Choose **Load demonstration → Connected two-sheet system**. The separate **Synthetic research loop** facility contains two generated PNG drawings, `RL-001` and `RL-002`, with matching printed symbols, names, piping and graph records. Loading it again reopens the existing records.
2. On `RL-001`, click **P-101**, then **Trace connections**. Inspect the local branch and follow the confirmed `X-A`/`X-B` continuation to `RL-002`. Use the scroll wheel or **+ / −** to zoom, drag to pan, and **Fit** to reset the view.
3. Choose **Scope isolation**. The candidate boundaries include **V-101, V-102, V-201 and V-202**. V-201 is drawn normally closed; that recorded position still needs verification. CV-201 is a check valve and receives no boundary credit under the recorded default rules.
4. Review the drain, vent, relief and open-end items. The example deliberately has incomplete special paths. It demonstrates why finding several boundary valves does not establish a complete clearance.
5. Open **Facility graph** to compare drawing links with the equipment network. Click graph items to return to the source sheet. Open **Grid** to inspect each equipment item's spatial cell.

The synthetic graph is generated with known records for demonstrating the interface and algorithms. It is not evidence of automatic recognition accuracy on an unseen drawing. The **Beginner ISLOCA drawings** demonstration uses locally available beginner PNGs when present; otherwise import those PNGs with **Import drawings**. Printed names in an extraction are read from the image rather than populated from a list of expected tags.

## Grid coordinates

The separate **Grid** tab displays a uniform rectangular grid over the actual drawing. Set **Columns** and **Rows**, then choose **Apply grid**. The origin is the top-left cell **(1,1)**; columns increase to the right and rows increase downward. Each equipment record receives the cell containing the center of its marked symbol box. Coordinates appear with equipment labels and in the equipment locations list, and **Export equipment coordinates** writes JSON.

For example, the two-sheet demonstration uses 14 columns and 10 rows. P-101 is at **(6,5)** on RL-001. These are drawing grid references, not physical distances, pixel values or a count of equipment along a flow path. A branch does not restart the grid: each branch's equipment keeps its spatial location on the same sheet. Include the drawing/sheet ID with a coordinate because the same cell can occur on every sheet. Several nearby items can share a cell; increase the grid resolution when useful. Changing the grid does not change piping connectivity.

The source bounding boxes and polyline points remain available internally for placing marks on the image. They are separate from the grid coordinates shown to a reviewer. V2 generates its grid from the configured row and column counts; it does not claim to read an existing drawing-border zone standard automatically.

## Import and correct drawings

Choose a facility, then **Import drawings**. Supported input includes PNG, JPEG, TIFF, WebP, BMP, PDF and ZIP drawing collections. **PDF pages are rendered to PNG before extraction**. A multipage PDF or image becomes one sheet per page/frame. Jobs show progress and per-file errors; repeat imports of the same source page in the same facility reuse the existing sheet.

An optional UTF-8 index CSV can provide `file,page,drawing,sheet,revision,title` columns. For example:

```csv
file,page,drawing,sheet,revision,title
research-loop.pdf,1,LOOP-01,1,A,Pump supply
research-loop.pdf,2,LOOP-02,2,A,Return branches
```

Uploaded files are limited to 150 MB per request/file, expanded ZIP contents to 600 MB, and raster images to 30 million pixels. Reports and drawings with unknown symbols or ambiguous crossings can remain incomplete after local extraction.

Use the source image to review each observation:

- Select equipment to correct its printed name, type, normal position, review state and boundary credit, then choose **Save correction**. An eligible manual boundary needs a confirmed type/review and explicit eligible credit.
- Use **+ Equipment** to drag a box around missed equipment. **Move** adjusts a mark's location. Changes update its grid cell.
- Use **+ Pipe** or **+ Signal** to choose two equipment endpoints and add intermediate bend points. A crossing becomes a branch only when the graph records a junction and its connections; intersecting drawn lines alone do not establish a connection.
- In **Links**, compare both off-page endpoints before confirming a continuation. Suggested matches remain gaps until confirmed. Links stay within one facility.
- Use **Run local extraction** to read a sheet again. Saved corrections are retained where records can be reconciled, and the **Audit** view records local edits. Review the result after reanalysis.

DGN import requires a separately installed converter. Set `AIP_DGN_CONVERTER` to an executable that accepts **input.dgn output.pdf** as its two arguments. V2 then renders that PDF to images. There is no bundled DGN engine; exporting a PDF from an authorized CAD tool is another supported route.

## Optional AI and assistant connections

The workbench starts with **Local graph queries**, which answer from recorded drawings and connections without calling an AI service. In **Settings**, an optional provider can use the OpenAI Responses API, Anthropic Messages API, or an approved OpenAI-compatible gateway/local model. Enter the model ID your endpoint supports. The code does not assume a model name or supply an institutional entitlement.

API credentials come from the launcher process environment: `OPENAI_API_KEY` for OpenAI or an authenticated compatible endpoint, and `ANTHROPIC_API_KEY` for Anthropic. **Do not paste credentials into the repository, index CSV, drawing labels or Settings fields.** Configure credentials through your approved environment mechanism, restart the launcher, then use **Refresh status**. An ORNL ChatGPT license/subscription sign-in is separate from API access and does not automatically provide these credentials. Gateway/local model base URLs and model IDs can also be supplied with `AIP_AI_BASE_URL` and `AIP_AI_MODEL` when the corresponding Settings fields are empty.

Selecting or saving a provider does not send drawings. Submitting a question sends the supplied graph context to the selected endpoint; **Extract with configured AI** sends the selected drawing PNG. Use only an endpoint and drawing data permitted for that work. AI observations start unreviewed and must be checked against the image. The UI retains uncertain readings and incomplete links.

Compatible loopback model servers run without a cloud API key; the adapter does not forward `OPENAI_API_KEY` to loopback. Authenticated local model servers need a separately designed credential setup. Local graph queries need no model server or credentials. See [the verification record](V2_VERIFICATION.md) for the actual beginner PNG and PDF results.

**Settings → Show connection details** displays this workspace's authenticated REST/MCP endpoints and its local API key. Compatible clients on the same computer can use the read-only tools to search drawings, find a component, follow an off-page flag, trace lines, view a source region or request a candidate isolation boundary. Keep that API key private. A cloud assistant cannot directly reach this computer's loopback address; any institutional bridge or deployment is separate work. The local reviewer name records an audit label rather than authenticating distinct team members.

## Candidate isolation rules and limits

V2 traces recorded process connections as an **undirected graph**. It follows only confirmed same-facility sheet continuations and stops at the first confirmed eligible manual valve on each observed path. Check, motor/power and automatic valves receive no boundary credit under the recorded defaults. Signal connections are excluded from isolation traversal. Recorded normal positions describe the drawing; they are not live equipment state or measured flow direction.

Results include boundary candidates, drains, vents, relief paths, noncredited items, missing endpoints, unconfirmed links and the rule assumptions used. An **incomplete** status remains when observations or paths need review. A **candidate** status means the observed graph met these prototype rules, and still requires responsible engineering review. Missing drawing content cannot be proven absent by traversal.

This research prototype does not certify a P&ID, validate operability, authorize valve operation, produce an approved clearance, establish double-block/bleed adequacy, or demonstrate compliance with a particular facility procedure. The default rules are a configurable conservative demonstration, not an asserted national-laboratory isolation standard. Production use would require approved site-specific rules, validated drawing interpretation, access control and independent verification.

The handoff archive includes public source, tests and synthetic/reference assets. It excludes runtime drawing workspaces, credentials and private course/AI logs.
