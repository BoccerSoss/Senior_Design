"use strict";

function remembered(name, fallback = "") { try { return localStorage.getItem(`aip-v2-${name}`) || fallback; } catch { return fallback; } }
function remember(name, value) { try { localStorage.setItem(`aip-v2-${name}`, value); } catch { /* Storage may be disabled by the browser. */ } }
const app = {
  data: { facilities: [], sheets: [], links: [], jobs: [], audit: [], settings: {} },
  facilityId: remembered("facility"), sheetId: "", componentId: "", connectionId: "", view: remembered("view", "drawings"), tab: "selection",
  tool: "select", search: "", gridVisible: true, gridLabels: true, labels: true, pipes: true, signals: true,
  scale: 1, offset: [0, 0], image: null, imageKey: "", pointer: null, draft: null,
  trace: null, isolation: null, messages: [], graphMode: "sheets", assistantBusy: false,
  poll: null, toastTimer: null, integration: null, gridData: {}, initial: true,
};
const $ = (selector, root = document) => root.querySelector(selector);
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const dataValue = value => esc(value ?? "");
const selectedSheet = () => app.data.sheets.find(sheet => sheet.id === app.sheetId);
const selectedComponent = () => selectedSheet()?.components?.find(component => component.id === app.componentId);
const activeSheets = () => app.data.sheets.filter(sheet => sheet.facility_id === app.facilityId);
const componentName = component => component?.tag || component?.label || component?.observed_tag || component?.normalized_tag || `Unnamed ${component?.type || "component"}`;
const sheetName = sheet => sheet?.drawing_number || sheet?.title || sheet?.filename || "Untitled drawing";
const norm = value => String(value ?? "").toLowerCase();
const pointCenter = component => component.bbox ? [(component.bbox[0] + component.bbox[2]) / 2, (component.bbox[1] + component.bbox[3]) / 2] : [0, 0];
const endpointId = endpoint => endpoint?.component_id ?? endpoint?.component;
const isConnector = component => ["connector","off page connector","line continuation"].includes(norm(component?.type).replaceAll("-"," ").replaceAll("_"," "));
const evidenceText = value => typeof value === "object" ? value.method || value.description || JSON.stringify(value) : value;

