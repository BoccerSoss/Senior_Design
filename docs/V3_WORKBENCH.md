# V3 drawing workbench

## Try it on a drawing

1. On GitHub, select the `codex/aip-v3` branch and choose **Code → Download ZIP**. Extract the ZIP and double-click **Open AIP V3.cmd** inside the project folder. The first launch installs the pinned dependencies for Windows x64 and Python 3.12. Keep the launcher open while using the demo.
2. Choose **Open example → Beginner ISLOCA drawings** to see all six full-page PNGs (pages 65–70) in the drawing library. The left-hand **Drawing library** shows every saved collection together. Click a drawing under another collection to switch without leaving the current tab. The sheets are bundled with the demo. A fresh workspace opens on this collection. **Four-component example** opens the synthetic control, and **Import drawings** accepts additional files. Imported PDFs are rendered to PNG first.
3. Select **V3** in **Method**, choose **Find connections with AI**, select **Local connection model**, and click **Trace drawing**. Click a traced line to inspect its endpoints, evidence and recorded direction. The model uses image-derived equipment boxes, OCR and a learned classifier for pipe strokes. It checks straight runs and single elbows. Equipment missed by the detector can break a valid connection or allow a false shortcut.
4. Under **Connectivity matrix**, use the method selector to compare the direct equipment pairs. The latest successful V3 trace for each sheet persists separately from V2 and its edits. Choose **Benchmark results** to inspect the scored comparisons.
5. Choose **Ask about drawing → Local graph lookup** and enter **What connects to V-101?** on the four-component example. Click the cited connection to see the evidence on the drawing. Answers describe the selected extraction and its review status.

In **V2**, **Read with symbol reference** uses checked symbol types and locations on matching PNGs and reads the printed names from the image. The saved result appears under **V2 · Reference-assisted**. The **V3** option displays the saved trace’s provider, such as **V3 · Local model** or **V3 · ChatGPT subscription**. The comparison benchmark uses image-only runs.

In **Drawing → V3**, click a symbol or a row under **Components** to see its equipment tag, type, and AI evidence. The orange dashed box marks the text region when OCR returned one. Edit the tag or type and select **Save correction**. Select **Mark reviewed** to acknowledge the item after checking it. The original AI prediction stays available in the evidence panel. The matrix and grid use the saved corrections.

Open **Review** on the right to see flagged observations such as unread names, unknown types, and ambiguous or unsupported arrow claims. Select a flagged row to inspect and correct it directly. In **Connections**, select a line to see whether the arrowhead was on the segment or elsewhere on its uninterrupted run. One terminal arrow in `A—B—C→` establishes both `A→B` and `B→C`. You can correct the direction and connection type from that view. The direction remains unknown when V3 cannot link a usable arrow to the run.

**View saved trace** reopens a previous result immediately. Repeating a local trace reuses the result when the PNG, model and workbench version match. Select **Recompute the local trace from the image** for a fresh timing. To replace a trace with saved reviews, select the confirmation in the trace dialog. A successful replacement archives the reviewed result in the workspace database. Hosted tracing sends a new request when you choose **Trace drawing**. The demo runs at `http://127.0.0.1:8766` and stores your workspace in `runs/v3`.

## Find equipment on the grid

Open **Grid** and select the V2 or V3 extraction you want to inspect. **Regular grid** overlays the original drawing with evenly spaced lines. An equipment coordinate such as **(3, 2)** means the symbol center is nearest the third column and second row, counting from the upper left. Coordinates appear on the grid automatically and beside the names in the Drawing component list. Nearby symbols can share a coordinate. Select equipment under **Equipment locations** to find its symbol on the drawing.

