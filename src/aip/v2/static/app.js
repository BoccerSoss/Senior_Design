"use strict";

function remembered(name, fallback = "") { try { return localStorage.getItem(`aip-v2-${name}`) || fallback; } catch { return fallback; } }
function remember(name, value) { try { localStorage.setItem(`aip-v2-${name}`, value); } catch { /* Storage may be disabled by the browser. */ } }
const app = {
  data: { facilities: [], sheets: [], links: [], jobs: [], audit: [], settings: {} },
  facilityId: remembered("facility"), sheetId: remembered("sheet"), componentId: "", connectionId: "", view: remembered("view", "drawings"), tab: "components",
  tool: "select", search: "", gridVisible: true, gridLabels: false, labels: true, pipes: true, signals: true,
  gridLayout: remembered("grid-layout") === "source" ? "source" : "regular",
  scale: 1, offset: [0, 0], image: null, imageKey: "", pointer: null, draft: null,
  matrixData: {}, matrixPending: new Set(), linkSourceId: "",
  poll: null, toastTimer: null, integration: null, gridData: {}, gridPending: new Set(), initial: true,
};
const $ = (selector, root = document) => root.querySelector(selector);
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const dataValue = value => esc(value ?? "");
const sourceSheet = () => app.data.sheets.find(sheet => sheet.id === app.sheetId);
const selectedSheet = () => window.aipV3?.displaySheet(sourceSheet()) || sourceSheet();
const selectedComponent = () => selectedSheet()?.components?.find(component => component.id === app.componentId);
const activeSheets = () => app.data.sheets.filter(sheet => sheet.facility_id === app.facilityId);
const isJunction = component => ["junction","pipe junction","junction point","tee"].includes(String(component?.type || "").toLowerCase());
const componentName = component => component?.tag || component?.label || component?.observed_tag || component?.normalized_tag || (isJunction(component) ? "Junction" : `Unnamed ${component?.type || "component"}`);
const sheetName = sheet => sheet?.drawing_number || sheet?.title || sheet?.filename || "Untitled drawing";
const norm = value => String(value ?? "").toLowerCase();
const pointCenter = component => component.bbox ? [(component.bbox[0] + component.bbox[2]) / 2, (component.bbox[1] + component.bbox[3]) / 2] : [0, 0];
const endpointId = endpoint => endpoint?.component_id ?? endpoint?.component;
const isConnector = component => ["connector","off page connector","line continuation"].includes(norm(component?.type).replaceAll("-"," ").replaceAll("_"," "));
const evidenceText = value => value && typeof value === "object" ? value.method || value.description || JSON.stringify(value) : value;

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
  if (data.capabilities?.ai_connections && !document.getElementById("v3-script")) {
    const script = document.createElement("script");
    script.id = "v3-script";
    script.src = "/v3.js";
    script.onload = render;
    script.onerror = () => { script.remove(); toast("The V3 controls could not be loaded. Refresh to retry.", true); };
    document.head.append(script);
  }
  if (!app.data.facilities.some(facility => facility.id === app.facilityId)) app.facilityId = app.data.facilities[0]?.id || "";
  const sheets = app.data.sheets;
  if (!sourceSheet()) {
    const awaitingImport = pendingJobs().some(job => job.facility_id === app.facilityId);
    const first = activeSheets()[0] || (!awaitingImport && sheets[0]);
    app.sheetId = first?.id || "";
    app.componentId = app.connectionId = "";
    app.imageKey = "";
  }
  app.facilityId = sourceSheet()?.facility_id || app.facilityId;
  remember("sheet", app.sheetId);
  remember("facility", app.facilityId);
  if (app.componentId && !selectedComponent()) app.componentId = "";
  const drawings = `${sheets.length} drawing${sheets.length === 1 ? "" : "s"}`;
  const sets = new Set(sheets.map(sheet => sheet.facility_id)).size;
  $("#workspace-description").textContent = sets ? `${drawings} in ${sets} drawing set${sets === 1 ? "" : "s"}` : drawings;
  render();
  schedulePoll();
}

async function loadState() {
  const state = await api("/api/state");
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
  if (!["drawings", "matrix", "grid"].includes(app.view)) app.view = "drawings";
  if (!["components", "connections"].includes(app.tab)) app.tab = "components";
  remember("view", app.view);
  document.querySelectorAll("[data-view]").forEach(button => button.classList.toggle("active", button.dataset.view === app.view));
  if (app.view === "matrix") renderMatrix();
  else renderDrawings();
  window.aipV3?.syncDrawing();
  app.initial = false;
}

function groupedLibraryMarkup() {
  const query = norm(app.search).trim();
  const groups = app.data.facilities.map(facility => {
    const matches = app.data.sheets.filter(sheet => sheet.facility_id === facility.id &&
      (!query || norm([facility.name, sheetName(sheet), sheet.title, sheet.filename, sheet.revision,
        ...(sheet.components || []).map(componentName), ...(sheet.v3_summary?.names || [])].join(" ")).includes(query)));
    if (!matches.length) return "";
    return `<section class="sheet-group" aria-label="${dataValue(facility.name)}">
      <h3 class="sheet-group-heading">${esc(facility.name)}<span>${matches.length}</span></h3>
      <div class="sheet-group-items">${matches.map(sheetRowMarkup).join("")}</div>
    </section>`;
  }).join("");
  return groups || `<p class="section-note library-empty">${query ? "No matching drawings." : "Import drawings or open an example to begin."}</p>`;
}

function sheetRowMarkup(sheet) {
  const selected = sheet.id === app.sheetId;
  return `<button class="sheet-row${selected ? " active" : ""}" data-action="open-sheet" data-id="${dataValue(sheet.id)}" aria-current="${selected ? "true" : "false"}" title="${dataValue(`${sheetName(sheet)} · ${sheet.title || sheet.filename || ""}`)}">
    <div class="sheet-title"><span>${esc(sheetName(sheet))}</span><small>${sheet.active === false ? "Archived · " : ""}${sheet.revision ? `Rev ${esc(sheet.revision)}` : ""}</small></div>
    <div class="sheet-caption">${esc(sheet.title || sheet.filename || "")}</div>
    <div class="sheet-count">${esc(libraryCount(sheet))} · Sheet ${esc(sheet.sheet_number || sheet.page || 1)}</div>
  </button>`;
}

function libraryCount(sheet) {
  if (sheet.v3_summary) return `${sheet.v3_summary.components} V3 components`;
  if (sheet.source_metadata?.figure && !sheet.source_metadata.analysis && !sheet.components?.length) return "Ready to trace";
  return `${sheet.components?.length || 0} components`;
}

function libraryMarkup() {
  const jobs = app.data.jobs.slice(-4);
  return `<aside class="library">
    <div class="library-heading"><h2>Drawing library</h2><span class="muted tiny">${app.data.sheets.length}</span></div>
    <input class="search-input" id="sheet-search" type="search" value="${dataValue(app.search)}" placeholder="Find a drawing or equipment tag" aria-label="Find a drawing or equipment tag">
    <div class="sheet-list">${groupedLibraryMarkup()}</div>
    ${jobs.length ? `<div class="jobs">${jobs.map(job => `<div class="job-row${["failed", "error"].includes(job.status) ? " error" : ""}"><strong>${esc(job.title || job.name || job.type || "Import")}</strong><br>${esc(job.error || job.message || job.status)}${typeof job.progress === "number" ? ` · ${Math.round(job.progress)}%` : ""}${job.messages?.length?`<div class="tiny">${job.messages.map(esc).join("<br>")}</div>`:""}</div>`).join("")}${pendingJobs().length ? '<div class="busy-progress"></div>' : ""}</div>` : ""}
    <div class="library-footer"><a href="/reference" target="_blank" rel="noopener">Symbol reference library ↗</a></div>
  </aside>`;
}

function replaceWorkspace(markup) {
  const previous = $(".sheet-list");
  const top = previous?.scrollTop || 0;
  const left = previous?.scrollLeft || 0;
  $("#main").innerHTML = markup;
  const list = $(".sheet-list");
  list.scrollTop = top;
  list.scrollLeft = left;
}