function toast(message, error = false) {
  clearTimeout(app.toastTimer);
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast${error ? " error" : ""}`;
  element.hidden = false;
  app.toastTimer = setTimeout(() => { element.hidden = true; }, error ? 9000 : 4500);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const type = response.headers.get("content-type") || "";
  const body = type.includes("json") ? await response.json() : { error: await response.text() };
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

async function action(name, payload = {}, { refresh = true, message = "" } = {}) {
  const output = await api("/api/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: name, ...payload }) });
  if (output.state) setState(output.state);
  else if (refresh) await loadState();
  if (message) toast(message);
  return output;
}

function facilitiesMarkup(selected = app.facilityId) {
  return app.data.facilities.map(facility => `<option value="${dataValue(facility.id)}"${facility.id === selected ? " selected" : ""}>${esc(facility.name)}</option>`).join("");
}

function setState(data) {
  app.data = { facilities: [], sheets: [], links: [], jobs: [], audit: [], settings: {}, ...data };
  if (!app.data.facilities.some(facility => facility.id === app.facilityId)) app.facilityId = app.data.facilities[0]?.id || "";
  remember("facility", app.facilityId);
  const sheets = activeSheets();
  if (!sheets.some(sheet => sheet.id === app.sheetId)) {
    app.sheetId = sheets[0]?.id || "";
    app.componentId = app.connectionId = "";
    app.imageKey = "";
  }
  if (app.componentId && !selectedComponent()) app.componentId = "";
  $("#facility").innerHTML = facilitiesMarkup();
  $("#workspace-description").textContent = `${sheets.length} drawing${sheets.length === 1 ? "" : "s"} · ${sheets.reduce((count, sheet) => count + (sheet.components?.length || 0), 0)} components`;
  render();
  schedulePoll();
}

async function loadState() {
  const state = await api(`/api/state${app.facilityId ? `?facility_id=${encodeURIComponent(app.facilityId)}` : ""}`);
  setState(state);
}

function pendingJobs() {
  return app.data.jobs.filter(job => ["queued", "pending", "running", "processing", "analyzing"].includes(job.status));
}

function schedulePoll() {
  clearTimeout(app.poll);
  if (pendingJobs().length) app.poll = setTimeout(async () => {
    try { await loadState(); } catch (error) { toast(error.message, true); }
  }, 1800);
}

function render() {
  remember("view", app.view);
  document.querySelectorAll("[data-view]").forEach(button => button.classList.toggle("active", button.dataset.view === app.view));
  if (app.view === "drawings" || app.view === "grid") renderDrawings();
  else if (app.view === "graph") renderGraph();
  else if (app.view === "assistant") renderAssistant();
  else if (app.view === "audit") renderAudit();
  else renderSettings();
  app.initial = false;
}

function filteredSheets() {
  const query = norm(app.search).trim();
  return activeSheets().filter(sheet => !query || norm(`${sheetName(sheet)} ${sheet.title} ${sheet.filename} ${sheet.revision} ${(sheet.components || []).map(componentName).join(" ")}`).includes(query));
}

function libraryMarkup() {
  const sheets = filteredSheets();
  const jobs = app.data.jobs.filter(job => !job.facility_id || job.facility_id === app.facilityId).slice(-4);
  return `<aside class="library">
    <div class="library-heading"><h2>Drawing library</h2><span class="muted tiny">${activeSheets().length}</span></div>
    <input class="search-input" id="sheet-search" type="search" value="${dataValue(app.search)}" placeholder="Find a drawing or equipment tag" aria-label="Find a drawing or equipment tag">
    <div class="sheet-list">${sheets.length ? sheets.map(sheet => `<button class="sheet-row${sheet.id === app.sheetId ? " active" : ""}" data-action="open-sheet" data-id="${dataValue(sheet.id)}"><div class="sheet-title"><span>${esc(sheetName(sheet))}</span><small>${sheet.active===false?"Archived · ":""}${sheet.revision ? `Rev ${esc(sheet.revision)}` : ""}</small></div><div class="sheet-caption">${esc(sheet.title || sheet.filename || "")}</div><div class="sheet-count">${sheet.components?.length || 0} components · Sheet ${esc(sheet.sheet_number || sheet.page || 1)}</div></button>`).join("") : `<p class="section-note" style="padding:10px">${app.search ? "No matching drawings." : "Import a drawing set or load a demonstration."}</p>`}</div>
    ${jobs.length ? `<div class="jobs">${jobs.map(job => `<div class="job-row${["failed", "error"].includes(job.status) ? " error" : ""}"><strong>${esc(job.title || job.name || job.type || "Import")}</strong><br>${esc(job.error || job.message || job.status)}${typeof job.progress === "number" ? ` · ${Math.round(job.progress)}%` : ""}${job.messages?.length?`<div class="tiny">${job.messages.map(esc).join("<br>")}</div>`:""}</div>`).join("")}${pendingJobs().length ? '<div class="busy-progress"></div>' : ""}</div>` : ""}
    <div class="library-footer"><a href="/reference" target="_blank" rel="noopener">Symbol reference library ↗</a></div>
  </aside>`;
}

function renderDrawings() {
  const sheet = selectedSheet();
  const gridMode = app.view === "grid";
  $("#main").innerHTML = `<div class="drawing-layout">${libraryMarkup()}
    <section class="viewer">
      ${sheet ? `<div class="viewer-heading"><div><h2>${esc(sheetName(sheet))}</h2><div class="muted">${esc(sheet.title || sheet.filename || "")} ${sheet.revision ? `· Revision ${esc(sheet.revision)}` : ""}</div></div><button class="button small sheet-menu" data-action="sheet-settings">Sheet details</button></div>
      <div class="viewer-toolbar">
        ${gridMode ? `<span class="section-note">Spatial drawing grid</span>` : `<button class="tool-button${app.tool === "select" ? " active" : ""}" data-tool="select" title="Select equipment or pan">Select</button><button class="tool-button${app.tool === "component" ? " active" : ""}" data-tool="component" title="Drag a box around missing equipment">+ Equipment</button><button class="tool-button${app.tool === "pipe" ? " active" : ""}" data-tool="pipe" title="Select two components to connect">+ Pipe</button><button class="tool-button${app.tool === "signal" ? " active" : ""}" data-tool="signal">+ Signal</button><button class="tool-button${app.tool === "move" ? " active" : ""}" data-tool="move">Move</button>`}
        <span class="toolbar-divider"></span><button class="tool-button" data-action="zoom-out" title="Zoom out">−</button><span class="zoom-label" id="zoom-label"></span><button class="tool-button" data-action="zoom-in" title="Zoom in">+</button><button class="tool-button" data-action="fit">Fit</button>
        <label class="layer-toggle"><input type="checkbox" data-layer="${gridMode ? "gridLabels" : "labels"}"${(gridMode ? app.gridLabels : app.labels) ? " checked" : ""}>Labels</label>
        ${!gridMode ? `<label class="layer-toggle"><input type="checkbox" data-layer="pipes"${app.pipes ? " checked" : ""}>Pipes</label><label class="layer-toggle"><input type="checkbox" data-layer="signals"${app.signals ? " checked" : ""}>Signals</label>` : `<label class="layer-toggle"><input type="checkbox" data-layer="gridVisible"${app.gridVisible ? " checked" : ""}>Grid</label>`}
      </div>
      <div class="canvas-wrap"><canvas class="drawing-canvas" id="drawing-canvas" aria-label="Interactive drawing. Scroll to zoom, drag to pan and click equipment to inspect." tabindex="0"></canvas><div class="canvas-caption" id="canvas-caption">${gridMode ? "Grid cells use column, row from the top-left of the sheet" : toolHint()}</div></div>
      <div class="viewer-legend"><span><i class="legend-line"></i>Process pipe</span><span><i class="legend-line signal"></i>Signal</span><span><i class="legend-line isolation"></i>Candidate boundary</span></div>` : `<div class="empty-workspace"><h1>Connected drawings, one workspace</h1><p>Import a drawing set to inspect equipment, review connections and explore candidate isolation boundaries across sheets.</p><div class="actions"><button class="button primary" data-action="import">Import drawings</button><button class="button" data-action="show-demo">Try demonstration</button></div></div>`}
    </section>
    <aside class="inspector">${gridMode ? `<div class="inspector-content">${gridPanelMarkup()}</div>` : `<div class="inspector-tabs">${["selection", "trace", "isolation", "links"].map(tab => `<button data-tab="${tab}" class="${app.tab === tab ? "active" : ""}">${({selection: "Equipment",trace: "Trace",isolation: "Isolation",links: "Links"})[tab]}</button>`).join("")}</div><div class="inspector-content">${inspectorMarkup()}</div>`}</aside>
  </div>`;
  if (sheet) { setupCanvas(); if (gridMode) loadGrid(sheet); }
}

function toolHint() {
  return { select: "Scroll to zoom · Drag to pan · Click to inspect", component: "Drag a rectangle around the equipment symbol", pipe: "Click the first component, then the next. Click a blank point to add a bend.", signal: "Click both signal endpoints. Click blank space to add a bend.", move: "Drag equipment to adjust its box" }[app.tool];
}

function gridPosition(component, sheet = selectedSheet()) {
  const stored = app.gridData[sheet?.id];
  if (stored && stored.version === sheet?.version) {
    const coordinate = stored.coordinates?.find(item => item.component_id === component.id)?.coordinate;
    if (coordinate) return coordinate;
  }
  const grid = sheet?.grid || {};
  const [x, y] = pointCenter(component);
  const columns = Number(grid.columns) || 12, rows = Number(grid.rows) || 16;
  return [Math.min(columns, Math.max(1, Math.floor(x / sheet.width * columns) + 1)), Math.min(rows, Math.max(1, Math.floor(y / sheet.height * rows) + 1))];
}

async function loadGrid(sheet) {
  if (app.gridData[sheet.id]?.version === sheet.version) return;
  try {
    const result = await api(`/api/grid?sheet_id=${encodeURIComponent(sheet.id)}`);
    app.gridData[sheet.id] = {...result,version:sheet.version};
    if (app.view === "grid" && app.sheetId === sheet.id) drawCanvas();
  } catch (error) { toast(error.message,true); }
}

function gridPanelMarkup() {
  const sheet = selectedSheet();
  if (!sheet) return '<p class="empty-selection">Open a drawing to set up its grid.</p>';
  const grid = sheet.grid || {};
  return `<div class="eyebrow">Drawing coordinates</div><h2>Spatial grid</h2><p class="muted tiny">Every equipment item has a cell based on its center on the sheet. Branches keep their position on the same grid.</p>
    <form id="grid-form"><div class="form-grid"><label class="field">Columns<input name="columns" type="number" min="1" max="200" required value="${Number(grid.columns) || 12}"></label><label class="field">Rows<input name="rows" type="number" min="1" max="200" required value="${Number(grid.rows) || 16}"></label></div><p class="section-note">Origin at the top-left. Columns increase to the right and rows increase downward. Grid settings do not change pipe connections or isolation results.</p><button class="button primary wide" type="submit">Apply grid</button></form>
    <a class="button wide" style="margin-top:9px" href="/api/export?sheet_id=${encodeURIComponent(sheet.id)}&format=grid" download>Export equipment coordinates</a><hr class="section-divider"><h3>Equipment locations</h3><div class="result-list">${(sheet.components || []).map(component => { const [col,row] = gridPosition(component,sheet); return `<div class="result-item"><div><button class="result-title" data-action="select-component" data-id="${dataValue(component.id)}">${esc(componentName(component))}</button><div class="result-detail">${esc(component.type)} · (${col},${row})</div></div></div>`; }).join("") || '<p class="section-note">Run extraction or add equipment to assign locations.</p>'}</div>`;
}

function selectionMarkup() {
  const sheet = selectedSheet();
  const component = selectedComponent();
  if (app.connectionId && sheet) {
    const connection = sheet.connections.find(edge => edge.id === app.connectionId);
    if (connection) return `<div class="eyebrow">Connection</div><h2>Review connection</h2><p>${esc(componentName(sheet.components.find(item => item.id === connection.source)))} ↔ ${esc(componentName(sheet.components.find(item => item.id === connection.target)))}</p><form id="connection-form"><label class="field">Line kind<select name="kind">${options(["pipe","signal","unknown"],connection.kind || "unknown")}</select></label><label class="field">Recorded direction<select name="direction">${[{id:"unknown",label:"Unknown"},{id:"source_to_target",label:"First → second"},{id:"target_to_source",label:"Second → first"},{id:"bidirectional",label:"Bidirectional"}].map(item=>`<option value="${item.id}"${connection.direction===item.id?" selected":""}>${item.label}</option>`).join("")}</select></label><label class="field">Review state<select name="review_status">${options(["unreviewed","confirmed"],connection.review_status || "unreviewed")}</select></label><p class="section-note">Direction records drawing evidence. Isolation review considers every connected process path.</p><button class="button primary" type="submit">Save connection</button></form><p class="section-note">${esc(evidenceText(connection.evidence || ""))}</p><button class="button danger" data-action="delete-connection">Delete connection</button>`;
  }
  if (!component) return `<div class="eyebrow">Drawing review</div><h2>Inspect the drawing</h2><p class="empty-selection">Click a component to inspect its name, type and connections. Add missing equipment or pipe segments with the drawing tools.</p>${sheet ? `<button class="button wide" data-action="reanalyze">Run local extraction</button><div style="height:9px"></div><button class="button wide" data-action="extract-ai">Extract with configured AI</button>${sheet.source_metadata?.analysis_error?`<div class="report-note">Extraction could not finish: ${esc(sheet.source_metadata.analysis_error)}</div>`:""}${sheet.pending_corrections?.length?`<div class="report-note">${sheet.pending_corrections.length} corrections need reattachment after reanalysis: ${sheet.pending_corrections.map(esc).join(", ")}</div>`:""}<hr class="section-divider"><h3>Review queue</h3><div class="result-list">${reviewQueueMarkup(sheet)}</div>` : ""}`;
  const warning = !component.tag || norm(component.type) === "unknown" || component.review_status !== "confirmed";
  return `<div class="eyebrow">Selected equipment</div><div class="selected-tag">${esc(componentName(component))}</div><div class="selection-meta"><span>${esc(component.type || "unknown")}</span><span class="pill${warning ? " warn" : " good"}">${warning ? "Review needed" : "Confirmed"}</span></div>
  <form id="component-form"><label class="field">Equipment tag or printed name<input name="tag" value="${dataValue(component.tag || "")}" placeholder="Unread label"></label><div class="form-grid"><label class="field">Type<input name="type" list="component-types" value="${dataValue(component.type || "unknown")}" required></label><label class="field">Normal position<select name="normal_position">${options(["unknown","open","closed"],component.normal_position || "unknown")}</select></label></div><datalist id="component-types">${["pump","valve","manual valve","check valve","motor-operated valve","tank","vessel","instrument","junction","connector","drain","vent","relief valve","unknown"].map(type => `<option value="${type}">`).join("")}</datalist><div class="form-grid"><label class="field">Boundary credit<select name="isolation_credit">${options(["unverified","eligible","excluded"],component.isolation_credit || "unverified")}</select></label><label class="field">Review state<select name="review_status">${options(["unreviewed","confirmed"],component.review_status || "unreviewed")}</select></label></div><div class="form-actions"><button type="submit" class="button primary">Save correction</button><button type="button" class="button danger" data-action="delete-component">Remove</button></div></form>
  <div class="connection-buttons"><button class="button small" data-action="trace">Trace connections</button><button class="button small" data-action="isolate">Scope isolation</button></div><hr class="section-divider"><dl class="fact-list"><dt>Sheet</dt><dd>${esc(sheetName(sheet))}</dd><dt>Grid</dt><dd><button class="result-title" data-action="grid-view">View coordinates</button></dd><dt>Source</dt><dd>${esc(evidenceText(component.source || component.provenance || sheet.source_metadata?.analysis?.method || sheet.provenance?.method || "Drawing review"))}</dd></dl>
  ${component.evidence ? `<p class="section-note">${esc(typeof component.evidence === "object" ? JSON.stringify(component.evidence) : component.evidence)}</p>` : ""}
  <img class="region-preview" src="/api/region?sheet_id=${encodeURIComponent(sheet.id)}&component_id=${encodeURIComponent(component.id)}" alt="Drawing region around selected equipment">
  <h3>Connected equipment</h3><div class="result-list">${neighborsMarkup(sheet,component)}</div>`;
}

function options(values, selected) {
  return values.map(value => `<option value="${dataValue(value)}"${value === selected ? " selected" : ""}>${esc(value.replaceAll("_", " "))}</option>`).join("");
}

function reviewQueueMarkup(sheet) {
  const queue = (sheet.components || []).filter(component => component.review_status !== "confirmed" || !component.tag || norm(component.type) === "unknown");
  return queue.slice(0,30).map(component => `<div class="result-item"><div><button class="result-title" data-action="select-component" data-id="${dataValue(component.id)}">${esc(componentName(component))}</button><div class="result-detail">${esc(component.type || "unknown")} · ${!component.tag ? "Name unread" : "Awaiting review"}</div></div></div>`).join("") || '<p class="section-note">All equipment on this sheet has been reviewed.</p>';
}

function neighborsMarkup(sheet, component) {
  return (sheet.connections || []).filter(edge => [edge.source,edge.target].includes(component.id)).map(edge => {
    const other = sheet.components.find(item => item.id === (edge.source === component.id ? edge.target : edge.source));
    return `<div class="result-item"><div><button class="result-title" data-action="select-component" data-id="${dataValue(other?.id)}">${esc(componentName(other))}</button><div class="result-detail">${esc(edge.kind || "pipe")} connection</div></div></div>`;
  }).join("") || '<p class="section-note">No connections recorded.</p>';
}

function inspectorMarkup() {
  if (app.tab === "selection") return selectionMarkup();
  if (app.tab === "links") return linksMarkup();
  const isolation = app.tab === "isolation";
  const result = isolation ? app.isolation : app.trace;
  return `<div class="eyebrow">${isolation ? "Engineer review" : "Connected network"}</div><h2>${isolation ? "Candidate isolation" : "Trace connections"}</h2>
    <p class="section-note">${isolation ? "Trace to reviewed boundary candidates, then verify the result against the source drawings and site process." : "Follow recorded piping through confirmed connections between sheets."}</p>
    ${selectedComponent() ? `<button class="button primary wide" data-action="${isolation ? "isolate" : "trace"}">${isolation ? "Scope" : "Trace from"} ${esc(componentName(selectedComponent()))}</button>` : '<p class="empty-selection">Select equipment on a drawing first.</p>'}
    ${!isolation ? '<label class="layer-toggle" style="margin:12px 0"><input id="trace-signals" type="checkbox">Include signal connections</label>' : ""}
    ${result ? resultMarkup(result,isolation) : ""}`;
}

function resultMarkup(result, isolation) {
  const content = isolation ? `<div class="report-note">${result.status === "incomplete" ? "The boundary has open paths or unverified items." : "Candidate boundary for engineering review."}</div>${categoryMarkup("Closure candidates",result.closures,true)}${categoryMarkup("Drains",result.drains)}${categoryMarkup("Vents",result.vents)}${categoryMarkup("Relief paths",result.relief)}${categoryMarkup("Valves not credited",result.not_credited)}${categoryMarkup("Open paths",result.open_ends)}` : categoryMarkup("Connected equipment",result.nodes);
  const warnings = result.warnings || [];
  return `<hr class="section-divider"><div class="result-heading"><h3>${isolation ? "Boundary review" : "Network result"}</h3><button class="button small" data-action="export-result" data-kind="${isolation ? "isolation" : "trace"}">JSON</button></div>${content}${warnings.length ? `<h3 style="margin-top:18px">Items to verify</h3><ul class="warning-list">${warnings.map(item => `<li>${esc(typeof item === "string" ? item : item.message || item.reason || JSON.stringify(item))}</li>`).join("")}</ul>` : ""}${isolation ? '<button class="button wide" data-action="print-report">Print review report</button>' : ""}`;
}

function categoryMarkup(title, items = [], numbered = false) {
  if (!items?.length) return "";
  return `<h3 style="margin-top:17px">${esc(title)} <span class="muted tiny">${items.length}</span></h3><div class="result-list">${items.map((item,index) => {
    if (typeof item === "string") return `<div class="result-item"><div class="result-detail">${esc(item)}</div></div>`;
    const sheet = app.data.sheets.find(sheet => sheet.id === item.sheet_id);
    const component = sheet?.components?.find(component => component.id === item.component_id);
    return `<div class="result-item">${numbered ? `<span class="result-number">${item.index || index+1}</span>` : ""}<div><button class="result-title" data-action="result-component" data-sheet="${dataValue(item.sheet_id || "")}" data-id="${dataValue(item.component_id || "")}">${esc(item.tag || item.label || (component ? componentName(component) : item.reason || "Open connection"))}</button><div class="result-detail">${esc(sheetName(sheet))}${item.reason ? ` · ${esc(item.reason)}` : ""}${item.normal_position === "closed" ? " · Verify normally closed" : ""}</div></div></div>`;
  }).join("")}</div>`;
}

function linksMarkup() {
  const sheet = selectedSheet();
  if (!sheet) return '<p class="empty-selection">Open a drawing to review its off-page links.</p>';
  const links = app.data.links.filter(link => link.from?.sheet_id === sheet.id || link.to?.sheet_id === sheet.id);
  const connector = isConnector(selectedComponent()) ? selectedComponent() : null;
  return `<div class="eyebrow">Off-page continuations</div><h2>Connected sheets</h2><p class="section-note">Confirm both connector ends against the drawings. Links remain inside the selected facility.</p><button class="button wide" data-action="suggest-links">Find connector candidates</button>${links.map(link => linkCardMarkup(link)).join("") || '<p class="empty-selection">No cross-sheet links recorded.</p>'}
    ${connector ? `<hr class="section-divider"><h3>Connect ${esc(componentName(connector))}</h3><form id="link-form"><label class="field">Destination sheet<select name="to_sheet" id="link-target-sheet" required><option value="">Choose a sheet</option>${activeSheets().filter(other => other.id !== sheet.id).map(other => `<option value="${dataValue(other.id)}">${esc(sheetName(other))}</option>`).join("")}</select></label><label class="field">Destination connector<select name="to_component" id="link-target-component" required><option value="">Choose destination sheet first</option></select></label><button class="button primary wide" type="submit">Confirm connector link</button></form>` : '<p class="section-note">Select an off-page connector to add a confirmed link.</p>'}`;
}

function linkCardMarkup(link) {
  const from = app.data.sheets.find(sheet => sheet.id === link.from?.sheet_id);
  const to = app.data.sheets.find(sheet => sheet.id === link.to?.sheet_id);
  const fromId = endpointId(link.from), toId = endpointId(link.to);
  const first = from?.components?.find(component => component.id === fromId), second = to?.components?.find(component => component.id === toId);
  return `<div class="link-card"><span class="pill ${link.status === "confirmed" ? "good" : "warn"}">${esc(link.status || "suggested")}</span><div class="link-pair"><div>${from ? `<img src="/api/region?sheet_id=${encodeURIComponent(from.id)}&component_id=${encodeURIComponent(fromId)}" alt="Source connector">` : ""}<span>${esc(sheetName(from))}<br>${esc(componentName(first))}</span></div><div>${to ? `<img src="/api/region?sheet_id=${encodeURIComponent(to.id)}&component_id=${encodeURIComponent(toId)}" alt="Destination connector">` : ""}<span>${esc(sheetName(to))}<br>${esc(componentName(second))}</span></div></div><div class="form-actions">${link.status !== "confirmed" ? `<button class="button small primary" data-action="link-status" data-id="${dataValue(link.id)}" data-status="confirmed">Confirm link</button>` : `<button class="button small" data-action="result-component" data-sheet="${dataValue(to?.id)}" data-id="${dataValue(toId)}">Open destination</button>`}<button class="button small" data-action="link-status" data-id="${dataValue(link.id)}" data-status="rejected">Reject</button></div></div>`;
}

function renderGraph() {
  const sheets = activeSheets().filter(sheet => sheet.active !== false && !sheet.superseded);
  $("#main").innerHTML = `<div class="view-page"><div class="page-heading"><div><h1>Facility graph</h1><p>Explore the drawing set and its confirmed connections. Select a node to open its source sheet.</p></div><div class="toolbar-right"><a class="button" href="/api/export?facility_id=${encodeURIComponent(app.facilityId)}&format=json" download>Export graph JSON</a><a class="button" href="/api/export?facility_id=${encodeURIComponent(app.facilityId)}&format=csv" download>Equipment CSV</a><a class="button" href="/api/export?facility_id=${encodeURIComponent(app.facilityId)}&format=matrix" download>Adjacency matrix CSV</a></div></div><div class="card"><div class="graph-controls"><button data-graph="sheets" class="tool-button${app.graphMode === "sheets" ? " active" : ""}">Drawing sheets</button><button data-graph="components" class="tool-button${app.graphMode === "components" ? " active" : ""}">Equipment network</button><span class="muted tiny" style="margin-left:auto">${sheets.length} sheets · ${app.data.links.filter(link => link.status === "confirmed" && link.facility_id === app.facilityId).length} confirmed links</span></div><div class="graph-stage">${sheets.length ? graphSvg(sheets) : '<p class="graph-placeholder">Import drawings or load a demonstration to build the facility graph.</p>'}</div></div></div>`;
}

function graphSvg(sheets) {
  const componentsMode = app.graphMode === "components";
  const nodes = componentsMode ? sheets.flatMap(sheet => (sheet.components || []).map(component => ({ id:`${sheet.id}/${component.id}`, sheet, component, title:componentName(component), type:component.type }))) : sheets.map(sheet => ({ id:sheet.id,sheet,title:sheetName(sheet),type:"sheet" }));
  const columns = componentsMode ? 6 : 4;
  const nodeWidth = componentsMode ? 170 : 220, nodeHeight = componentsMode ? 100 : 155;
  const width = Math.max(950,columns*nodeWidth+60), height = Math.max(460,Math.ceil(nodes.length/columns)*nodeHeight+70);
  const positions = new Map(nodes.map((node,index) => [node.id,[60+index%columns*nodeWidth,50+Math.floor(index/columns)*nodeHeight]]));
  const edges = [];
  if (componentsMode) sheets.forEach(sheet => (sheet.connections || []).forEach(connection => edges.push({source:`${sheet.id}/${connection.source}`,target:`${sheet.id}/${connection.target}`,kind:connection.kind})));
  app.data.links.filter(link => link.facility_id === app.facilityId && link.status !== "rejected").forEach(link => edges.push({source:componentsMode ? `${link.from.sheet_id}/${endpointId(link.from)}` : link.from.sheet_id,target:componentsMode ? `${link.to.sheet_id}/${endpointId(link.to)}` : link.to.sheet_id,status:link.status}));
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="Facility drawing and component connections"><defs><marker id="graph-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#8a9eaa"/></marker></defs>${edges.map(edge => {const a=positions.get(edge.source),b=positions.get(edge.target);if(!a||!b)return "";return `<path class="graph-edge ${edge.kind === "signal" ? "signal" : ""} ${edge.status === "suggested" ? "suggested" : ""}" d="M${a[0]+55},${a[1]+27} L${b[0]+55},${b[1]+27}"/>`;}).join("")}${nodes.map(node => {const [x,y]=positions.get(node.id);return `<g class="graph-node" tabindex="0" role="button" data-action="graph-node" data-sheet="${dataValue(node.sheet.id)}" data-id="${dataValue(node.component?.id || "")}" transform="translate(${x},${y})"><rect x="0" y="0" width="${componentsMode ? 137 : 180}" height="${componentsMode ? 61 : 105}" rx="6" fill="${node.sheet.id === app.sheetId ? "#edf5ee" : "white"}" stroke="#c6d3d5"/>${componentsMode ? componentIcon(node.type,16,22) : '<path d="M15 15h16l7 7v24H15z M31 15v9h7 M20 30h13 M20 36h13" stroke="#547a68" fill="none" stroke-width="1.5"/>'}<text class="graph-label" x="${componentsMode ? 39 : 15}" y="${componentsMode ? 25 : 69}">${esc(node.title.length > (componentsMode ? 15 : 24) ? node.title.slice(0,componentsMode ? 14 : 23)+"…" : node.title)}</text><text class="graph-sublabel" x="15" y="${componentsMode ? 47 : 87}">${esc(componentsMode ? node.type : `${node.sheet.components?.length || 0} components`)}</text></g>`;}).join("")}</svg>`;
}