Choose **Equipment centers** to place lines through the exact centers of the detected equipment. The coordinate then gives the column and row among those lines. **Export JSON** saves the axes and equipment coordinates for the selected layout. To check a coordinate inside the demo, open **Ask about drawing**, select **Local graph lookup** and ask “Where is V-101 on the grid?” on the four-component example. [Grid conventions and exported fields](V2_WORKBENCH.md#use-the-separate-grid-tool)

## Update an existing copy

1. Close the old workbench using **Ctrl+C** in its launcher window. Saved drawings, corrections, matrices and traces live under `runs\v3`.
2. If you installed from a ZIP, visit the [V3 branch](https://github.com/BoccerSoss/Senior_Design/tree/codex/aip-v3), choose **Code → Download ZIP**, then **Extract All** into a new folder. To keep your drawings, copy the old `runs\v3` folder to the same location inside the newly extracted project before you launch. If you use Git and already have the V3 branch checked out, run `git pull --ff-only` there.
3. Double-click **Open AIP V3.cmd** in the updated folder. Refresh the workbench browser tab if it was already open.

## Use your ChatGPT subscription

Open the four-component example and choose **Ask about drawing → ChatGPT subscription → Continue with ChatGPT**. Click **Open OpenAI sign-in**, finish sign-in and consent, then return to AIP. Choose an available **Plan model**, enter **What connects to V-101?**, and click **Ask model**. Click a cited connection to inspect its source. For image analysis, select **V3** in **Method**, choose **Find connections with AI → ChatGPT subscription → Trace drawing**, then open **View matrix**.

Plan sharing depends on the account and workspace. OpenAI lists eligible Plus and Pro accounts. Review the allowance or credits you authorize in ChatGPT's usage settings. AIP relies on the model list returned for that account. Windows protects the saved connection for the current user under `%LOCALAPPDATA%\AIP\ChatGPT`. [OpenAI plan sign-in](https://developers.openai.com/siwc/quickstart) · [Manage plan usage](https://chatgpt.com/settings/usage)

**If your workspace cannot connect**, choose **ChatGPT in your browser** in the model list. For questions, click **Copy question and matrix**, then **Open ChatGPT**, select the intended workspace, and paste. For a visual check, use **Download drawing PNG** and attach it to your chat. A response in that chat stays there. To trace locally, select **V3** in **Method** and choose **Find connections with AI → Local connection model → Trace drawing**.

## Use a provider API key

In **Ask about drawing**, choose **OpenAI (API key)** or **Claude (Anthropic)**. Open the session connection form and enter the model ID and provider API key issued for your account. The server keeps this configuration in memory until it stops. Select an API model with image support if you also want to trace drawings with it.

When you submit a question, the selected provider receives the question and the drawing's recorded facts. A model selects supporting facts and the workbench renders the answer from those records, with clickable citations. When you choose a hosted provider under **Find connections with AI**, that action sends the selected sheet image. A hosted vision result must pass endpoint and evidence checks before it appears in the candidate matrix.

For an ORNL-provided gateway, use the project endpoint and its credentials. These startup environment variables are an alternative to entering API credentials in the workbench.

| Provider | Model ID | Credential | API base |
| --- | --- | --- | --- |
| OpenAI | `AIP_OPENAI_MODEL` | `OPENAI_API_KEY` | Fixed OpenAI Responses endpoint |
| Anthropic | `AIP_ANTHROPIC_MODEL` | `ANTHROPIC_API_KEY` | Fixed Anthropic Messages endpoint |
| Organization gateway | `AIP_GATEWAY_MODEL` | `AIP_GATEWAY_API_KEY` if required | `AIP_GATEWAY_BASE_URL`, including the API prefix |

The gateway must support OpenAI-compatible chat completions. The local tracing model and local graph lookup are available without credentials. A listed hosted profile indicates that configuration is present. The first request checks actual access to the provider and model.

### If a model request returns HTTP 429

V3 displays the provider's recognized error code. For a temporary rate limit, wait for the displayed interval before trying again. For insufficient quota, an exhausted credit balance, or an enforced spending or usage limit, ask the administrator of the API project attached to the key to check the specified limit. For ORNL work, confirm the key belongs to the intended lab project. An unspecified 429 leaves the cause undetermined. [OpenAI's 429 troubleshooting guide](https://help.openai.com/en/articles/5955604-troubleshooting-api-rate-limits-and-429-errors)

For local questions, choose **Ask about drawing → Local graph lookup**. For local tracing, select **V3** in **Method**, then choose **Find connections with AI → Local connection model**.

## Ask from an external client

**Claude Desktop.** Start V3 and open **Setup → Connect an external AI client → View Claude Desktop configuration**. In Claude Desktop, open **Settings → Developer → Edit Config**. Add the displayed `aip-drawings` entry to the existing `mcpServers` object, save, and restart Claude Desktop. The entry launches a local Python bridge. Keep the workbench running and ask Claude to list the drawings and read the V3 direct connections on a selected sheet. The bridge obtains its local access token from the running workbench. [Local MCP client setup](https://modelcontextprotocol.io/docs/develop/connect-local-servers)

**Hosted ChatGPT.** Use the browser-copy workflow above for an ordinary ChatGPT conversation. Connecting ChatGPT through MCP requires workspace support and a reachable MCP service with compatible authentication. This release runs on loopback. [ChatGPT connector setup](https://developers.openai.com/plugins/deploy/connect-chatgpt)

The read-only tools `list_drawings` and `drawing_connections` expose saved V2 or V3 equipment pairs, evidence paths, review flags and known directions. External clients decide which returned facts to present. Clearance status and approved procedures require separate sources.