function renderDrawings() {
  const sheet = selectedSheet(), gridMode = app.view === "grid";
  const aiView = window.aipV3?.isCandidate() && !gridMode;
  const previousInspector = $(".inspector[data-grid-sheet]");
  const previousScroll = gridMode && sheet?.id === previousInspector?.dataset.gridSheet ? previousInspector?.scrollTop || 0 : 0;
  replaceWorkspace(`<div class="drawing-layout">${libraryMarkup()}<section class="viewer">
    ${sheet ? `<div class="viewer-heading"><div><h2>${esc(sheetName(sheet))}</h2><div class="muted">${esc(sheet.title || sheet.filename || "")} ${sheet.revision ? `· Revision ${esc(sheet.revision)}` : ""}</div></div><button class="button small sheet-menu" data-action="sheet-settings">Drawing details</button></div>
    ${!gridMode ? `<div class="drawing-actions">${aiView ? "" : '<button class="button primary" data-action="reanalyze">Analyze drawing</button>'}${window.aipV3?.drawingControls() || ""}<button class="button" data-action="matrix-view">View matrix</button><span>${sheet.components?.length||0} components · ${sheet.connections?.length||0} recorded connections</span></div>` : ""}
    <div class="viewer-toolbar">
      ${gridMode ? `<select class="grid-layout-select" id="grid-layout" aria-label="Grid layout"><option value="regular"${app.gridLayout === "regular" ? " selected" : ""}>Regular grid</option><option value="source"${app.gridLayout === "source" ? " selected" : ""}>Equipment centers</option></select>` : aiView ? '<span class="section-note">AI observations · Select to inspect</span>' : `<button class="tool-button${app.tool === "select" ? " active" : ""}" data-tool="select" title="Select equipment or pan">Select</button><button class="tool-button${app.tool === "component" ? " active" : ""}" data-tool="component" title="Drag a box around missing equipment">+ Component</button><button class="tool-button${app.tool === "pipe" ? " active" : ""}" data-tool="pipe" title="Connect immediate equipment endpoints">+ Connection</button><button class="tool-button${app.tool === "signal" ? " active" : ""}" data-tool="signal">+ Signal</button><button class="tool-button${app.tool === "move" ? " active" : ""}" data-tool="move">Move</button>`}
      <span class="toolbar-divider"></span><button class="tool-button" data-action="zoom-out" title="Zoom out">−</button><span class="zoom-label" id="zoom-label"></span><button class="tool-button" data-action="zoom-in" title="Zoom in">+</button><button class="tool-button" data-action="fit">Fit</button>
      <label class="layer-toggle"><input type="checkbox" data-layer="${gridMode ? "gridLabels" : "labels"}"${(gridMode ? app.gridLabels : app.labels) ? " checked" : ""}>${gridMode ? "Coordinates" : "Labels"}</label>
      ${!gridMode ? `<label class="layer-toggle"><input type="checkbox" data-layer="pipes"${app.pipes ? " checked" : ""}>Connections</label><label class="layer-toggle"><input type="checkbox" data-layer="signals"${app.signals ? " checked" : ""}>Signals</label>` : `<label class="layer-toggle"><input type="checkbox" data-layer="gridVisible"${app.gridVisible ? " checked" : ""}>Grid lines</label>`}
    </div><div class="canvas-wrap"><canvas class="drawing-canvas" id="drawing-canvas" aria-label="Interactive drawing. Scroll to zoom, drag to pan and click components or connections to inspect." tabindex="0"></canvas>${gridMode ? "" : `<div class="canvas-caption" id="canvas-caption">${toolHint()}</div>`}</div>
    <div class="viewer-legend">${gridMode ? `<span><i class="grid-dot"></i>Equipment location</span><span>Column → &nbsp; Row ↓</span>` : `<span><i class="legend-line"></i>Equipment connection</span><span><i class="legend-line signal"></i>Signal</span><span><i class="legend-line unknown"></i>Unclassified</span>`}</div>` : `<div class="empty-workspace"><div class="eyebrow">Drawing → Components and connections → Matrix</div><h1>Build a connectivity matrix</h1><p>Open a PNG or JPEG, review detected components and direct connections, then view the labeled matrix. PDFs are converted to images before analysis.</p><div class="actions"><button class="button primary" data-action="import">Import drawings</button><button class="button" data-action="show-demo">Open example</button></div></div>`}
    </section><aside class="inspector"${gridMode && sheet ? ` data-grid-sheet="${dataValue(sheet.id)}"` : ""}>${gridMode ? `<div class="inspector-content">${gridPanelMarkup()}</div>` : `<div class="inspector-tabs">${["components", "connections"].map(tab => `<button data-tab="${tab}" class="${app.tab === tab ? "active" : ""}">${tab === "components" ? "Components" : "Connections"}</button>`).join("")}</div><div class="inspector-content">${aiView ? window.aipV3.inspector(sheet) : app.tab === "connections" ? connectionsMarkup() : selectionMarkup()}</div>`}</aside></div>`);
  if (previousScroll) $(".inspector").scrollTop = previousScroll;
  if (sheet) { setupCanvas(); if (gridMode) loadGrid(sheet); }
}

function toolHint() {
  return { select: "Scroll to zoom · Drag to pan · Click to inspect", component: "Drag a rectangle around the equipment symbol", pipe: "Click the first component, then the next. Click a blank point to add a bend.", signal: "Click both signal endpoints. Click blank space to add a bend.", move: "Drag equipment to adjust its box" }[app.tool];
}

function gridMap(sheet = selectedSheet()) {
  const stored = app.gridData[sheet?.id];
  return stored && stored.version === sheet?.version && stored.layout === app.gridLayout ? stored : null;
}

function gridRecord(component, sheet = selectedSheet()) {
  return gridMap(sheet)?.records?.get(component.id) || null;
}

function gridQuery() {
  return `${matrixQuery()}&layout=${app.gridLayout}`;
}

async function loadGrid(sheet) {
  const layout = app.gridLayout;
  const key = `${sheet.id}/${sheet.version}/${layout}`;
  if (app.gridPending.has(key) || gridMap(sheet)) return;
  app.gridPending.add(key);
  const current = () => app.view === "grid" && app.sheetId === sheet.id &&
    selectedSheet()?.version === sheet.version && app.gridLayout === layout;
  try {
    const result = await api(`/api/grid?${gridQuery()}`);
    if (!current()) return;
    const kind = layout === "regular" ? "regular-intersections" : "source-intersections";
    if (result.layout_kind !== kind || !Array.isArray(result.axes?.x) ||
        !Array.isArray(result.axes?.y) || !Array.isArray(result.coordinates)) {
      throw new Error("Restart the updated workbench to load this grid.");
    }
    const records = new Map(result.coordinates.map(record => [record.component_id, record]));
    app.gridData[sheet.id] = {...result, version:sheet.version, layout, records};
  } catch (error) {
    if (current()) app.gridData[sheet.id] = {version:sheet.version, layout, error:error.message};
  } finally {
    app.gridPending.delete(key);
    if (current()) renderDrawings();
  }
}

function gridPanelMarkup() {
  const sheet = selectedSheet();
  if (!sheet) return '<p class="empty-selection">Open a drawing to view equipment locations.</p>';
  const map = gridMap(sheet);
  const rows = (sheet.components || []).map(component => {
    const point = gridRecord(component, sheet)?.coordinate;
    const location = point ? `(${point.join(", ")})` : !map ? "…" : map.error ? "Unavailable" : "Unplaced";
    const selected = component.id === app.componentId;
    return `<button type="button" class="grid-location" data-action="select-component" data-id="${dataValue(component.id)}" aria-pressed="${selected}">
      <span>${esc(componentName(component))}</span><span class="grid-coordinate">${esc(location)}</span></button>`;
  }).join("");
  return `<h2>Equipment locations</h2>${window.aipV3?.sourceSwitch() || ""}
    <p class="section-note">${app.gridLayout === "regular" ? "Each symbol uses the nearest numbered intersection. Select an item to see its position on the drawing." : "Grid lines pass through the center of every marked symbol."}</p>
    ${map?.grid ? `<div class="grid-summary"><span>${Number(map.grid.columns)} columns × ${Number(map.grid.rows)} rows</span><a href="/api/export?${gridQuery()}&format=grid" download>Export JSON</a></div>` : ""}
    ${map?.shared_cells ? '<p class="section-note">Some items share an intersection. Select a name to locate its symbol.</p>' : ""}
    ${map?.error ? `<p class="report-note error-note">${esc(map.error)}</p><button class="button small" data-action="refresh-grid">Retry grid</button>` : !map ? '<p class="section-note">Loading coordinates…</p>' : ""}
    <div class="grid-locations">${rows || '<p class="section-note">Analyze the drawing or mark a component to see its coordinates.</p>'}</div>
    ${map?.warnings?.length ? `<details class="grid-notes"><summary>Coordinate notes (${map.warnings.length})</summary><ul>${map.warnings.map(item => `<li>${esc(item)}</li>`).join("")}</ul></details>` : ""}`;
}