function componentIcon(type, x, y) {
  const text=norm(type);let shape;
  if(text.includes("valve"))shape=`<path d="M-9-6L9 6V-6L-9 6Z"/>`;
  else if(text.includes("pump"))shape='<circle r="8"/><path d="M-5 4L5 0L-5-4Z"/>';
  else if(text.includes("tank")||text.includes("vessel"))shape='<rect x="-7" y="-10" width="14" height="20" rx="5"/>';
  else if(text.includes("junction"))shape='<path d="M-9 0H9M0 0V-9"/><circle r="2" fill="#507d6a"/>';
  else shape='<circle r="7"/>';
  return `<g transform="translate(${x},${y})" stroke="#507d6a" stroke-width="1.4" fill="none">${shape}</g>`;
}

function renderAssistant() {
  const sheets = activeSheets();
  const provider = app.data.settings.provider || "local";
  $("#main").innerHTML = `<div class="view-page"><div class="page-heading"><div><h1>Ask the connected drawing</h1><p>Answers use the selected facility and recorded drawing connections. Choose a configured AI provider when you want to send that context to an external service.</p></div></div><div class="assistant-layout"><div class="card"><div class="assistant-messages" id="assistant-messages">${app.messages.length ? app.messages.map(message => `<div class="message ${message.role}"><div class="message-label">${message.role === "user" ? "You" : "Drawing assistant"}</div>${esc(message.text)}${message.citations?.length?`<div class="message-sources">${message.citations.map(source=>`<button class="button small" data-action="result-component" data-sheet="${dataValue(source.sheet_id)}">${esc(source.drawing_number||source.title||"Source drawing")} ${source.revision?`· Rev ${esc(source.revision)}`:""}</button>`).join("")}</div>`:""}</div>`).join("") : '<p class="empty-selection">Search for equipment, inspect a flow path or ask about a candidate boundary. The local assistant queries the saved graph.</p>'}${app.assistantBusy ? '<div class="message"><div class="spinner"></div></div>' : ""}</div><form id="assistant-form" class="assistant-form"><textarea name="question" aria-label="Question" placeholder="What connects to this pump?" required></textarea><div class="assistant-actions"><select id="ask-provider" name="provider" aria-label="AI provider">${options(["local","openai","anthropic","compatible"],provider)}</select><button class="button primary" type="submit"${app.assistantBusy ? " disabled" : ""}>Ask</button></div></form></div><div><div class="card"><h3>Drawing context</h3><label class="field">Sheet<select id="assistant-sheet">${sheets.map(sheet => `<option value="${dataValue(sheet.id)}"${sheet.id === app.sheetId ? " selected" : ""}>${esc(sheetName(sheet))}</option>`).join("")}</select></label><p class="section-note">${selectedComponent() ? `Selected ${esc(componentName(selectedComponent()))}` : "Select equipment on a drawing to focus the question."}</p><button class="suggestion" data-question="What equipment is on this sheet?">List the equipment on this sheet</button><button class="suggestion" data-question="What connects to the selected component?">Trace the selected component</button><button class="suggestion" data-question="What is the candidate isolation boundary for the selected component?">Review the candidate isolation boundary</button><hr class="section-divider"><button class="button wide" data-action="settings-view">Configure GPT or Claude</button></div></div></div></div>`;
  const box=$("#assistant-messages");box.scrollTop=box.scrollHeight;
}

