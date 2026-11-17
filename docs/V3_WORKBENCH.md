# V3 drawing workbench

## Try it on a drawing

1. On GitHub, select the `codex/aip-v3` branch and choose **Code → Download ZIP**. Extract the ZIP and double-click **Open AIP V3.cmd** inside the project folder. The first launch installs the pinned dependencies for Windows x64 and Python 3.12. Keep the launcher open while using the demo.
2. Choose **Open example → Four-component example**, or **Import drawings** for your own sheet. A PDF is rendered to PNG first. V3 opens on port 8766 and saves its local workspace in `runs/v3`.
3. Choose **Find connections with AI**, select **Local connection model**, and click **Trace drawing**. Click a traced line to inspect its endpoints, evidence and recorded direction. The model uses image-derived equipment boxes, OCR and a learned classifier for pipe strokes. It checks straight runs and single elbows. Equipment missed by the detector can break a valid connection or allow a false shortcut.
4. Under **Connectivity matrix**, use the method selector to compare the direct equipment pairs. The latest successful V3 trace for each sheet persists separately from V2 and its edits. The Grid tab assigns coordinates on the original drawing for the selected method. Choose **Benchmark results** to inspect the scored comparisons.
5. Choose **Ask about drawing → Local graph lookup** and enter **What connects to V-101?** on the four-component example. Click the cited connection to see the evidence on the drawing. Answers describe the selected extraction and its review status.

## Ask GPT or Claude in the workbench

In **Ask about drawing**, choose **OpenAI (GPT)** or **Claude (Anthropic)**. Open the session connection form and enter the model ID and provider API key issued for your account. The server keeps this configuration in memory until it stops. Select an API model with image support if you also want to trace drawings with it.

When you submit a question, the selected provider receives the question and the drawing's recorded facts. A model selects supporting facts and the workbench renders the answer from those records, with clickable citations. When you choose a hosted provider under **Find connections with AI**, that action sends the selected sheet image. A hosted vision result must pass endpoint and evidence checks before it appears in the candidate matrix.

For the ORNL environment, use a model endpoint and credentials provided for the project. A browser sign-in alone does not configure the connector. These startup environment variables are an alternative to session entry.

| Provider | Model ID | Credential | API base |
| --- | --- | --- | --- |
| OpenAI | `AIP_OPENAI_MODEL` | `OPENAI_API_KEY` | Fixed OpenAI Responses endpoint |
| Anthropic | `AIP_ANTHROPIC_MODEL` | `ANTHROPIC_API_KEY` | Fixed Anthropic Messages endpoint |
| Organization gateway | `AIP_GATEWAY_MODEL` | `AIP_GATEWAY_API_KEY` if required | `AIP_GATEWAY_BASE_URL`, including the API prefix |

The gateway must support OpenAI-compatible chat completions. The local tracing model and local graph lookup are available without credentials. A listed hosted profile indicates that configuration is present. The first request checks actual access to the provider and model.

### If a model request returns HTTP 429

V3 displays the provider's recognized error code. For a temporary rate limit, wait for the displayed interval before trying again. For insufficient quota, an exhausted credit balance, or an enforced spending or usage limit, ask the administrator of the API project attached to the key to check the specified limit. For ORNL work, confirm the key belongs to the intended lab project. An unspecified 429 leaves the cause undetermined. [OpenAI's 429 troubleshooting guide](https://help.openai.com/en/articles/5955604-troubleshooting-api-rate-limits-and-429-errors)

The local demo remains available under **Ask about drawing → Local graph lookup** or **Find connections with AI → Local connection model**.

## Ask from an external client

**Claude Desktop.** Start V3 and open **Setup → Connect an external AI client → View Claude Desktop configuration**. In Claude Desktop, open **Settings → Developer → Edit Config**. Add the displayed `aip-drawings` entry to the existing `mcpServers` object, save, and restart Claude Desktop. The entry launches a local Python bridge. Keep the workbench running and ask Claude to list the drawings and read the V3 direct connections on a selected sheet. The bridge obtains its local access token from the running workbench. [Local MCP client setup](https://modelcontextprotocol.io/docs/develop/connect-local-servers)

**Hosted ChatGPT.** For questions on this computer, the in-app OpenAI option calls the Responses API. Connecting the hosted ChatGPT interface itself requires workspace support and a reachable MCP service with compatible authentication. This release runs on loopback. [ChatGPT connector setup](https://developers.openai.com/plugins/deploy/connect-chatgpt)

The read-only tools `list_drawings` and `drawing_connections` expose saved V2 or V3 equipment pairs, evidence paths, review flags and known directions. External clients decide which returned facts to present. Clearance status and approved procedures require separate sources.