function options(values, selected) {
  return values.map(value => `<option value="${dataValue(value)}"${value === selected ? " selected" : ""}>${esc(value.replaceAll("_", " "))}</option>`).join("");
}

function extractionNotes(sheet) {
  return `${sheet.source_metadata?.analysis_error ? `<div class="report-note error-note">Extraction could not finish: ${esc(sheet.source_metadata.analysis_error)}</div>` : ""}${sheet.pending_corrections?.length ? `<div class="report-note">${sheet.pending_corrections.length} corrections need reattachment after reanalysis: ${sheet.pending_corrections.map(esc).join(", ")}</div>` : ""}`;
}

function selectionMarkup() {
  const sheet = selectedSheet(), component = selectedComponent();
  if (!sheet) return '<h2>Components</h2><p class="empty-selection">Open an example or import a drawing to begin.</p>';
  if (!component) return `<div class="eyebrow">1 · Review components</div><h2>Components <span class="muted tiny">${sheet.components.length}</span></h2><p class="section-note">Select a symbol or a name below to review its type and printed label. Unread names remain explicit.</p>${extractionNotes(sheet)}<div class="result-list">${sheet.components.map(item => `<div class="result-item"><div><button class="result-title" data-action="select-component" data-id="${dataValue(item.id)}">${esc(componentName(item))}</button><div class="result-detail">${esc(item.type || "unknown")} · ${!item.tag && !isJunction(item) ? "Name unread" : item.review_status === "confirmed" ? "Reviewed" : "Awaiting review"}</div></div></div>`).join("") || '<p class="empty-selection">No components recorded yet. Choose Analyze drawing, or mark a missed symbol with + Component.</p>'}</div>`;
  const warning = !component.tag || norm(component.type) === "unknown" || component.review_status !== "confirmed";
  return `<button class="result-title back-link" data-action="all-components">← All components</button><div class="eyebrow">Selected component</div><div class="selected-tag">${esc(componentName(component))}</div><div class="selection-meta"><span>${esc(component.type || "unknown")}</span><span class="pill${warning ? " warn" : " good"}">${warning ? "Review needed" : "Reviewed"}</span></div>
    <form id="component-form"><label class="field">Equipment tag or printed name<input name="tag" value="${dataValue(component.tag || "")}" placeholder="Unread label"></label><label class="field">Type<input name="type" list="component-types" value="${dataValue(component.type || "unknown")}" required></label><datalist id="component-types">${["pump","valve","tank","vessel","instrument","junction","connector","unknown"].map(type => `<option value="${type}">`).join("")}</datalist><label class="field">Review state<select name="review_status">${options(["unreviewed","confirmed"],component.review_status || "unreviewed")}</select></label><div class="form-actions"><button type="submit" class="button primary">Save correction</button><button type="button" class="button danger" data-action="delete-component">Remove</button></div></form>
    <hr class="section-divider"><dl class="fact-list"><dt>Drawing</dt><dd>${esc(sheetName(sheet))}</dd><dt>Coordinates</dt><dd><button class="result-title" data-action="grid-view">Open Grid</button></dd><dt>Source</dt><dd>${esc(evidenceText(component.source || component.provenance || sheet.source_metadata?.analysis?.method || "Drawing review"))}</dd></dl>
    ${component.evidence ? `<p class="section-note">${esc(evidenceText(component.evidence))}</p>` : ""}<img class="region-preview" src="/api/region?sheet_id=${encodeURIComponent(sheet.id)}&component_id=${encodeURIComponent(component.id)}" alt="Drawing region around selected component">
    <h3>Direct connections</h3><div class="result-list">${connectionRows(sheet, component.id)}</div>`;
}

function lineKind(kind) { return ({direct_undirected:"Equipment connection",pipe:"Process pipe",signal:"Instrument signal",unknown:"Unclassified line"})[kind] || "Unclassified line"; }

function connectionRows(sheet, componentId = null) {
  const edges = sheet.connections.filter(edge => !componentId || [edge.source,edge.target].includes(componentId));
  return edges.map(edge => {
    const first = sheet.components.find(item=>item.id===edge.source), second = sheet.components.find(item=>item.id===edge.target);
    return `<div class="result-item"><div><button class="result-title" data-action="select-connection" data-id="${dataValue(edge.id)}">${esc(first ? componentName(first) : `Missing: ${edge.source}`)} ↔ ${esc(second ? componentName(second) : `Missing: ${edge.target}`)}</button><div class="result-detail">${esc(lineKind(edge.kind))} · ${edge.review_status === "confirmed" ? "Reviewed" : edge.review_status === "rejected" ? "Rejected" : "Awaiting review"}</div></div></div>`;
  }).join("") || '<p class="section-note">No direct connections recorded.</p>';
}

function connectionsMarkup() {
  const sheet = selectedSheet();
  if (!sheet) return '<h2>Connections</h2><p class="empty-selection">Open a drawing to inspect direct connections.</p>';
  const edge = sheet.connections.find(item=>item.id===app.connectionId);
  if (!edge) return `<div class="eyebrow">2 · Review connections</div><h2>Direct connections <span class="muted tiny">${sheet.connections.length}</span></h2><p class="section-note">A connection joins immediate equipment neighbors. A route through another component is not direct. Signals and unclassified lines are excluded from the equipment matrix.</p><div class="connection-buttons"><button class="button small" data-action="matrix-view">View matrix</button><a class="button small" href="/api/export?${matrixQuery()}&format=connections" download>Save connections CSV</a></div><hr class="section-divider"><div class="result-list">${connectionRows(sheet)}</div>${linkedDrawingsButton(sheet)}`;
  const first=sheet.components.find(item=>item.id===edge.source),second=sheet.components.find(item=>item.id===edge.target);
  return `<button class="result-title back-link" data-action="all-connections">← All connections</button><div class="eyebrow">Selected connection</div><h2>Review connection</h2><p class="connection-endpoints">${esc(first?componentName(first):edge.source)} ↔ ${esc(second?componentName(second):edge.target)}</p><form id="connection-form"><label class="field">Connection type<select name="kind">${["direct_undirected","pipe","signal","unknown"].map(kind=>`<option value="${kind}"${edge.kind===kind?" selected":""}>${lineKind(kind)}</option>`).join("")}</select></label><label class="field">Observed direction<select name="direction">${[{id:"unknown",label:"Unknown / no visible arrow"},{id:"source_to_target",label:`${first?componentName(first):edge.source} → ${second?componentName(second):edge.target}`},{id:"target_to_source",label:`${second?componentName(second):edge.target} → ${first?componentName(first):edge.source}`},{id:"bidirectional",label:"Bidirectional"}].map(item=>`<option value="${item.id}"${(edge.direction||"unknown")===item.id?" selected":""}>${esc(item.label)}</option>`).join("")}</select></label><label class="field">Review state<select name="review_status">${options(["unreviewed","confirmed","rejected"],edge.review_status || "unreviewed")}</select></label><p class="section-note">The connectivity matrix is undirected. A visible arrow is recorded separately and does not change its symmetric entries.</p><button class="button primary" type="submit">Save connection</button></form>${edge.evidence?`<p class="section-note">${esc(evidenceText(edge.evidence))}</p>`:""}<button class="button danger" data-action="delete-connection">Remove connection</button>${linkedDrawingsButton(sheet)}`;
}

function linkedDrawingsButton(sheet) {
  const links=app.data.links.filter(link=>link.facility_id===app.facilityId&&[link.from?.sheet_id,link.to?.sheet_id].includes(sheet.id));
  return `<hr class="section-divider"><h3>Linked drawings</h3><p class="section-note">Review off-page connector pairs and follow connections between documents.</p><button class="button wide" data-action="linked-drawings">Linked drawings${links.length?` (${links.length})`:""}</button>`;
}