function renderAudit() {
  const records = [...app.data.audit].reverse();
  $("#main").innerHTML = `<div class="view-page"><div class="page-heading"><div><h1>Drawing audit</h1><p>Corrections, imports, connector decisions and analysis requests recorded by the local workspace.</p></div><a class="button" href="/api/export?facility_id=${encodeURIComponent(app.facilityId)}&format=json" download>Export project record</a></div><div class="card" style="padding:0;overflow:auto"><table class="audit-table"><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Details</th></tr></thead><tbody>${records.map(record => `<tr><td>${esc(displayDate(record.timestamp || record.created_at || record.time))}</td><td>${esc(record.actor || "Local user")}</td><td>${esc(record.action || record.event || "Update")}</td><td><div class="audit-detail">${esc(typeof record.details === "object" ? JSON.stringify(record.details,null,2) : record.details || record.message || record.sheet_id || "")}</div></td></tr>`).join("") || '<tr><td colspan="4" class="muted">No changes recorded yet.</td></tr>'}</tbody></table></div></div>`;
}

function displayDate(value) {
  const date=new Date(value);return Number.isNaN(date.getTime()) ? String(value || "") : date.toLocaleString();
}

function renderSettings() {
  const settings=app.data.settings;
  $("#main").innerHTML = `<div class="view-page"><div class="page-heading"><div><h1>Workspace settings</h1><p>Drawings and corrections are stored in this local workspace. AI requests run only when you submit a question or extraction.</p></div></div><div class="settings-grid"><section class="card"><h2>AI connection</h2><form id="settings-form"><label class="field">Provider<select name="provider">${providerOptions(settings.provider || "local")}</select></label><label class="field">Model<input name="model" value="${dataValue(settings.model || "")}" placeholder="Enter the approved model ID"></label><label class="field">API base URL<input name="base_url" type="url" value="${dataValue(settings.base_url || "")}" placeholder="Provider default or approved local endpoint"></label><div id="provider-status">${providerStatusMarkup()}</div><p class="section-note">Configure <code>OPENAI_API_KEY</code> or <code>ANTHROPIC_API_KEY</code> in the launcher environment, then restart. Keys are never entered or saved here. Your ORNL ChatGPT account and subscription are separate from API access. For an approved local model, choose the compatible gateway and enter its base URL.</p><label class="layer-toggle" style="margin:14px 0"><input name="use_geometry_reference" type="checkbox"${settings.use_geometry_reference ? " checked" : ""}>Use source symbol geometry reference for local extraction</label><label class="field">Reviewer name<input name="actor" value="${dataValue(settings.actor || "Local user")}" required></label><button type="submit" class="button primary">Save settings</button><button type="button" class="button" data-action="provider-status">Refresh status</button></form></section><section class="card"><h2>Agent access</h2><p>Use the REST API or MCP tools to search drawings, follow connections and request candidate isolation results from a compatible assistant.</p><div class="integration-list"><span class="pill">REST API</span><span class="pill">MCP</span></div><button class="button" data-action="integration">Show connection details</button><pre class="code-block" id="integration-config">${esc(app.integration ? JSON.stringify(app.integration,null,2) : "Load the connection details to see this workspace’s endpoints and tool configuration.")}</pre><a href="/api/integration" target="_blank" rel="noopener">Open integration JSON ↗</a><hr class="section-divider"><h3>Drawing controls</h3><div class="keyboard-shortcuts"><span><kbd>+</kbd> <kbd>−</kbd> Zoom</span><span><kbd>F</kbd> Fit</span><span><kbd>Esc</kbd> Cancel drawing</span><span><kbd>Delete</kbd> Remove selection</span></div></section></div></div>`;
}

