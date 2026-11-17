"use strict";

window.aipV3 = (() => {
  const traces = new Map();
  const pending = new Set();
  const sources = new Map();
  const preferences = {};
  let profiles = [];
  const post = (url, body) => api(url, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
  });
  const isCandidate = () => sources.get(app.sheetId) === "v3" && traces.has(app.sheetId);
  const method = () => isCandidate() ? "v3" : "v2";
  const baselineLabel = () => sourceSheet()?.source_metadata?.analysis?.method === "drawing-reference"
    ? "V2 · Reference-assisted" : "Current method (V2)";

  function displaySheet(sheet) {
    if (!sheet || !isCandidate()) return sheet;
    const trace = traces.get(sheet.id);
    return {...sheet, version:`v3/${sheet.version}/${trace.created_at || ""}`, components: trace.components, connections: trace.connections};
  }

  function changeSource(value) {
    sources.set(app.sheetId, value);
    app.componentId = app.connectionId = "";
    app.tool = "select";
    app.pointer = app.draft = null;
    render();
  }

  function syncDrawing() {
    const id = app.sheetId;
    if (!id || traces.has(id) || pending.has(id)) return;
    pending.add(id);
    api(`/api/v3/trace?sheet_id=${encodeURIComponent(id)}`).then(({trace}) => {
      if (traces.has(id)) return;
      traces.set(id, trace || null);
      if (trace && app.sheetId === id) render();
    }).catch(error => {
      if (app.sheetId === id) toast(error.message, true);
    }).finally(() => pending.delete(id));
  }

  function sourceSwitch() {
    if (!traces.get(app.sheetId)) return "";
    return `<label class="v3-source">Method <select data-v3-source aria-label="Displayed extraction method">
      <option value="v2"${!isCandidate() ? " selected" : ""}>${esc(baselineLabel())}</option>
      <option value="v3"${isCandidate() ? " selected" : ""}>AI connections (V3)</option>
    </select></label>`;
  }

  function focusSelection() {
    const sheet = selectedSheet();
    if (app.view !== "drawings" || !sheet || !app.image) return;
    const edge = sheet.connections.find(item => item.id === app.connectionId);
    const boxes = sheet.components.filter(item => edge
      ? item.id === edge.source || item.id === edge.target : item.id === app.componentId)
      .filter(item => Array.isArray(item.bbox)).map(item => item.bbox);
    const points = [...(edge?.path || []), ...boxes.flatMap(([left,top,right,bottom]) => [[left,top],[right,bottom]])];
    if (!points.length) return;
    let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
    for (const [x,y] of points) {
      left = Math.min(left,x); right = Math.max(right,x);
      top = Math.min(top,y); bottom = Math.max(bottom,y);
    }
    const [width,height] = canvasSize();
    if (!width || !height) return;
    app.scale = Math.min(3,width/Math.max(right-left+80,100),height/Math.max(bottom-top+80,100));
    app.offset = [(width-(right+left)*app.scale)/2,(height-(bottom+top)*app.scale)/2];
    drawCanvas();
  }

  function controls() {
    return `${sourceSwitch()}<button type="button" class="button" data-v3="trace">Find connections with AI</button>
      <button type="button" class="button" data-v3="ask">Ask about drawing</button>`;
  }

  function matrixControls() {
    const heading = $(".matrix-page .page-heading");
    if (!heading) return;
    const actions = document.createElement("div");
    actions.className = "v3-matrix-actions";
    actions.innerHTML = controls() + '<button class="button" data-v3="report">Benchmark results</button>';
    heading.after(actions);
  }

  function inspector(sheet) {
    const trace = traces.get(sheet.id);
    const edge = sheet.connections.find(item => item.id === app.connectionId);
    const component = sheet.components.find(item => item.id === app.componentId);
    const heading = `<p class="section-note">AI observations from ${esc(trace.model || trace.provider)}.</p>`;
    if (app.tab === "connections") {
      if (!edge) {
        const excluded = trace.excluded_connections || [];
        return `<h2>AI connections</h2>${heading}<div class="result-list">${connectionRows(sheet)}</div>
          ${excluded.length ? `<details class="matrix-order"><summary>${excluded.length} excluded proposals</summary><ul>${excluded.map(item =>
            `<li>${esc(item.exclusion_reason || item.uncertainty || "Insufficient evidence")}</li>`).join("")}</ul></details>` : ""}`;
      }
      const label = id => componentName(sheet.components.find(item => item.id === id));
      const from = label(edge.source), to = label(edge.target);
      const direction = {source_to_target: `${from} → ${to}`, target_to_source: `${to} → ${from}`, bidirectional: "Bidirectional"};
      return `<button class="result-title back-link" data-action="all-connections">← All AI connections</button>
        <h2>${esc(from)} ↔ ${esc(to)}</h2>${heading}<dl class="fact-list"><dt>Line type</dt><dd>${esc(lineKind(edge.kind))}</dd>
        <dt>Flow direction</dt><dd>${esc(direction[edge.direction] || "Unknown")}</dd></dl>
        <p>${esc(evidenceText(edge.evidence) || "Review the traced route on the drawing.")}</p>`;
    }
    if (component) return `<button class="result-title back-link" data-action="all-components">← All AI components</button>
      <h2>${esc(componentName(component))}</h2>${heading}<p>${esc(component.type || "Unknown type")} · Awaiting review</p>
      <p class="section-note">${esc(evidenceText(component.evidence))}</p><h3>Connections</h3>${connectionRows(sheet, component.id)}`;
    return `<h2>AI components</h2>${heading}<div class="result-list">${sheet.components.map(item =>
      `<div class="result-item"><div><button class="result-title" data-action="select-component" data-id="${dataValue(item.id)}">${esc(componentName(item))}</button>
      <div class="result-detail">${esc(item.type || "Unknown type")}</div></div></div>`).join("") || "<p>No components found.</p>"}</div>`;
  }

  function dialog(title) {
    const element = document.createElement("dialog");
    element.className = "v3-dialog";
    element.innerHTML = `<div class="dialog-heading"><h2>${esc(title)}</h2><button type="button" class="icon-button" aria-label="Close" data-action="close-dialog">×</button></div>
      <div class="v3-body" aria-live="polite"><p>Loading…</p></div>`;
    element.addEventListener("close", () => element.remove(), {once:true});
    document.body.append(element);
    element.showModal();
    return element;
  }

  async function loadProfiles() {
    const response = await api("/api/v3/providers");
    profiles = response.providers;
  }

  function connectSetup(panel) {
    panel.innerHTML = `<p class="section-note">For questions inside AIP, choose an available model in Ask about drawing. External clients that support local MCP can read the saved drawing graph through this endpoint.</p>
      <p><a href="/api/v3/client-config" target="_blank" rel="noopener">View Claude Desktop configuration ↗</a></p>
      <label class="field">Read-only MCP endpoint<input readonly value="${dataValue(location.origin + "/mcp")}"></label>
      <p class="section-note">This address is reachable on this computer. A hosted client needs a lab-provided reachable deployment.</p>
      <button type="button" class="button small" data-v3="key">Show local client token</button>`;
  }

  function factMarkup(reply) {
    const facts = Array.isArray(reply.facts) ? reply.facts : [];
    if (!facts.length) return `<p>${esc(reply.answer)}</p>`;
    return `<ul class="v3-facts">${facts.map(({fact, citation}) => `<li>${esc(fact)}<br>
      <button type="button" class="result-title" data-v3="citation" data-kind="${dataValue(citation.kind)}" data-id="${dataValue(citation.id)}">Inspect ${esc(citation.label)}</button></li>`).join("")}</ul>`;
  }

  async function showModelDialog(task) {
    const sheet = sourceSheet(), selectedMethod = method();
    if (!sheet) return;
    const asking = task === "ask";
    const modal = dialog(asking ? "Ask about drawing" : "Find equipment connections");
    const body = $(".v3-body", modal);
    try {
      await loadProfiles();
      if (!modal.open) return;
      let choices = profiles.filter(profile => profile[task]);
      if (!choices.length) throw Error("No model profiles are available for this action.");
      const choice = choices.find(item => item.id === preferences[task]) || choices.find(item => item.ready) || choices[0];
      const graph = selectedSheet();
      const tagged = selectedComponent()?.tag || graph?.components.find(item => item.tag && String(item.type).includes("pump"))?.tag
        || graph?.components.find(item => item.tag)?.tag;
      const example = tagged ? `What connects directly to ${tagged}?` : "How many direct equipment connections are recorded?";
      body.innerHTML = `<p class="section-note">${esc(sheetName(sheet))}${asking ? ` · ${selectedMethod === "v3" ? "AI connections (V3)" : esc(baselineLabel())}` : ""}</p>
        <form class="v3-form"><label class="field">Model connection<select name="provider">${choices.map(item =>
          `<option value="${dataValue(item.id)}"${item.id === choice.id ? " selected" : ""}>${esc(item.label || item.id)}${item.ready ? "" : " · Setup needed"}</option>`).join("")}</select></label>
        <p class="v3-profile section-note"></p>
        <details class="v3-connect" hidden><summary>Connect model for this session</summary>
          <label class="field">API model ID<input name="model_id" type="text" maxlength="256" autocomplete="off" spellcheck="false"></label>
          <label class="field">Provider API key<input name="api_key" type="password" maxlength="1000" autocomplete="off" spellcheck="false"></label>
          <p class="section-note">AIP keeps this credential in server memory until the workbench closes.</p>
          <button type="button" class="button small" data-connect-session>Connect model</button>
        </details>
        ${asking ? `<label class="field">Question<textarea name="question" rows="3" maxlength="4000" required>${esc(example)}</textarea></label>` : ""}
        <div class="dialog-actions"><button type="submit" class="button primary">${asking ? "Ask model" : "Trace drawing"}</button></div></form>
        <div class="v3-result" role="status"></div>`;
      const form = $("form", body), status = $(".v3-result", body), connect = $(".v3-connect", form);
      let busy = false;
      function updateProfile() {
        const profile = choices.find(item => item.id === form.elements.provider.value);
        preferences[task] = profile.id;
        const data = asking ? "the question and this sheet’s recorded observations" : "the selected sheet image";
        const destination = profile.external === false || ["local-ml","local-graph"].includes(profile.id) ? "locally" : "at the configured endpoint";
        $(".v3-profile", form).textContent = profile.ready
          ? `${profile.model || profile.label} processes ${data} ${destination}.`
          : ["openai", "anthropic"].includes(profile.id) ? "Connect your model below, or configure it on the server." : profile.error || "Configure this provider and model on the server.";
        connect.hidden = !["openai", "anthropic"].includes(profile.id);
        connect.open = false;
        form.elements.api_key.value = "";
        form.elements.model_id.value = profile.model || "";
        $("button[type=submit]", form).disabled = !profile.ready;
      }
      form.elements.provider.onchange = updateProfile;
      updateProfile();
      $("[data-connect-session]", connect).addEventListener("click", async event => {
        if (busy) return;
        const provider = form.elements.provider.value, model = form.elements.model_id.value.trim();
        let key = form.elements.api_key.value.trim();
        if (!model || !key) { status.textContent = "Enter both the model ID and its provider API key."; return; }
        busy = true;
        form.elements.api_key.value = "";
        event.currentTarget.disabled = true;
        form.elements.provider.disabled = true;
        $("button[type=submit]", form).disabled = true;
        try {
          await post("/api/v3/connect", {provider, model, api_key:key});
          await loadProfiles();
          choices = profiles.filter(item => item[task]);
          const active = choices.find(item => item.id === provider);
          form.elements.provider.selectedOptions[0].textContent = active.label;
          updateProfile();
          status.textContent = "Model configured for this workbench session.";
        } catch (error) { status.textContent = error.message; }
        finally {
          key = "";
          busy = false;
          $("[data-connect-session]", connect).disabled = false;
          form.elements.provider.disabled = false;
          $("button[type=submit]", form).disabled = !choices.find(item => item.id === provider).ready;
        }
      });
      form.addEventListener("submit", async event => {
        event.preventDefault();
        event.stopPropagation();
        if (busy) return;
        busy = true;
        const provider = form.elements.provider.value;
        form.elements.provider.disabled = true;
        $("button[type=submit]", form).disabled = true;
        status.textContent = asking ? "Finding supporting observations…" : "Tracing equipment connections…";
        try {
          if (asking) {
            const reply = await post("/api/v3/ask", {sheet_id:sheet.id, provider, method:selectedMethod, question:form.elements.question.value});
            status.innerHTML = `<h3>${esc(reply.model || reply.provider)}</h3>${factMarkup(reply)}`;
            status.dataset.method = selectedMethod;
            status.dataset.sheet = sheet.id;
          } else {
            const {trace} = await post("/api/v3/trace", {sheet_id:sheet.id, provider});
            traces.set(sheet.id, trace);
            delete app.matrixData[sheet.id];
            delete app.gridData[sheet.id];
            if (app.sheetId === sheet.id && modal.open) {
              changeSource("v3");
              app.tab = "connections";
              render();
              modal.close();
              toast(`AI found ${trace.connections.length} connection observations. Select a route to inspect it.`);
            } else if (app.sheetId === sheet.id) render();
          }
        } catch (error) { status.textContent = error.message; }
        finally { busy = false; form.elements.provider.disabled = false; updateProfile(); }
      });
    } catch (error) { body.textContent = error.message; }
  }

  function reportTable(report, metric) {
    const score = value => Number.isFinite(value) ? value.toFixed(3) : "—";
    return `<table class="v3-scores"><thead><tr><th>Method</th><th>TP</th><th>FP</th><th>FN</th><th>F1</th><th>Gap to ${esc(report.target_f1)}</th></tr></thead><tbody>${["baseline","ai"].map(key => {
      const result = report.methods?.[key]?.[metric];
      return `<tr><th>${key === "baseline" ? "Current (V2)" : "AI (V3)"}</th><td>${result?.tp ?? "—"}</td><td>${result?.fp ?? "—"}</td>
        <td>${result?.fn ?? "—"}</td><td>${score(result?.f1)}</td><td>${score(result?.gap_to_target)}</td></tr>`;
    }).join("")}</tbody></table>`;
  }

  function cohortMarkup(report) {
    const named = !!report.methods?.baseline?.named_edges;
    const coverage = report.methods?.ai?.endpoint_coverage;
    return `<section><h3>${esc(report.cohort)}</h3><p class="section-note">${esc(report.review_status)}</p>
        <p class="section-note">${esc(report.completed_pairs)} of ${esc(report.drawing_count)} drawings compared · ${esc(report.model || report.ai_method)}</p>
        <strong>${named ? "Direct connections with named endpoints" : "Direct connections by endpoint location"}</strong>
        ${reportTable(report,named ? "named_edges" : "localized_edges")}
        ${named ? `<details><summary>Score using endpoint locations only</summary>${reportTable(report,"localized_edges")}</details>` :
          '<p class="section-note">Endpoint name accuracy requires reference equipment tags. This cohort has no named-edge score.</p>'}
        ${coverage ? `<p class="section-note">V3 located both endpoints on ${esc(coverage.both_endpoints_located)} of ${esc(coverage.reference_pairs)} reference connections.</p>` : ""}
        ${(report.warnings || []).map(item => `<p class="section-note">${esc(item)}</p>`).join("")}</section>`;
  }

  async function showReport() {
    const modal = dialog("Benchmark results"), body = $(".v3-body", modal);
    try {
      const response = await api("/api/v3/report");
      const reports = (response.reports || [response.report || response]).filter(report => report?.methods);
      if (!reports.length) throw Error("A comparison report is not available yet.");
      body.innerHTML = `<p class="section-note">TP counts correct connections, FP counts extra connections, and FN counts missed connections.</p>
        ${reports.map(cohortMarkup).join("<hr class='section-divider'>")}`;
    } catch (error) { body.textContent = error.message; }
  }

  document.addEventListener("change", event => {
    if (event.target.matches("[data-v3-source]")) changeSource(event.target.value);
  });
  document.addEventListener("click", async event => {
    if (event.target.closest('[data-action="select-connection"], [data-action="select-component"]')) focusSelection();
    const button = event.target.closest("[data-v3]");
    if (!button) return;
    const operation = button.dataset.v3;
    if (operation === "trace" || operation === "ask") await showModelDialog(operation);
    if (operation === "report") await showReport();
    if (operation === "citation") {
      const result = button.closest(".v3-result");
      sources.set(result.dataset.sheet, result.dataset.method);
      button.closest("dialog").close();
      if (button.dataset.kind === "connection") openConnection(result.dataset.sheet, button.dataset.id);
      else openSheet(result.dataset.sheet, button.dataset.kind === "component" ? button.dataset.id : "", "drawings");
    }
    if (operation === "key") {
      try {
        const integration = await api("/api/integration");
        const field = document.createElement("input");
        field.readOnly = true;
        field.setAttribute("aria-label", "Read-only client token");
        field.value = integration.api_key;
        button.replaceWith(field);
        field.select();
      } catch (error) { toast(error.message, true); }
    }
  });

  const setup = $("#setup-form");
  document.title = "AIP V3 · P&ID Matrix Builder";
  $(".brand-mark").textContent = "AIP V3";
  const connector = document.createElement("details");
  connector.innerHTML = "<summary>Connect an external AI client</summary><div></div>";
  setup.querySelector(".dialog-actions").before(connector);
  connectSetup($("div", connector));
  $("#setup-dialog").addEventListener("close", () => {
    connector.open = false;
    connectSetup($("div", connector));
  });
  return {isCandidate, displaySheet, drawingControls: controls, matrixControls, syncDrawing, inspector, sourceSwitch, focusSelection};
})();