function linkedDrawingsMarkup() {
  const sheet=selectedSheet();if(!sheet)return '<p>Open a drawing first.</p>';
  const connectors=sheet.components.filter(isConnector);
  const selected=connectors.find(item=>item.id===app.linkSourceId)||connectors.find(item=>item.id===app.componentId)||connectors[0];
  const links=app.data.links.filter(link=>link.facility_id===app.facilityId&&[link.from?.sheet_id,link.to?.sheet_id].includes(sheet.id));
  return `<p class="section-note">${esc(sheetName(sheet))} · Confirm matches against both source images. Suggested links stay unconfirmed until reviewed. The equipment matrix displays the selected drawing only.</p><button class="button" data-action="suggest-links">Find connector candidates</button><div class="linked-card-list">${links.map(link=>linkedDrawingCard(link,sheet.id)).join("")||'<p class="empty-selection">No linked drawings recorded for this sheet.</p>'}</div><hr class="section-divider"><h3>Connect an off-page connector</h3>${selected?`<form id="link-form"><label class="field">Source connector<select name="from_component" id="link-source-component">${connectors.map(item=>`<option value="${dataValue(item.id)}"${item.id===selected.id?" selected":""}>${esc(componentName(item))}</option>`).join("")}</select></label><div class="form-grid"><label class="field">Destination drawing<select name="to_sheet" id="link-target-sheet" required><option value="">Choose a drawing</option>${activeSheets().filter(other=>other.id!==sheet.id&&other.active!==false).map(other=>`<option value="${dataValue(other.id)}">${esc(sheetName(other))}</option>`).join("")}</select></label><label class="field">Destination connector<select name="to_component" id="link-target-component" required><option value="">Choose destination drawing first</option></select></label></div><div class="link-pair" id="manual-link-preview"><div><img src="/api/region?sheet_id=${encodeURIComponent(sheet.id)}&component_id=${encodeURIComponent(selected.id)}" alt="Selected source connector"><span>${esc(sheetName(sheet))} · ${esc(componentName(selected))}</span></div><div><p class="section-note">Choose a destination to compare both connector images before confirming.</p></div></div><button class="button primary" type="submit">Confirm connector link</button></form>`:'<p class="section-note">This sheet has no off-page connector observations. Select or add a component with type connector to record a sheet continuation.</p>'}`;
}

function linkedDrawingCard(link,currentSheetId) {
  const from=app.data.sheets.find(item=>item.id===link.from?.sheet_id),to=app.data.sheets.find(item=>item.id===link.to?.sheet_id);
  const first=from?.components.find(item=>item.id===link.from?.component_id),second=to?.components.find(item=>item.id===link.to?.component_id);
  const remote=link.from?.sheet_id===currentSheetId?link.to:link.from;
  const endpoint=(sheet,component,ref,label)=>sheet&&component?`<div><img src="/api/region?sheet_id=${encodeURIComponent(sheet.id)}&component_id=${encodeURIComponent(component.id)}" alt="${label} connector on ${dataValue(sheetName(sheet))}"><strong>${esc(sheetName(sheet))}</strong><span>${esc(componentName(component))}</span><button class="result-title" data-action="linked-component" data-sheet="${dataValue(ref.sheet_id)}" data-id="${dataValue(ref.component_id)}">Open drawing</button></div>`:`<div><p class="inline-error">${label} endpoint is unavailable.</p></div>`;
  return `<article class="link-card"><span class="pill ${link.status==="confirmed"?"good":"warn"}">${esc(link.status||"suggested")}</span><div class="link-pair">${endpoint(from,first,link.from,"Source")}${endpoint(to,second,link.to,"Destination")}</div>${link.reason?`<p class="section-note">${esc(link.reason)}</p>`:""}<div class="form-actions">${link.status!=="confirmed"?`<button class="button small primary" data-action="link-status" data-id="${dataValue(link.id)}" data-status="confirmed">Confirm link</button>`:""}${link.status!=="rejected"?`<button class="button small" data-action="link-status" data-id="${dataValue(link.id)}" data-status="rejected">Reject link</button>`:""}${remote?`<button class="button small" data-action="linked-component" data-sheet="${dataValue(remote.sheet_id)}" data-id="${dataValue(remote.component_id)}">Open linked drawing</button>`:""}</div></article>`;
}

function updateManualLinkPreview() {
  const source=selectedSheet(),destination=app.data.sheets.find(item=>item.id===$("#link-target-sheet")?.value);
  const first=source?.components.find(item=>item.id===$("#link-source-component")?.value),second=destination?.components.find(item=>item.id===$("#link-target-component")?.value);
  const preview=$("#manual-link-preview");if(!preview)return;
  const endpoint=(sheet,item,label)=>sheet&&item?`<div><img src="/api/region?sheet_id=${encodeURIComponent(sheet.id)}&component_id=${encodeURIComponent(item.id)}" alt="${label} connector preview"><span>${esc(sheetName(sheet))} · ${esc(componentName(item))}</span></div>`:`<div><p class="section-note">Choose ${label.toLowerCase()} connector to compare its image.</p></div>`;
  preview.innerHTML=endpoint(source,first,"Source")+endpoint(destination,second,"Destination");
}

function showLinkedDrawings() {
  let dialog=$("#links-dialog");if(dialog)dialog.remove();dialog=document.createElement("dialog");dialog.id="links-dialog";dialog.className="linked-dialog";dialog.innerHTML=`<div class="dialog-heading"><h2>Linked drawings</h2><button type="button" class="icon-button" data-action="close-dialog" aria-label="Close">×</button></div><div id="linked-content">${linkedDrawingsMarkup()}</div>`;document.body.append(dialog);dialog.showModal();
}
function refreshLinkedDrawings() { const content=$("#linked-content");if(content)content.innerHTML=linkedDrawingsMarkup(); }

function matrixQuery() {
  return `facility_id=${encodeURIComponent(app.facilityId)}&sheet_id=${encodeURIComponent(app.sheetId)}` +
    (window.aipV3?.isCandidate() ? "&method=v3" : "");
}
function matrixKey(sheet) { return `${matrixQuery()}/${sheet.version}`; }

async function loadMatrix(sheet) {
  const key=matrixKey(sheet);
  if(app.matrixPending.has(key)||app.matrixData[sheet.id]?.key===key)return;
  app.matrixPending.add(key);
  const query=matrixQuery();
  try {
    const result=await api(`/api/matrix?${query}`);
    app.matrixData[sheet.id]={key,result};
  } catch(error) { app.matrixData[sheet.id]={key,error:error.message}; }
  finally {
    app.matrixPending.delete(key);
    if(app.view==="matrix"&&app.sheetId===sheet.id)renderMatrix();
  }
}

function renderMatrix() {
  const sheet=selectedSheet(),cached=sheet?app.matrixData[sheet.id]:null;
  const key=sheet?matrixKey(sheet):"";
  const current=cached?.key===key?cached:null;
  replaceWorkspace(`<div class="matrix-layout">${libraryMarkup()}<section class="matrix-page"><div class="page-heading"><div><div class="eyebrow">3 · Connectivity output</div><h1>Connectivity matrix</h1><p>${sheet?`${esc(sheetName(sheet))} · This drawing only. `:""}Immediate equipment adjacency. <strong>1</strong> means a direct connection is recorded; <strong>0</strong> means none is recorded. The matrix is symmetric with a zero diagonal.</p></div>${sheet?`<div class="toolbar-right"><button class="button" data-action="drawing-view">Back to drawing</button><a class="button primary" href="/api/export?${matrixQuery()}&format=matrix" download>Save matrix CSV</a><a class="button" href="/api/export?${matrixQuery()}&format=matrix-json" download>Save JSON</a></div>`:""}</div>${!sheet?'<div class="card"><p>Open an example or import a drawing to create its connectivity matrix.</p><button class="button primary" data-action="show-demo">Open example</button></div>':current?.error?`<div class="card"><h2>Matrix could not be loaded</h2><p class="inline-error">${esc(current.error)}</p><button class="button" data-action="refresh-matrix">Retry matrix</button></div>`:current?.result?matrixMarkup(current.result):'<div class="card matrix-loading"><div class="spinner"></div><p>Building the matrix from recorded components and connections…</p></div>'}</section></div>`);
  if(sheet&&window.aipV3)window.aipV3.matrixControls();
  if(sheet&&!current)loadMatrix(sheet);
}