function providerOptions(selected) {
  return [{id:"local",label:"Local graph queries"},{id:"compatible",label:"Approved gateway / local model"},{id:"openai",label:"OpenAI"},{id:"anthropic",label:"Anthropic"}].map(item=>`<option value="${item.id}"${item.id===selected?" selected":""}>${item.label}</option>`).join("");
}

function providerStatusMarkup() {
  const status=app.data.provider_status||{};
  return `<p class="provider-status"><span class="pill${status.ready?" good":" warn"}">${status.ready?"Ready":"Setup needed"}</span> ${esc(status.provider==="local"?"Local graph queries · no network":status.error||status.model||"Provider status unavailable")}</p>${status.key_env?`<label class="field">Credential from ${esc(status.key_env)}<input type="text" disabled value="${status.key_available?"•••••••• · configured":"Not present in launcher environment"}"></label>`:""}`;
}

function setupCanvas() {
  const canvas=$("#drawing-canvas");
  const sheet=selectedSheet();
  const rect=canvas.getBoundingClientRect();
  const ratio=window.devicePixelRatio || 1;
  canvas.width=Math.max(1,Math.round(rect.width*ratio));canvas.height=Math.max(1,Math.round(rect.height*ratio));
  const key=`${sheet.id}:${sheet.image_url}`;
  if(app.imageKey!==key){
    app.imageKey=key;app.image=null;const image=new Image();
    image.onload=()=>{if(app.imageKey!==key)return;app.image=image;fitCanvas();};
    image.onerror=()=>toast("The source image could not be opened.",true);
    image.src=sheet.image_url || `/api/images/${encodeURIComponent(sheet.id)}`;
  } else drawCanvas();
  canvas.onwheel=event=>{event.preventDefault();const r=canvas.getBoundingClientRect();zoomCanvas(event.deltaY<0 ? 1.16 : 1/1.16,[event.clientX-r.left,event.clientY-r.top]);};
  canvas.onpointerdown=pointerDown;
  canvas.onpointermove=pointerMove;
  canvas.onpointerup=pointerUp;
  canvas.onpointercancel=()=>{if(app.pointer?.box&&app.pointer.hit)app.pointer.hit.bbox=app.pointer.box;app.pointer=null;app.draft=null;drawCanvas();};
}

function canvasSize(){const canvas=$("#drawing-canvas");return canvas ? [canvas.clientWidth,canvas.clientHeight] : [0,0];}
function fitCanvas(){const sheet=selectedSheet();if(!sheet)return;const[w,h]=canvasSize();app.scale=Math.min((w-40)/sheet.width,(h-40)/sheet.height);app.offset=[(w-sheet.width*app.scale)/2,(h-sheet.height*app.scale)/2];drawCanvas();}
function zoomCanvas(factor,anchor=null){const[w,h]=canvasSize();const at=anchor || [w/2,h/2];const point=worldPoint(at);const next=Math.max(.03,Math.min(8,app.scale*factor));app.scale=next;app.offset=[at[0]-point[0]*next,at[1]-point[1]*next];drawCanvas();}
function worldPoint(point){return[(point[0]-app.offset[0])/app.scale,(point[1]-app.offset[1])/app.scale];}
function screenPoint(point){return[point[0]*app.scale+app.offset[0],point[1]*app.scale+app.offset[1]];}

