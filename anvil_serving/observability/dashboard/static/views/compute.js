import { badge, button, el, empty, field, heading, kv, metric, notice, route, select, table, timestamp } from "./common.js";
import { getSession, request, workbenchRequest } from "./api.js";
import { actionButtons, previewAction } from "./operations.js";

let selected = "", hostChoice = "", detailTab = "logs";
window.addEventListener("observatory-session-changed", () => { selected = ""; hostChoice = ""; detailTab = "logs"; });
export async function computeView(ctx) {
  const root = el("div", { class: "workbench-page stack", "data-story": "US-COMPUTE-01" }, heading("Compute", "See what each host is serving. Inspect its runtime, read logs, and review owner controls.", [button("Refresh state", ctx.refresh, "quiet-button")]));
  const hosts = (ctx.fleet.hosts || []).filter(host => !ctx.host || host.id === ctx.host);
  const rows = [...(ctx.fleet.serves || []).map(serve => ({ ...serve, identity: serve.container, manager: "Managed serve", kind: "serve" })), ...(ctx.fleet.services || []).map(service => ({ ...service, kind: "service" }))];
  if (ctx.host) hostChoice = ctx.host;
  else if (hostChoice && !hosts.some(host => host.id === hostChoice)) hostChoice = "";
  const cards = el("div", { class: "grid three compute-hosts", "aria-label": "Compute hosts" });
  for (const host of hosts) {
    const owned = rows.filter(row => row.host_id === host.id);
    cards.append(el("article", { class: "card stack" },
      el("div", { class: "card-head" }, el("div", {}, el("h2", { text: host.display_name || host.id }), el("p", { class: "meta", text: host.platform || "Platform not reported" })), badge(host.controller?.status)),
      kv([["Workloads", `${owned.filter(row => row.runtime_state === "running").length} running / ${owned.length} declared`], ["CPU", metric(host.resources?.cpu_utilization)], ["Memory", metric(host.resources?.memory_used)], ["Telemetry", badge(host.telemetry?.status)]]),
      button(hostChoice === host.id ? "Showing this host" : "Show workloads", () => { hostChoice = host.id; selected = ""; ctx.render(); }, hostChoice === host.id ? "primary" : "quiet-button")));
  }
  root.append(cards);
  const hostSelect = select([["", "All visible hosts"], ...hosts.map(host => [host.id, host.display_name || host.id])], hostChoice, () => { hostChoice = hostSelect.value; selected = ""; ctx.render(); });
  const search = el("input", { type: "search", placeholder: "Model, container, or service", autocomplete: "off" });
  const workloads = el("section", { class: "panel stack" }, el("h2", { text: "Host workloads" }));
  const detail = el("section", { class: "panel stack" });
  root.append(el("div", { class: "filters" }, field("Host", hostSelect), field("Find workload", search)), workloads, detail);
  let detailAbort = new AbortController();
  ctx.signal.addEventListener("abort", () => detailAbort.abort(), { once: true });
  function renderRows() {
    const found = rows.filter(row => (!ctx.host || row.host_id === ctx.host) && (!hostChoice || row.host_id === hostChoice) && `${row.display_name} ${row.id} ${row.model || ""} ${row.identity || ""}`.toLowerCase().includes(search.value.toLowerCase()));
    if (!found.some(row => row.id === selected)) selected = found[0]?.id || "";
    workloads.replaceChildren(el("h2", { text: "Host workloads" }), found.length ? table(["Workload / model", "Host", "Container or service", "State", ""], found.map(row => [
      el("div", {}, el("strong", { text: row.display_name || row.id }), el("p", { class: "meta", text: row.observed_model || row.model || row.manager })),
      hosts.find(host => host.id === row.host_id)?.display_name || row.host_id,
      el("code", { text: row.identity || "Owner has not reported an identity" }), badge(row.runtime_state),
      button(row.id === selected ? "Selected" : "Inspect", () => { selected = row.id; renderRows(); }, "quiet-button")
    ]), "Declared workloads and owner observations") : empty("No declared workloads match this host and search."));
    renderDetail(found.find(row => row.id === selected));
  }
  function renderDetail(current) {
    detailAbort.abort(); detailAbort = new AbortController();
    const signal = detailAbort.signal;
    detail.replaceChildren();
    if (!current) { detail.hidden = true; return; }
    detail.hidden = false;
    const controlsSlot = el("div", { class: "actions" }, el("p", { class: "meta", text: "Reading owner controls…" }));
    const content = el("div", { class: "stack" });
    const tabs = el("nav", { class: "local-tabs", "aria-label": "Workload detail" });
    for (const [id, label] of [["logs", "Logs"], ["configuration", "Configuration"], ["exec", "Exec"]]) tabs.append(button(label, () => { detailTab = id; renderDetail(current); }, detailTab === id ? "primary" : "quiet-button"));
    detail.append(el("div", { class: "page-heading" }, el("div", {}, el("h2", { text: current.display_name || current.id }), el("p", { class: "meta", text: `${current.manager || "Workload"} · ${current.identity || "Identity not reported"}` }))), controlsSlot, tabs, content);
    let controls;
    request(`controls?resource=${encodeURIComponent(current.id)}`, { signal }).then(value => {
      if (signal.aborted) return; controls = value;
      controlsSlot.replaceChildren(actionButtons(current.id, value, ctx, { exclude: ["container.exec", "configuration.apply", "experiment.start"] }));
      if (detailTab === "exec") renderExec();
    }).catch(error => { if (!signal.aborted) { controlsSlot.replaceChildren(notice(error.message, "warning")); if (detailTab === "exec") content.replaceChildren(notice("Diagnostics are unavailable until the owner controls can be verified.", "warning")); } });
    function renderExec() {
      const action = controls?.actions?.find(item => item.id === "container.exec" && item.supported && item.permitted);
      if (!action || current.exec?.status !== "available") {
        content.replaceChildren(notice("This workload has no declared container diagnostics. Add named diagnostics to its owner's workload policy to enable Exec.", "warning")); return;
      }
      const command = select(current.exec.commands || [], current.exec.commands?.[0] || "");
      content.replaceChildren(el("h3", { text: "Container diagnostics" }), el("p", { text: "Run a declared command in this exact container. Review the target and command before execution; output is retained with the operation." }), field("Diagnostic", command), button("Review diagnostic", () => previewAction(current.id, action, ctx, { parameters: { command_id: command.value } }), "primary", !getSession()?.operate));
    }
    if (detailTab === "logs") {
      const output = el("pre", { class: "evidence-json", tabindex: "0", "aria-label": "Workload log output", text: "Reading bounded owner logs…" });
      const status = el("p", { class: "meta", role: "status" });
      const refresh = button("Refresh logs", readLogs, "quiet-button");
      content.append(el("div", { class: "actions" }, refresh, el("a", { href: route("observability", "logs"), text: "Search retained container logs →" })), status, output);
      async function readLogs() {
        refresh.disabled = true;
        try {
          const value = await workbenchRequest(`workloads/${encodeURIComponent(current.id)}/logs`, { signal, timeout: 15000 });
          if (signal.aborted) return;
          output.textContent = value.text || "The owner returned an empty log tail.";
          status.replaceChildren(`${value.tail || 200} line limit · ${value.truncated ? "truncated" : "within display limit"} · Read `, timestamp(value.observed_at, ctx.zone));
        } catch (error) { if (!signal.aborted) { output.textContent = ""; status.replaceChildren(notice(error.message, "warning")); } }
        finally { if (!signal.aborted) refresh.disabled = false; }
      }
      readLogs();
    } else if (detailTab === "configuration") content.append(kv([["Workload", current.id], ["Runtime identity", current.identity || "Not reported"], ["Host", current.host_id], ["Manager", current.manager], ["Configured model", current.model], ["Observed model", current.observed_model], ["Readiness", badge(current.readiness)], ["Admission", badge(current.admission)], ["GPU owners", (current.gpu_ids || []).join(", ") || "Not reported"]]), el("a", { href: route("models"), text: "Browse model and recipe settings →" }));
    else content.append(el("p", { class: "meta", text: "Checking declared diagnostics…" }));
  }
  search.addEventListener("input", renderRows); renderRows(); return root;
}