function matrixMarkup(result) {
  const nodes=result.nodes||[],values=result.matrix||[],edges=result.direct_edges||[],stats=result.stats||{};
  const pairs=new Map(edges.map(edge=>[[edge.source_index,edge.target_index].sort((a,b)=>a-b).join(":"),edge]));
  const inspectNode=node=>`data-action="matrix-component" data-sheet="${dataValue(node.sheet_id)}" data-id="${dataValue(node.component_id)}"`;
  const table=nodes.length?`${nodes.length>5?'<p class="matrix-scroll-hint">↔ Scroll sideways to see more columns. Row labels stay fixed.</p>':""}<div class="matrix-scroll" tabindex="0" aria-label="Scrollable connectivity matrix"><table class="connectivity-table${nodes.length<=5?" compact-matrix":""}"><caption>Rows and columns use the same component order. Select a name to inspect its symbol or a 1 to inspect its connection.</caption><thead><tr><th class="matrix-corner" scope="col">Component</th>${nodes.map(node=>`<th scope="col"><button ${inspectNode(node)}>${esc(node.label)}</button></th>`).join("")}</tr></thead><tbody>${nodes.map((node,row)=>`<tr><th scope="row"><button ${inspectNode(node)}>${esc(node.label)}</button></th>${nodes.map((_,col)=>{const value=values[row]?.[col]===1?1:0,edge=pairs.get([row,col].sort((a,b)=>a-b).join(":")),observation=edge?.observations?.[0];return `<td class="${value?"connected":"empty-cell"}${row===col?" diagonal":""}">${value&&observation?`<button class="matrix-value" data-action="matrix-connection" data-sheet="${dataValue(observation.sheet_id)}" data-id="${dataValue(observation.connection_id)}" aria-label="Inspect connection ${dataValue(node.label)} and ${dataValue(nodes[col].label)}">1</button>`:value}</td>`;}).join("")}</tr>`).join("")}</tbody></table></div>`:'<p class="empty-selection">No equipment observations are available for this matrix. Analyze the drawing or add missing components first.</p>';
  const diagnostics=result.diagnostics||[],warnings=result.warnings||[];
  return `<div class="matrix-summary"><div><strong>${Number(stats.components??nodes.length)}</strong><span>Components</span></div><div><strong>${Number(stats.direct_connections??edges.length)}</strong><span>Direct connections</span></div><div><strong>${Number(stats.unread_labels||0)}</strong><span>Unread names</span></div><div><strong>${Number(stats.unreviewed_connections||0)}</strong><span title="Recorded connections awaiting review">To review</span></div></div>
    <section class="card matrix-card"><h2>Equipment adjacency</h2><p class="section-note">A path passing through another equipment item does not create an extra direct edge. Signals, rejected lines and unclassified connections do not create a 1. Unread names keep a unique label in the matrix.</p>${table}<details class="matrix-order"><summary>Row / column order (${nodes.length})</summary><ol>${nodes.map(node=>`<li><button class="result-title" ${inspectNode(node)}>${esc(node.label)}</button> <span class="muted tiny">${esc(node.type||"unknown")}</span></li>`).join("")}</ol></details></section>
    <div class="matrix-detail-grid"><section class="card"><h2>Direct connection list</h2>${edges.length?`<div class="result-list">${edges.map(edge=>{const observation=edge.observations?.[0];return `<div class="result-item"><div><strong class="direct-edge-label">${esc(edge.source_label)} ↔ ${esc(edge.target_label)}</strong><div class="result-detail">${edge.review_status==="confirmed"?"Reviewed":"Awaiting review"}${edge.observations?.length>1?` · ${edge.observations.length} observations, counted once`:""}</div></div>${observation?`<button class="button small" data-action="matrix-connection" data-sheet="${dataValue(observation.sheet_id)}" data-id="${dataValue(observation.connection_id)}">Inspect</button>`:""}</div>`;}).join("")}</div>`:'<p class="section-note">No included direct equipment connections are recorded.</p>'}</section><section class="card"><h2>Items to review</h2>${diagnostics.length||warnings.length?`<ul class="matrix-diagnostics">${diagnostics.map(item=>`<li><span class="pill ${item.severity==="error"?"bad":"warn"}">${esc(item.code||"Review")}</span><p>${esc(item.message||item.reason||"")}</p>${item.connection_id?`<button class="result-title" data-action="matrix-connection" data-sheet="${dataValue(item.sheet_id)}" data-id="${dataValue(item.connection_id)}">Inspect connection</button>`:item.component_id?`<button class="result-title" data-action="matrix-component" data-sheet="${dataValue(item.sheet_id)}" data-id="${dataValue(item.component_id)}">Inspect component</button>`:""}</li>`).join("")}${warnings.map(item=>`<li><p>${esc(typeof item==="string"?item:item.message||item.reason||JSON.stringify(item))}</p></li>`).join("")}</ul>`:'<p class="section-note">No matrix validation issues were reported. Check the observations against the source drawing before using the result.</p>'}</section></div>`;
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
    image.onload=()=>{if(app.imageKey!==key)return;app.image=image;fitCanvas();window.aipV3?.focusSelection();};
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
  if (app.view === "grid") {
    drawGrid(ctx, sheet);
  } else {
    for (const edge of sheet.connections || []) drawConnection(ctx, edge, sheet);
    drawObservedArrows(ctx, sheet);
    ctx.globalAlpha = 1;
    for (const component of sheet.components || []) {
      if (!component.bbox) continue;
      const [x0,y0,x1,y1] = component.bbox;
      const active = component.id === app.componentId;
      if (active) {
        ctx.fillStyle = "#19785f20"; ctx.fillRect(x0,y0,x1-x0,y1-y0);
      }
      ctx.strokeStyle = active ? "#19785f" : "#3890b2";
      ctx.lineWidth = (active ? 2.5 : 1) / app.scale;
      ctx.strokeRect(x0,y0,x1-x0,y1-y0);
    }
    if (app.labels) {
      for (const component of sheet.components || []) {
        if (component.bbox) drawEquipmentLabel(ctx, component);
      }
    }
  }
  const selected=selectedComponent();
  if(app.view!=="grid" && selected?.tag && selected.tag_bbox){
    const [l,t,r,b]=selected.tag_bbox;
    ctx.save();ctx.strokeStyle="#c46116";ctx.lineWidth=2/app.scale;
    ctx.setLineDash([4/app.scale,3/app.scale]);ctx.strokeRect(l,t,r-l,b-t);ctx.restore();
  }
  if(app.draft){ctx.strokeStyle="#19785f";ctx.lineWidth=2/app.scale;ctx.setLineDash([5/app.scale,3/app.scale]);if(app.draft.box){const[x0,y0,x1,y1]=app.draft.box;ctx.strokeRect(x0,y0,x1-x0,y1-y0);}else if(app.draft.points?.length){ctx.beginPath();app.draft.points.forEach((p,index)=>index?ctx.lineTo(...p):ctx.moveTo(...p));ctx.stroke();}ctx.setLineDash([]);}
  ctx.restore();const zoom=$("#zoom-label");if(zoom)zoom.textContent=`${Math.round(app.scale*100)}%`;
}

function drawConnection(ctx,edge,sheet){
  if(edge.kind==="signal"?!app.signals:!app.pipes)return;
  const from=sheet.components.find(item=>item.id===edge.source),to=sheet.components.find(item=>item.id===edge.target);
  if(!from||!to)return;
  const points=edge.path?.length>1?edge.path:[pointCenter(from),pointCenter(to)];
  const selected=edge.id===app.connectionId,unknown=!["direct_undirected","pipe","signal"].includes(edge.kind);
  ctx.strokeStyle=selected?"#dc7435":unknown?"#b78338":edge.kind==="signal"?"#8c66b1":"#277ba2";
  ctx.lineWidth=(selected?3:1.5)/app.scale;ctx.globalAlpha=.8;
  ctx.setLineDash(edge.kind==="signal"?[6/app.scale,4/app.scale]:unknown?[3/app.scale,3/app.scale]:[]);
  ctx.beginPath();points.forEach((point,index)=>index?ctx.lineTo(...point):ctx.moveTo(...point));ctx.stroke();ctx.setLineDash([]);
}