function drawCanvas() {
  const canvas=$("#drawing-canvas"),sheet=selectedSheet();if(!canvas||!sheet)return;
  const ctx=canvas.getContext("2d"),ratio=window.devicePixelRatio||1;
  ctx.setTransform(ratio,0,0,ratio,0,0);const[w,h]=canvasSize();ctx.clearRect(0,0,w,h);ctx.fillStyle="#eef0eb";ctx.fillRect(0,0,w,h);
  ctx.save();ctx.translate(...app.offset);ctx.scale(app.scale,app.scale);ctx.fillStyle="white";ctx.shadowColor="#1734261a";ctx.shadowBlur=15/app.scale;ctx.fillRect(0,0,sheet.width,sheet.height);ctx.shadowBlur=0;
  if(app.image)ctx.drawImage(app.image,0,0,sheet.width,sheet.height);
  const nodeKeys=new Set([...(app.view==="grid"?[]:app.trace?.nodes||[]),...(app.view==="grid"?[]:app.isolation?.nodes||[])].filter(node=>node.sheet_id===sheet.id).map(node=>node.component_id));
  if(app.view!=="grid")for(const edge of sheet.connections||[])drawConnection(ctx,edge,sheet);
  ctx.globalAlpha=1;
  for(const component of sheet.components||[]){if(!component.bbox)continue;const[x0,y0,x1,y1]=component.bbox;const selected=component.id===app.componentId;ctx.fillStyle=selected?"#19785f20":nodeKeys.has(component.id)?"#19785f16":"transparent";ctx.fillRect(x0,y0,x1-x0,y1-y0);ctx.strokeStyle=selected?"#19785f":nodeKeys.has(component.id)?"#249b70":"#3890b2";ctx.lineWidth=(selected?2.5:1)/app.scale;ctx.strokeRect(x0,y0,x1-x0,y1-y0);}
  if(app.view==="grid"&&app.gridVisible)drawGrid(ctx,sheet);
  const closures=app.view==="grid"?[]:app.isolation?.closures||[];
  for(const component of sheet.components||[]){if(!component.bbox)continue;const closure=closures.find(item=>item.sheet_id===sheet.id&&item.component_id===component.id);if(closure){const[x,y]=pointCenter(component);ctx.beginPath();ctx.arc(x,y,11/app.scale,0,Math.PI*2);ctx.fillStyle="#ad4140";ctx.fill();ctx.font=`bold ${11/app.scale}px Segoe UI`;ctx.fillStyle="white";ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText(String(closure.index || closures.indexOf(closure)+1),x,y);}if(app.view==="grid"?app.gridLabels:app.labels)drawEquipmentLabel(ctx,component,sheet);}
  if(app.draft){ctx.strokeStyle="#19785f";ctx.lineWidth=2/app.scale;ctx.setLineDash([5/app.scale,3/app.scale]);if(app.draft.box){const[x0,y0,x1,y1]=app.draft.box;ctx.strokeRect(x0,y0,x1-x0,y1-y0);}else if(app.draft.points?.length){ctx.beginPath();app.draft.points.forEach((p,index)=>index?ctx.lineTo(...p):ctx.moveTo(...p));ctx.stroke();}ctx.setLineDash([]);}
  ctx.restore();const zoom=$("#zoom-label");if(zoom)zoom.textContent=`${Math.round(app.scale*100)}%`;
}

function drawConnection(ctx,edge,sheet){
  if(edge.kind==="signal"?!app.signals:edge.kind==="pipe"&&!app.pipes)return;
  const from=sheet.components.find(item=>item.id===edge.source),to=sheet.components.find(item=>item.id===edge.target);
  if(!from||!to)return;
  const points=edge.path?.length>1?edge.path:[pointCenter(from),pointCenter(to)];
  const selected=edge.id===app.connectionId,unknown=!["pipe","signal"].includes(edge.kind);
  ctx.strokeStyle=selected?"#dc7435":unknown?"#b78338":edge.kind==="signal"?"#8c66b1":"#277ba2";
  ctx.lineWidth=(selected?3:1.5)/app.scale;ctx.globalAlpha=.8;
  ctx.setLineDash(edge.kind==="signal"?[6/app.scale,4/app.scale]:unknown?[3/app.scale,3/app.scale]:[]);
  ctx.beginPath();points.forEach((point,index)=>index?ctx.lineTo(...point):ctx.moveTo(...point));ctx.stroke();ctx.setLineDash([]);
  let segment=null,longest=0;
  points.slice(1).forEach((point,index)=>{const a=points[index],length=Math.hypot(point[0]-a[0],point[1]-a[1]);if(length>longest){longest=length;segment=[a,point];}});
  if(segment&&longest*app.scale>22&&["source_to_target","target_to_source","bidirectional"].includes(edge.direction)){
    const[a,b]=segment,angle=Math.atan2(b[1]-a[1],b[0]-a[0]),x=(a[0]+b[0])/2,y=(a[1]+b[1])/2;
    const arrow=(rotation,offset=0)=>{ctx.save();ctx.translate(x+Math.cos(angle)*offset/app.scale,y+Math.sin(angle)*offset/app.scale);ctx.rotate(rotation);ctx.beginPath();ctx.moveTo(-6/app.scale,-4/app.scale);ctx.lineTo(0,0);ctx.lineTo(-6/app.scale,4/app.scale);ctx.stroke();ctx.restore();};
    if(edge.direction!=="target_to_source")arrow(angle,edge.direction==="bidirectional"?7:0);
    if(edge.direction!=="source_to_target")arrow(angle+Math.PI,edge.direction==="bidirectional"?-7:0);
  }
}

function drawGrid(ctx,sheet){const columns=Number(sheet.grid?.columns)||12,rows=Number(sheet.grid?.rows)||16;const dx=sheet.width/columns,dy=sheet.height/rows;ctx.strokeStyle="#3c796b4d";ctx.lineWidth=.8/app.scale;ctx.beginPath();for(let i=0;i<=columns;i++){ctx.moveTo(i*dx,0);ctx.lineTo(i*dx,sheet.height);}for(let j=0;j<=rows;j++){ctx.moveTo(0,j*dy);ctx.lineTo(sheet.width,j*dy);}ctx.stroke();ctx.font=`${10/app.scale}px Segoe UI`;ctx.textAlign="center";ctx.textBaseline="top";ctx.fillStyle="#246554";for(let i=0;i<columns;i++)ctx.fillText(String(i+1),(i+.5)*dx,6/app.scale);ctx.textAlign="left";ctx.textBaseline="middle";for(let j=0;j<rows;j++)ctx.fillText(String(j+1),6/app.scale,(j+.5)*dy);}

function drawEquipmentLabel(ctx,component,sheet){const[x0,,x1,y1]=component.bbox;const[col,row]=gridPosition(component,sheet);const label=app.view==="grid"?`${componentName(component)} (${col},${row})`:componentName(component);ctx.font=`${11/app.scale}px Segoe UI`;ctx.textAlign="center";ctx.textBaseline="top";const width=ctx.measureText(label).width+8/app.scale;const x=(x0+x1)/2,y=y1+5/app.scale;ctx.fillStyle="#fffffff2";ctx.fillRect(x-width/2,y-1/app.scale,width,15/app.scale);ctx.fillStyle=component.id===app.componentId?"#14654e":"#254f62";ctx.fillText(label,x,y);}

function eventPoint(event){const rect=$("#drawing-canvas").getBoundingClientRect();return[event.clientX-rect.left,event.clientY-rect.top];}
function hitComponent(point){return [...(selectedSheet()?.components||[])].reverse().find(component=>component.bbox&&component.bbox[0]-4/app.scale<=point[0]&&point[0]<=component.bbox[2]+4/app.scale&&component.bbox[1]-4/app.scale<=point[1]&&point[1]<=component.bbox[3]+4/app.scale);}
function distanceSegment(point,a,b){const dx=b[0]-a[0],dy=b[1]-a[1];const t=Math.min(1,Math.max(0,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/(dx*dx+dy*dy||1)));return Math.hypot(point[0]-a[0]-t*dx,point[1]-a[1]-t*dy);}
function hitConnection(point){const sheet=selectedSheet();return(sheet.connections||[]).find(edge=>{const a=sheet.components.find(item=>item.id===edge.source),b=sheet.components.find(item=>item.id===edge.target);if(!a||!b)return false;const pts=edge.path?.length>1?edge.path:[pointCenter(a),pointCenter(b)];return pts.some((p,index)=>index&&distanceSegment(point,pts[index-1],p)<5/app.scale);});}

function pointerDown(event){if(event.button!==0)return;const canvas=event.currentTarget;canvas.setPointerCapture(event.pointerId);const screen=eventPoint(event),world=worldPoint(screen);const hit=hitComponent(world);app.pointer={screen,world,offset:[...app.offset],hit,moved:false,box:hit?.bbox?[...hit.bbox]:null};if(app.tool==="component"&&app.view!=="grid")app.draft={box:[...world,...world]};}
function pointerMove(event){if(!app.pointer)return;const point=eventPoint(event),world=worldPoint(point);const dx=point[0]-app.pointer.screen[0],dy=point[1]-app.pointer.screen[1];if(Math.hypot(dx,dy)>4)app.pointer.moved=true;if(app.tool==="component"&&app.view!=="grid"){app.draft={box:[...app.pointer.world,...world]};}else if(app.tool==="move"&&app.pointer.hit&&app.view!=="grid"){const sheet=selectedSheet();const shift=[Math.max(-app.pointer.box[0],Math.min(sheet.width-app.pointer.box[2],dx/app.scale)),Math.max(-app.pointer.box[1],Math.min(sheet.height-app.pointer.box[3],dy/app.scale))];app.pointer.hit.bbox=app.pointer.box.map((value,index)=>value+shift[index%2]);}else if(["select","move"].includes(app.tool)||app.view==="grid")app.offset=[app.pointer.offset[0]+dx,app.pointer.offset[1]+dy];drawCanvas();}
async function pointerUp(event){if(!app.pointer)return;const start=app.pointer;app.pointer=null;const point=worldPoint(eventPoint(event));try{if(app.tool==="component"&&app.view!=="grid"){const box=app.draft?.box;app.draft=null;if(start.moved&&box){const sheet=selectedSheet();const bbox=[Math.max(0,Math.min(box[0],box[2])),Math.max(0,Math.min(box[1],box[3])),Math.min(sheet.width,Math.max(box[0],box[2])),Math.min(sheet.height,Math.max(box[1],box[3]))].map(Math.round);if(bbox[2]-bbox[0]<3||bbox[3]-bbox[1]<3)return;const result=await action("save_component",{sheet_id:sheet.id,component:{type:"unknown",tag:"",bbox,normal_position:"unknown",review_status:"unreviewed",isolation_credit:"unverified"}});app.componentId=result.result?.item_id||result.result?.id||result.component?.id||selectedSheet().components.at(-1)?.id||"";app.tool="select";render();}}else if(app.tool==="move"&&start.hit&&start.moved&&app.view!=="grid"){await action("save_component",{sheet_id:app.sheetId,component:start.hit},{message:"Equipment position saved."});}else if(!start.moved){const hit=hitComponent(point);if((app.tool==="pipe"||app.tool==="signal")&&app.view!=="grid"){if(!app.draft){if(!hit)return toast("Choose the first equipment item.");app.draft={source:hit.id,points:[pointCenter(hit)]};}else if(hit){if(hit.id===app.draft.source)return toast("Choose a different endpoint.");const draft=app.draft;app.draft=null;await action("save_connection",{sheet_id:app.sheetId,connection:{source:draft.source,target:hit.id,kind:app.tool,path:[...draft.points,pointCenter(hit)],review_status:"confirmed"}},{message:"Connection saved."});}else app.draft.points.push(point.map(Math.round));drawCanvas();}else{app.componentId=hit?.id||"";app.connectionId=hit?"":hitConnection(point)?.id||"";app.tab="selection";render();}}}catch(error){toast(error.message,true);app.draft=null;if(start.box&&start.hit)start.hit.bbox=start.box;drawCanvas();}drawCanvas();}

function openSheet(id,componentId="",view=app.view==="grid"?"grid":"drawings"){app.sheetId=id;app.componentId=componentId;app.connectionId="";app.imageKey="";app.draft=null;app.view=view;app.tab="selection";render();}

document.addEventListener("click",async event=>{
  const viewButton=event.target.closest("[data-view]");if(viewButton){app.view=viewButton.dataset.view;app.tool="select";app.draft=null;render();return;}
  const tabButton=event.target.closest("[data-tab]");if(tabButton){app.tab=tabButton.dataset.tab;render();return;}
  const toolButton=event.target.closest("[data-tool]");if(toolButton){app.tool=toolButton.dataset.tool;app.draft=null;render();return;}
  const graphButton=event.target.closest("[data-graph]");if(graphButton){app.graphMode=graphButton.dataset.graph;render();return;}
  const question=event.target.closest("[data-question]");if(question){$("#assistant-form textarea").value=question.dataset.question;$("#assistant-form textarea").focus();return;}
  const button=event.target.closest("[data-action]");if(!button)return;
  const name=button.dataset.action;
  try{
    if(name==="close-dialog")button.closest("dialog").close();
    else if(name==="retry")location.reload();
    else if(name==="import"){if(!app.facilityId)return toast("Create a facility first.");$("#import-facility").innerHTML=facilitiesMarkup();$("#import-dialog").showModal();}
    else if(name==="show-demo")$("#demo-dialog").showModal();
    else if(name==="load-demo"){button.closest("dialog").close();const output=await action("demo",{kind:button.dataset.kind},{refresh:false});app.facilityId=output.facility_id||output.result?.facility_id||app.facilityId;app.sheetId=output.result?.target?.sheet_id||"";app.componentId=output.result?.target?.component_id||"";app.view="drawings";app.tab="selection";app.trace=app.isolation=null;await loadState();toast("Demonstration loaded.");}
    else if(name==="new-facility"){const name=prompt("Facility name");if(name?.trim()){const result=await action("create_facility",{name:name.trim()});app.facilityId=result.result?.id||result.facility?.id||app.data.facilities.at(-1)?.id||app.facilityId;app.sheetId="";app.trace=app.isolation=null;await loadState();}}
    else if(name==="open-sheet")openSheet(button.dataset.id);
    else if(name==="select-component"){app.componentId=button.dataset.id;app.connectionId="";app.tab="selection";render();}
    else if(name==="result-component"||name==="graph-node"){if(button.dataset.sheet)openSheet(button.dataset.sheet,button.dataset.id||"");}
    else if(name==="zoom-in")zoomCanvas(1.3);
    else if(name==="zoom-out")zoomCanvas(1/1.3);
    else if(name==="fit")fitCanvas();
    else if(name==="grid-view"){app.view="grid";app.tool="select";render();}
    else if(name==="settings-view"){app.view="settings";render();}
    else if(name==="sheet-settings")showSheetDetails();
    else if(name==="trace"||name==="isolate"){if(!app.componentId)return toast("Select equipment first.");const output=await action(name,{facility_id:app.facilityId,sheet_id:app.sheetId,component_id:app.componentId,include_signals:$("#trace-signals")?.checked||false},{refresh:false});if(name==="trace"){app.trace=output.result||output;app.isolation=null;app.tab="trace";}else{app.isolation=output.result||output;app.trace=null;app.tab="isolation";}render();}
    else if(name==="delete-component"){if(app.componentId&&confirm(`Remove ${componentName(selectedComponent())} and its connections?`)){await action("delete_component",{sheet_id:app.sheetId,component_id:app.componentId});app.componentId="";render();}}
    else if(name==="delete-connection"){if(app.connectionId&&confirm("Remove this connection?")){await action("delete_connection",{sheet_id:app.sheetId,connection_id:app.connectionId});app.connectionId="";render();}}
    else if(name==="suggest-links"){await action("suggest_links",{facility_id:app.facilityId},{message:"Connector candidates refreshed. Confirm each match against both drawings."});}
    else if(name==="link-status"){const link=app.data.links.find(item=>item.id===button.dataset.id);await action("save_link",{link:{...link,status:button.dataset.status}},{message:button.dataset.status==="confirmed"?"Connector link confirmed.":"Connector link rejected."});}
    else if(name==="reanalyze")await action("reanalyze",{sheet_id:app.sheetId},{message:"Drawing extraction started. Your corrections will be retained."});
    else if(name==="extract-ai"){const provider=app.data.settings.provider||"local";if(provider==="local")return toast("Choose an AI provider and model in Settings first.");if(confirm(`Send this drawing to ${provider} for extraction using the configured endpoint?`))await action("extract_ai",{sheet_id:app.sheetId,provider},{message:"AI extraction requested."});}
    else if(name==="provider-status"){app.data.provider_status=await api("/api/providers");$("#provider-status").innerHTML=providerStatusMarkup();}
    else if(name==="integration"){app.integration=await api("/api/integration");$("#integration-config").textContent=JSON.stringify(app.integration,null,2);}
    else if(name==="export-result")downloadJson(button.dataset.kind==="trace"?app.trace:app.isolation,`${button.dataset.kind}-review.json`);
    else if(name==="print-report")window.print();
  }catch(error){toast(error.message,true);}
});