function drawObservedArrows(ctx,sheet){
  const selected=sheet.connections.find(edge=>edge.id===app.connectionId);
  const arrows=sheet.arrows ?? sheet.source_metadata?.analysis?.arrows ?? [];
  if(!arrows.length)return;
  const edges=new Map(sheet.connections.map(edge=>[edge.id,edge]));
  ctx.save();
  ctx.globalAlpha=1;
  for(const arrow of arrows){
    const tip=arrow.tip || arrow.arrow_tip;
    if(!Array.isArray(tip))continue;
    const edge=edges.get(arrow.connection_id);
    if(edge?.kind==="signal" ? !app.signals : !app.pipes)continue;
    const highlighted=selected && (selected.id===arrow.connection_id || selected.direction_evidence?.arrow_ids?.includes(arrow.id));
    const wings=arrow.wings || arrow.arrow_wings;
    ctx.strokeStyle=highlighted?"#dc7435":"#17694e";
    ctx.lineWidth=(highlighted?2.5:1.4)/app.scale;
    ctx.beginPath();
    if(Array.isArray(wings)&&wings.length===2){
      ctx.moveTo(...wings[0]);ctx.lineTo(...tip);ctx.lineTo(...wings[1]);
    }else{
      ctx.arc(...tip,3/app.scale,0,Math.PI*2);
    }
    ctx.stroke();
    if(highlighted){
      ctx.beginPath();ctx.arc(...tip,7/app.scale,0,Math.PI*2);ctx.stroke();
    }
  }
  ctx.restore();
}

function drawGrid(ctx, sheet) {
  const map = gridMap(sheet);
  if (!map?.axes) return;
  const [width, height] = canvasSize();
  const [left, top] = screenPoint([0, 0]);
  const [right, bottom] = screenPoint([sheet.width, sheet.height]);
  const bounds = {left:Math.max(0, left), top:Math.max(0, top), right:Math.min(width, right), bottom:Math.min(height, bottom)};
  if (bounds.right <= bounds.left || bounds.bottom <= bounds.top) return;

  ctx.save();
  const ratio = window.devicePixelRatio || 1;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.beginPath();
  ctx.rect(bounds.left, bounds.top, bounds.right - bounds.left, bounds.bottom - bounds.top);
  ctx.clip();
  const selected = selectedComponent();
  const record = selected && gridRecord(selected, sheet);
  const xs = map.axes.x.map(x => left + x * app.scale);
  const ys = map.axes.y.map(y => top + y * app.scale);
  if (app.gridVisible) {
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(47, 93, 101, .22)";
    ctx.beginPath();
    for (const x of xs) { ctx.moveTo(x, bounds.top); ctx.lineTo(x, bounds.bottom); }
    for (const y of ys) { ctx.moveTo(bounds.left, y); ctx.lineTo(bounds.right, y); }
    ctx.stroke();
    if (record?.display_point) {
      const [x, y] = screenPoint(record.display_point);
      ctx.strokeStyle = "rgba(14, 108, 81, .58)";
      ctx.beginPath();
      ctx.moveTo(x, bounds.top); ctx.lineTo(x, bounds.bottom);
      ctx.moveTo(bounds.left, y); ctx.lineTo(bounds.right, y);
      ctx.stroke();
    }
    drawGridRuler(ctx, xs, true, bounds, record?.coordinate?.[0]);
    drawGridRuler(ctx, ys, false, bounds, record?.coordinate?.[1]);
  }

  const occupied = new Map();
  for (const item of map.coordinates) {
    if (item.display_point) occupied.set(item.coordinate.join(","), item);
  }
  for (const item of occupied.values()) {
    const [x, y] = screenPoint(item.display_point);
    ctx.beginPath();
    ctx.arc(x, y, 2.8, 0, Math.PI * 2);
    ctx.fillStyle = "#2b7869"; ctx.fill();
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 1; ctx.stroke();
  }
  if (record?.display_point) {
    const [x, y] = screenPoint(record.display_point);
    if (selected.bbox) {
      const [l, t, r, b] = selected.bbox;
      const [sx, sy] = screenPoint([l, t]);
      ctx.strokeStyle = "#13624e"; ctx.lineWidth = 1.8;
      ctx.strokeRect(sx, sy, (r - l) * app.scale, (b - t) * app.scale);
    }
    if (record.source_anchor) {
      const [sx, sy] = screenPoint(record.source_anchor);
      if (Math.hypot(sx - x, sy - y) > 5) {
        ctx.setLineDash([3, 2]); ctx.strokeStyle = "#13624e"; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(sx, sy); ctx.stroke(); ctx.setLineDash([]);
      }
    }
    ctx.beginPath(); ctx.arc(x, y, 4.3, 0, Math.PI * 2);
    ctx.fillStyle = "#125341"; ctx.fill(); ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.stroke();
  }

  const labels = [];
  if (record?.display_point) {
    drawGridCoordinate(ctx, record, bounds, labels, true);
  }
  if (app.gridLabels) {
    for (const item of occupied.values()) {
      if (item.coordinate.join(",") !== record?.coordinate?.join(",")) {
        drawGridCoordinate(ctx, item, bounds, labels, false);
      }
    }
  }
  ctx.restore();
}

function drawGridRuler(ctx, positions, horizontal, bounds, selectedNumber) {
  const start = horizontal ? bounds.left : bounds.top;
  const end = horizontal ? bounds.right : bounds.bottom;
  const selectedPosition = positions[selectedNumber - 1];
  let previous = -Infinity;
  ctx.font = "11px Segoe UI, sans-serif";
  ctx.textBaseline = "middle"; ctx.textAlign = "center";
  positions.forEach((position, index) => {
    if (position < start + (horizontal ? 11 : 20) || position > end - 8) return;
    const active = index + 1 === selectedNumber;
    if (!active && (position - previous < 23 || Math.abs(position - selectedPosition) < 23)) return;
    previous = position;
    const text = String(index + 1), w = ctx.measureText(text).width + 6;
    const x = horizontal ? position : bounds.left + w / 2 + 2;
    const y = horizontal ? bounds.top + 8 : position;
    ctx.fillStyle = active ? "#d9eee6" : "#fffffff2";
    ctx.fillRect(x - w / 2, y - 7, w, 14);
    ctx.fillStyle = active ? "#10543e" : "#4a6567"; ctx.fillText(text, x, y);
  });
}

function drawGridCoordinate(ctx, item, bounds, labels, active) {
  const [x, y] = screenPoint(item.display_point);
  if (x < bounds.left || x > bounds.right || y < bounds.top || y > bounds.bottom) return;
  const text = "(" + item.coordinate.join(", ") + ")";
  ctx.font = (active ? "600 " : "") + "11px Segoe UI, sans-serif";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  const w = ctx.measureText(text).width + 8, h = 16;
  const left = Math.max(bounds.left, Math.min(x - w / 2, bounds.right - w));
  const top = y + 6 + h <= bounds.bottom ? y + 6 : y - 6 - h;
  const box = {left, top, right:left + w, bottom:top + h};
  if (!active && labels.some(other => box.left <= other.right + 2 && other.left <= box.right + 2 &&
      box.top <= other.bottom + 1 && other.top <= box.bottom + 1)) return;
  labels.push(box);
  ctx.fillStyle = active ? "#155845" : "#fffffff0"; ctx.fillRect(left, top, w, h);
  ctx.fillStyle = active ? "#fff" : "#305d55"; ctx.fillText(text, left + w / 2, top + h / 2);
}

function drawEquipmentLabel(ctx,component) {
  if(isJunction(component) && !component.tag && component.id!==app.componentId)return;
  if(component.id===app.componentId && component.tag_bbox && component.tag)return;
  const [x0,,x1,y1] = component.bbox, label = componentName(component);
  ctx.font=`${11/app.scale}px Segoe UI`;ctx.textAlign="center";ctx.textBaseline="top";
  const width=ctx.measureText(label).width+8/app.scale,x=(x0+x1)/2,y=y1+5/app.scale;
  ctx.fillStyle="#fffffff2";ctx.fillRect(x-width/2,y-1/app.scale,width,15/app.scale);
  ctx.fillStyle=component.id===app.componentId?"#14654e":"#254f62";ctx.fillText(label,x,y);
}

function eventPoint(event){const rect=$("#drawing-canvas").getBoundingClientRect();return[event.clientX-rect.left,event.clientY-rect.top];}
function hitComponent(point) {
  const sheet = selectedSheet();
  const components = [...(sheet?.components || [])].reverse();
  const inside = (item, pad) => item.bbox && point[0] >= item.bbox[0] - pad && point[0] <= item.bbox[2] + pad &&
    point[1] >= item.bbox[1] - pad && point[1] <= item.bbox[3] + pad;
  if (app.view === "grid") {
    const items = (gridMap(sheet)?.coordinates || []).filter(item => item.display_point);
    const distance = item => Math.hypot(point[0] - item.display_point[0], point[1] - item.display_point[1]) * app.scale;
    const nearest = items.filter(item => distance(item) <= 6).sort((a, b) => distance(a) - distance(b))[0];
    if (nearest) {
      const matches = items.filter(item => item.coordinate[0] === nearest.coordinate[0] && item.coordinate[1] === nearest.coordinate[1]);
      const next = (matches.findIndex(item => item.component_id === app.componentId) + 1) % matches.length;
      return components.find(item => item.id === matches[next].component_id);
    }
    const exact = components.find(item => inside(item, 0));
    if (exact) return exact;
  }
  return components.find(item => inside(item, 4 / app.scale));
}
function distanceSegment(point,a,b){const dx=b[0]-a[0],dy=b[1]-a[1];const t=Math.min(1,Math.max(0,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/(dx*dx+dy*dy||1)));return Math.hypot(point[0]-a[0]-t*dx,point[1]-a[1]-t*dy);}
function hitConnection(point){const sheet=selectedSheet();return(sheet.connections||[]).find(edge=>{const a=sheet.components.find(item=>item.id===edge.source),b=sheet.components.find(item=>item.id===edge.target);if(!a||!b)return false;const pts=edge.path?.length>1?edge.path:[pointCenter(a),pointCenter(b)];return pts.some((p,index)=>index&&distanceSegment(point,pts[index-1],p)<5/app.scale);});}

function pointerDown(event){if(event.button!==0)return;const canvas=event.currentTarget;canvas.setPointerCapture(event.pointerId);const screen=eventPoint(event),world=worldPoint(screen);const hit=hitComponent(world);app.pointer={screen,world,offset:[...app.offset],hit,moved:false,box:hit?.bbox?[...hit.bbox]:null};if(app.tool==="component"&&app.view!=="grid")app.draft={box:[...world,...world]};}
function pointerMove(event){if(!app.pointer)return;const point=eventPoint(event),world=worldPoint(point);const dx=point[0]-app.pointer.screen[0],dy=point[1]-app.pointer.screen[1];if(Math.hypot(dx,dy)>4)app.pointer.moved=true;if(app.tool==="component"&&app.view!=="grid"){app.draft={box:[...app.pointer.world,...world]};}else if(app.tool==="move"&&app.pointer.hit&&app.view!=="grid"){const sheet=selectedSheet();const shift=[Math.max(-app.pointer.box[0],Math.min(sheet.width-app.pointer.box[2],dx/app.scale)),Math.max(-app.pointer.box[1],Math.min(sheet.height-app.pointer.box[3],dy/app.scale))];app.pointer.hit.bbox=app.pointer.box.map((value,index)=>value+shift[index%2]);}else if(["select","move"].includes(app.tool)||app.view==="grid")app.offset=[app.pointer.offset[0]+dx,app.pointer.offset[1]+dy];drawCanvas();}
async function pointerUp(event){if(!app.pointer)return;const start=app.pointer;app.pointer=null;const point=worldPoint(eventPoint(event));try{if(app.tool==="component"&&app.view!=="grid"){const box=app.draft?.box;app.draft=null;if(start.moved&&box){const sheet=selectedSheet();const bbox=[Math.max(0,Math.min(box[0],box[2])),Math.max(0,Math.min(box[1],box[3])),Math.min(sheet.width,Math.max(box[0],box[2])),Math.min(sheet.height,Math.max(box[1],box[3]))].map(Math.round);if(bbox[2]-bbox[0]<3||bbox[3]-bbox[1]<3)return;const result=await action("save_component",{sheet_id:sheet.id,component:{type:"unknown",tag:"",bbox,normal_position:"unknown",review_status:"unreviewed",isolation_credit:"unverified"}});app.componentId=result.result?.item_id||result.result?.id||result.component?.id||selectedSheet().components.at(-1)?.id||"";app.tool="select";render();}}else if(app.tool==="move"&&start.hit&&start.moved&&app.view!=="grid"){await action("save_component",{sheet_id:app.sheetId,component:start.hit},{message:"Equipment position saved."});}else if(!start.moved){const hit=hitComponent(point);if((app.tool==="pipe"||app.tool==="signal")&&app.view!=="grid"){if(!app.draft){if(!hit)return toast("Choose the first equipment item.");app.draft={source:hit.id,points:[pointCenter(hit)]};}else if(hit){if(hit.id===app.draft.source)return toast("Choose a different endpoint.");const draft=app.draft;app.draft=null;await action("save_connection",{sheet_id:app.sheetId,connection:{source:draft.source,target:hit.id,kind:app.tool==="pipe"?"direct_undirected":"signal",path:[...draft.points,pointCenter(hit)],review_status:"confirmed"}},{message:"Connection saved."});}else app.draft.points.push(point.map(Math.round));drawCanvas();}else{app.componentId=hit?.id||"";app.connectionId=hit||app.view==="grid"?"":hitConnection(point)?.id||"";app.tab=app.connectionId?"connections":"components";render();}}}catch(error){toast(error.message,true);app.draft=null;if(start.box&&start.hit)start.hit.bbox=start.box;drawCanvas();}drawCanvas();}

function openSheet(id, componentId = "", view = app.view, connectionId = "") {
  const sheet = app.data.sheets.find(item => item.id === id);
  if (!sheet) return;
  app.sheetId = sheet.id;
  app.facilityId = sheet.facility_id;
  app.componentId = componentId;
  app.connectionId = connectionId;
  app.view = view;
  app.tab = connectionId ? "connections" : "components";
  app.tool = "select";
  app.pointer = app.draft = null;
  app.imageKey = "";
  remember("sheet", app.sheetId);
  remember("facility", app.facilityId);
  render();
}
function openConnection(sheetId,connectionId) {
  openSheet(sheetId, "", "drawings", connectionId);
}