document.addEventListener("change",async event=>{
  if(event.target.id==="facility"){app.facilityId=event.target.value;app.sheetId="";app.componentId="";app.trace=app.isolation=null;try{await loadState();}catch(error){toast(error.message,true);}}
  if(event.target.dataset.layer){app[event.target.dataset.layer]=event.target.checked;drawCanvas();}
  if(event.target.id==="import-files")$("#import-file-summary").textContent=Array.from(event.target.files).map(file=>file.name).join(", ");
  if(event.target.id==="assistant-sheet"){app.sheetId=event.target.value;app.componentId="";}
  if(event.target.id==="link-target-sheet"){const sheet=app.data.sheets.find(sheet=>sheet.id===event.target.value);$("#link-target-component").innerHTML=(sheet?.components||[]).filter(isConnector).map(component=>`<option value="${dataValue(component.id)}">${esc(componentName(component))} · ${esc(component.type)}</option>`).join("");}
});

document.addEventListener("input",event=>{if(event.target.id==="sheet-search"){const start=event.target.selectionStart;app.search=event.target.value;render();const input=$("#sheet-search");input.focus();input.setSelectionRange(start,start);}});

document.addEventListener("submit",async event=>{
  event.preventDefault();const form=event.target,data=new FormData(form);const submit=form.querySelector('[type="submit"]');if(submit)submit.disabled=true;
  try{
    if(form.id==="component-form"){const component={...selectedComponent(),tag:data.get("tag").trim(),type:data.get("type").trim(),normal_position:data.get("normal_position"),isolation_credit:data.get("isolation_credit"),review_status:data.get("review_status")};await action("save_component",{sheet_id:app.sheetId,component},{message:"Equipment correction saved."});}
    else if(form.id==="connection-form"){const connection={...selectedSheet().connections.find(item=>item.id===app.connectionId),...Object.fromEntries(data.entries())};await action("save_connection",{sheet_id:app.sheetId,connection},{message:"Connection correction saved."});}
    else if(form.id==="grid-form"){await action("update_sheet",{sheet_id:app.sheetId,patch:{grid:{columns:Number(data.get("columns")),rows:Number(data.get("rows")),origin:"top-left"}}},{message:"Drawing grid updated."});}
    else if(form.id==="link-form"){await action("save_link",{link:{facility_id:app.facilityId,from:{sheet_id:app.sheetId,component_id:app.componentId},to:{sheet_id:data.get("to_sheet"),component_id:data.get("to_component")},status:"confirmed"}},{message:"Off-page connection confirmed."});}
    else if(form.id==="settings-form"){const settings=Object.fromEntries(data.entries());settings.use_geometry_reference=data.get("use_geometry_reference")==="on";await action("settings",{settings},{message:"Workspace settings saved."});}
    else if(form.id==="import-form"){const files=$("#import-files").files;if(!files.length)return;const body=new FormData();for(const file of files)body.append("files",file,file.name);const facility=$("#import-facility").value;body.append("facility_id",facility);await api("/api/import",{method:"POST",body});$("#import-dialog").close();form.reset();if(app.facilityId!==facility){app.facilityId=facility;app.sheetId="";app.componentId="";app.trace=app.isolation=null;}await loadState();toast("Import queued. Progress appears in the drawing library.");}
    else if(form.id==="assistant-form"){const question=data.get("question").trim();const provider=data.get("provider");if(!question)return;if(provider!=="local"&&!confirm(`Send this question and the selected drawing context to ${provider}?`))return;app.messages.push({role:"user",text:question});app.assistantBusy=true;renderAssistant();try{const output=await action("ask",{facility_id:app.facilityId,sheet_id:app.sheetId,component_id:app.componentId,question,provider},{refresh:false});const result=output.result||output;app.messages.push({role:"assistant",text:result.answer||result.text||JSON.stringify(result,null,2),citations:result.citations});if(result.trace)app.trace=result.trace;if(result.isolation)app.isolation=result.isolation;}finally{app.assistantBusy=false;renderAssistant();}}
  }catch(error){toast(error.message,true);}finally{if(submit&&submit.isConnected)submit.disabled=false;}
});

function showSheetDetails(){const sheet=selectedSheet();if(!sheet)return;let dialog=$("#sheet-dialog");if(dialog)dialog.remove();dialog=document.createElement("dialog");dialog.id="sheet-dialog";dialog.innerHTML=`<form id="sheet-form"><div class="dialog-heading"><h2>Drawing sheet</h2><button type="button" class="icon-button" data-action="close-dialog">×</button></div><label class="field">Title<input name="title" value="${dataValue(sheet.title||"")}" required></label><div class="revision-grid"><label class="field">Drawing number<input name="drawing_number" value="${dataValue(sheet.drawing_number||"")}"></label><label class="field">Sheet number<input name="sheet_number" value="${dataValue(sheet.sheet_number||1)}"></label><label class="field">Revision<input name="revision" value="${dataValue(sheet.revision||"")}"></label></div><label class="layer-toggle" style="margin:15px 0"><input name="active" type="checkbox"${sheet.active!==false?" checked":""}>Current revision · include in trace and isolation</label><p class="section-note">${esc(sheet.filename||"")} · ${sheet.width} × ${sheet.height} image units</p><div class="dialog-actions"><button class="button primary" type="submit">Save details</button></div></form>`;document.body.append(dialog);$("#sheet-form").addEventListener("submit",async event=>{event.preventDefault();event.stopPropagation();try{await action("update_sheet",{sheet_id:sheet.id,patch:{...Object.fromEntries([...new FormData(event.target).entries()].filter(([key])=>key!=="active")),active:new FormData(event.target).get("active")==="on"}});dialog.close();}catch(error){toast(error.message,true);}});dialog.showModal();}

function downloadJson(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:"application/json"}));const link=document.createElement("a");link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}

document.addEventListener("keydown",event=>{
  if(["INPUT","SELECT","TEXTAREA"].includes(event.target.tagName)||document.querySelector("dialog[open]"))return;
  if(event.key==="Escape"){app.draft=null;app.tool="select";app.componentId=app.connectionId="";render();}
  if(!["drawings","grid"].includes(app.view))return;
  if(event.key==="+"||event.key==="="){event.preventDefault();zoomCanvas(1.3);}
  else if(event.key==="-"){event.preventDefault();zoomCanvas(1/1.3);}
  else if(event.key.toLowerCase()==="f"){event.preventDefault();fitCanvas();}
  else if(event.key==="Delete"){const button=$(app.componentId?'[data-action="delete-component"]':'[data-action="delete-connection"]');button?.click();}
});

let resizeTimer;
window.addEventListener("resize",()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(["drawings","grid"].includes(app.view)&&selectedSheet())setupCanvas();},100);});
loadState().catch(error=>{$("#main").innerHTML=`<div class="empty-workspace"><h1>Workspace connection unavailable</h1><p>${esc(error.message)}</p><button class="button" data-action="retry">Retry connection</button></div>`;});