document.addEventListener("click",async event=>{
  const viewButton=event.target.closest("[data-view]");if(viewButton){app.view=viewButton.dataset.view;app.tool="select";app.draft=null;render();return;}
  const tabButton=event.target.closest("[data-tab]");if(tabButton){app.tab=tabButton.dataset.tab;render();return;}
  const toolButton=event.target.closest("[data-tool]");if(toolButton){app.tool=toolButton.dataset.tool;app.draft=null;render();return;}
  const button=event.target.closest("[data-action]");if(!button)return;
  const name=button.dataset.action;
  try {
    if(name==="close-dialog")button.closest("dialog").close();
    else if(name==="retry")location.reload();
    else if(name==="import"){$("#import-facility").innerHTML=facilitiesMarkup();$("#import-dialog").showModal();}
    else if(name==="show-demo")$("#demo-dialog").showModal();
    else if(name==="load-demo"){button.closest("dialog").close();const output=await action("demo",{kind:button.dataset.kind},{refresh:false});app.facilityId=output.facility_id||output.result?.facility_id||app.facilityId;app.sheetId=output.result?.target?.sheet_id||output.result?.sheet_ids?.[0]||"";app.componentId=output.result?.target?.component_id||"";app.view="drawings";app.tab="components";await loadState();toast(output.job?"Example queued for image analysis.":"Example opened.");}
    else if(name==="new-facility"){
      const name=prompt("Drawing set name")?.trim();
      if(name){
        const result=await action("create_facility",{name});
        $("#import-facility").innerHTML=facilitiesMarkup(result.result.id);
      }
    }
    else if(name==="open-sheet")openSheet(button.dataset.id);
    else if(name==="select-component"){app.componentId=button.dataset.id;app.connectionId="";app.tab="components";render();}
    else if(name==="all-components"){app.componentId="";app.connectionId="";app.tab="components";render();}
    else if(name==="select-connection"){app.connectionId=button.dataset.id;app.componentId="";app.tab="connections";render();}
    else if(name==="all-connections"){app.connectionId="";app.componentId="";app.tab="connections";render();}
    else if(name==="linked-drawings")showLinkedDrawings();
    else if(name==="suggest-links"){await action("suggest_links",{facility_id:app.facilityId},{message:"Connector candidates refreshed."});refreshLinkedDrawings();}
    else if(name==="link-status"){const link=app.data.links.find(item=>item.id===button.dataset.id);if(link){await action("save_link",{link:{...link,status:button.dataset.status}},{message:button.dataset.status==="confirmed"?"Connector link confirmed.":"Connector link rejected."});refreshLinkedDrawings();}}
    else if(name==="linked-component"){const dialog=$("#links-dialog");if(dialog)dialog.close();openSheet(button.dataset.sheet,button.dataset.id,"drawings");}
    else if(name==="matrix-component")openSheet(button.dataset.sheet,button.dataset.id,"drawings");
    else if(name==="matrix-connection")openConnection(button.dataset.sheet,button.dataset.id);
    else if(name==="matrix-view"){app.view="matrix";app.draft=null;app.tool="select";render();}
    else if(name==="drawing-view"){app.view="drawings";render();}
    else if(name==="refresh-matrix"){const sheet=selectedSheet();if(sheet){delete app.matrixData[sheet.id];renderMatrix();}}
    else if(name==="zoom-in")zoomCanvas(1.3);
    else if(name==="zoom-out")zoomCanvas(1/1.3);
    else if(name==="fit")fitCanvas();
    else if(name==="grid-view"){app.view="grid";app.tool="select";render();}
    else if(name==="refresh-grid"){delete app.gridData[app.sheetId];renderDrawings();}
    else if(name==="sheet-settings")showSheetDetails();
    else if(name==="setup"){const form=$("#setup-form");form.elements.actor.value=app.data.settings.actor||"Local reviewer";form.elements.use_geometry_reference.checked=app.data.settings.use_geometry_reference!==false;$("#setup-dialog").showModal();}
    else if(name==="delete-component"){if(app.componentId&&confirm(`Remove ${componentName(selectedComponent())} and its connections?`)){await action("delete_component",{sheet_id:app.sheetId,component_id:app.componentId});app.componentId="";render();}}
    else if(name==="delete-connection"){if(app.connectionId&&confirm("Remove this connection?")){await action("delete_connection",{sheet_id:app.sheetId,connection_id:app.connectionId});app.connectionId="";render();}}
    else if(name==="reanalyze")await action("reanalyze",{sheet_id:app.sheetId},{message:"Image analysis started. Saved corrections will be retained where they can be matched."});
  }catch(error){toast(error.message,true);}
});

document.addEventListener("change",async event=>{
  if(event.target.id==="grid-layout"){app.gridLayout=event.target.value;remember("grid-layout",app.gridLayout);renderDrawings();}
  if(event.target.dataset.layer){app[event.target.dataset.layer]=event.target.checked;drawCanvas();}
  if(event.target.id==="link-source-component"){app.linkSourceId=event.target.value;updateManualLinkPreview();}
  if(event.target.id==="link-target-sheet"){const sheet=app.data.sheets.find(item=>item.id===event.target.value);$("#link-target-component").innerHTML=(sheet?.components||[]).filter(isConnector).map(item=>`<option value="${dataValue(item.id)}">${esc(componentName(item))}</option>`).join("")||'<option value="">No connector observations</option>';updateManualLinkPreview();}
  if(event.target.id==="link-target-component")updateManualLinkPreview();
  if(event.target.id==="import-files")$("#import-file-summary").textContent=Array.from(event.target.files).map(file=>file.name).join(", ");
});

document.addEventListener("input",event=>{if(event.target.id==="sheet-search"){const start=event.target.selectionStart;app.search=event.target.value;render();const input=$("#sheet-search");input.focus();input.setSelectionRange(start,start);}});

document.addEventListener("submit",async event=>{
  if(event.target.id==="sheet-form")return;
  event.preventDefault();const form=event.target,data=new FormData(form);const submit=form.querySelector('[type="submit"]');if(submit)submit.disabled=true;
  try {
    if(form.id==="component-form"){const component={...selectedComponent(),tag:data.get("tag").trim(),type:data.get("type").trim(),review_status:data.get("review_status")};await action("save_component",{sheet_id:app.sheetId,component},{message:"Component correction saved."});}
    else if(form.id==="connection-form"){const connection={...selectedSheet().connections.find(item=>item.id===app.connectionId),...Object.fromEntries(data.entries())};await action("save_connection",{sheet_id:app.sheetId,connection},{message:"Connection correction saved. The matrix will use this record."});}
    else if(form.id==="link-form"){await action("save_link",{link:{from:{sheet_id:app.sheetId,component_id:data.get("from_component")},to:{sheet_id:data.get("to_sheet"),component_id:data.get("to_component")},status:"confirmed"}},{message:"Off-page connector link confirmed."});refreshLinkedDrawings();}
    else if(form.id==="setup-form"){await action("settings",{settings:{actor:data.get("actor").trim(),use_geometry_reference:data.get("use_geometry_reference")==="on"}},{message:"Extraction setup saved."});$("#setup-dialog").close();}
    else if(form.id==="import-form"){const files=$("#import-files").files;if(!files.length)return;const body=new FormData();for(const file of files)body.append("files",file,file.name);const facility=$("#import-facility").value;body.append("facility_id",facility);await api("/api/import",{method:"POST",body});$("#import-dialog").close();form.reset();if(app.facilityId!==facility){app.facilityId=facility;app.sheetId="";app.componentId="";app.connectionId="";}app.view="drawings";await loadState();toast("Import queued. Converted images and analysis progress appear in the drawing list.");}
  }catch(error){toast(error.message,true);}finally{if(submit&&submit.isConnected)submit.disabled=false;}
});

function showSheetDetails(){
  const sheet=selectedSheet();if(!sheet)return;let dialog=$("#sheet-dialog");if(dialog)dialog.remove();dialog=document.createElement("dialog");dialog.id="sheet-dialog";
  dialog.innerHTML=`<form id="sheet-form"><div class="dialog-heading"><h2>Drawing details</h2><button type="button" class="icon-button" data-action="close-dialog" aria-label="Close">×</button></div><label class="field">Title<input name="title" value="${dataValue(sheet.title||"")}" required></label><div class="revision-grid"><label class="field">Drawing number<input name="drawing_number" value="${dataValue(sheet.drawing_number||"")}"></label><label class="field">Sheet number<input name="sheet_number" value="${dataValue(sheet.sheet_number||1)}"></label><label class="field">Revision<input name="revision" value="${dataValue(sheet.revision||"")}"></label></div><label class="layer-toggle" style="margin:15px 0"><input name="active" type="checkbox"${sheet.active!==false?" checked":""}>Current drawing revision</label><p class="section-note">Source: ${esc(sheet.filename||"")}</p><div class="dialog-actions"><button class="button primary" type="submit">Save details</button></div></form>`;
  document.body.append(dialog);$("#sheet-form").addEventListener("submit",async event=>{event.preventDefault();event.stopPropagation();try{await action("update_sheet",{sheet_id:sheet.id,patch:{...Object.fromEntries([...new FormData(event.target).entries()].filter(([key])=>key!=="active")),active:new FormData(event.target).get("active")==="on"}});dialog.close();}catch(error){toast(error.message,true);}});dialog.showModal();
}

document.addEventListener("keydown",event=>{
  if(["INPUT","SELECT","TEXTAREA"].includes(event.target.tagName)||document.querySelector("dialog[open]"))return;
  if(event.key==="Escape"){app.draft=null;app.tool="select";app.componentId=app.connectionId="";render();}
  if(!["drawings","grid"].includes(app.view))return;
  if(event.key==="+"||event.key==="="){event.preventDefault();zoomCanvas(1.3);}
  else if(event.key==="-"){event.preventDefault();zoomCanvas(1/1.3);}
  else if(event.key.toLowerCase()==="f"){event.preventDefault();fitCanvas();}
  else if(event.key==="Delete"&&app.view==="drawings"){const button=$(app.componentId?'[data-action="delete-component"]':'[data-action="delete-connection"]');button?.click();}
});

let resizeTimer;
window.addEventListener("resize",()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(["drawings","grid"].includes(app.view)&&selectedSheet())setupCanvas();},100);});
loadState().catch(error=>{$("#main").innerHTML=`<div class="empty-workspace"><h1>Workspace connection unavailable</h1><p>${esc(error.message)}</p><button class="button" data-action="retry">Retry connection</button></div>`;});
