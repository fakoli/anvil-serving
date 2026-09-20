import { badge, button, el, empty, heading, kv, notice, route, select, table, timestamp } from "./common.js";
import { query, request } from "./api.js";
import { evidenceDialog, openOperation } from "./operations.js";
import { experimentsView } from "./experiments.js";

const tabs = [["overview", "Overview"], ["run", "Run flow"], ["compare", "Compare"], ["evidence", "Evidence"], ["events", "Events"], ["runs", "All runs"]];
// The cadence leaves the independently enforced 2.5s browser deadline inside
// the visible 5s active and 10s discovery budgets.
const SOURCE_TIMEOUT = 2500, DISCOVERY_INTERVAL = 6500, ACTIVE_INTERVAL = 1500;
const TERMINAL = new Set(["succeeded", "failed", "cancelled", "completed"]);
export const workbenchRoute = (tab, id) => id ? route("workbench", id, tab) : route("workbench", tab);

const activeRun = (run) => !TERMINAL.has(String(run.status || "").toLowerCase());
const sourceLabel = (source) => source.label || source.id || "Unknown source";
const sourceStatus = (state) => state.status || (state.loading ? "loading" : "unavailable");
function stale(state) {
  state.status = "stale";
  state.items = state.items.map((item) => ({ ...item, freshness: "stale" }));
}
function coalesceRuns(states) {
  const correlated = new Map(), rows = [];
  for (const state of states.values()) for (const item of state.items) {
    if (!item || typeof item !== "object" || typeof item.id !== "string") continue;
    const correlation = typeof item.correlation_id === "string" && item.correlation_id ? item.correlation_id : null;
    if (!correlation) { rows.push({ ...item, source_label: sourceLabel(state.source), representations: [item] }); continue; }
    const existing = correlated.get(correlation);
    if (existing) { existing.representations.push(item); continue; }
    const row = { ...item, source_label: sourceLabel(state.source), representations: [item] };
    correlated.set(correlation, row); rows.push(row);
  }
  return rows.sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
}

function createRunFeed(ctx, update) {
  const states = new Map(); let stopped = false, catalogTimer = null, catalogController = null, catalogGeneration = 0;
  const render = () => { if (!stopped) update(states, coalesceRuns(states)); };
  const clear = (state) => {
    clearTimeout(state.timer); state.timer = null; state.generation += 1;
    state.controller?.abort(); state.controller = null;
  };
  const delayFor = (state) => state.items.some(activeRun) ? ACTIVE_INTERVAL : DISCOVERY_INTERVAL;
  const schedule = (state, delay = delayFor(state)) => {
    clearTimeout(state.timer);
    if (!stopped && !document.hidden) state.timer = setTimeout(() => void readSource(state), delay);
  };
  const readSource = async (state) => {
    if (stopped || document.hidden || state.controller) return;
    const generation = ++state.generation, controller = new AbortController();
    state.loading = true; state.controller = controller; render();
    try {
      const value = await request(query(`runs/${encodeURIComponent(state.source.id)}`, { limit: 100 }), { signal: controller.signal, timeout: SOURCE_TIMEOUT });
      if (stopped || state.generation !== generation) return;
      state.items = Array.isArray(value?.items) ? value.items : [];
      state.next_cursor = value?.next_cursor || null;
      state.sources = Array.isArray(value?.sources) ? value.sources : [];
      state.status = state.sources.some((item) => item?.status === "stale") ? "stale" : state.sources.some((item) => item?.status === "unavailable") ? "unavailable" : "fresh";
    } catch (error) {
      if (error.name === "AbortError" || stopped || state.generation !== generation) return;
      if (error.status === 403) { state.items = []; clear(state); states.delete(state.source.id); render(); return; }
      if (state.items.length) stale(state); else state.status = "unavailable";
    } finally {
      if (stopped || state.generation !== generation) return;
      state.loading = false; state.controller = null; render(); schedule(state);
    }
  };
  const reconcileCatalog = async () => {
    if (stopped || document.hidden || catalogController) return;
    const generation = ++catalogGeneration, controller = new AbortController();
    try {
      catalogController = controller;
      const value = await request("run-sources", { signal: controller.signal, timeout: SOURCE_TIMEOUT });
      if (stopped || generation !== catalogGeneration) return;
      const available = new Set();
      for (const source of Array.isArray(value?.items) ? value.items : []) {
        if (!source || typeof source.id !== "string") continue;
        available.add(source.id);
        const existing = states.get(source.id);
        if (existing) { existing.source = source; continue; }
        const state = { source, items: [], status: "loading", sources: [], loading: false, controller: null, timer: null, next_cursor: null, generation: 0 };
        states.set(source.id, state); void readSource(state);
      }
      for (const [sourceId, state] of states) if (!available.has(sourceId)) { clear(state); states.delete(sourceId); }
      render();
    } catch (error) { if (error.name !== "AbortError") render(); }
    finally {
      if (generation !== catalogGeneration) return;
      catalogController = null;
      if (!stopped && !document.hidden) catalogTimer = setTimeout(() => void reconcileCatalog(), DISCOVERY_INTERVAL);
    }
  };
  const visibility = () => {
    if (document.hidden) {
      clearTimeout(catalogTimer);
      catalogGeneration += 1; catalogController?.abort(); catalogController = null;
      for (const state of states.values()) { clear(state); if (state.items.length) stale(state); }
      render(); return;
    }
    void reconcileCatalog();
    for (const state of states.values()) void readSource(state);
  };
  const stop = () => {
    if (stopped) return;
    stopped = true; clearTimeout(catalogTimer); catalogGeneration += 1; catalogController?.abort(); catalogController = null;
    for (const state of states.values()) clear(state);
    document.removeEventListener("visibilitychange", visibility);
  };
  document.addEventListener("visibilitychange", visibility);
  ctx.signal.addEventListener("abort", stop, { once: true });
  return { start: reconcileCatalog };
}

function runTable(runs, ctx) {
  return table(["Run", "Source", "Outcome", "Freshness", "Updated", ""], runs.map((run) => [
    el("div", {}, el("strong", { text: run.title || run.label || run.action_id || run.native_id || run.id }), el("small", { class: "mono", text: run.native_id || run.id })),
    run.source_label || run.source || "Not reported", badge(run.native_state || run.execution_outcome || run.status), badge(run.freshness || "fresh"), timestamp(run.updated_at, ctx.zone),
    el("a", { class: "text-link", href: workbenchRoute("events", run.id), "data-focus-key": `run:${run.id}`, text: "Open run →" }),
  ]));
}
function sourceCoverage(states, ctx) {
  return el("div", { class: "run-source-grid", "aria-label": "Run source coverage" }, [...states.values()].map((state) =>
    {
      const meta = state.sources[0] || {}, status = sourceStatus(state);
      const coverage = status === "unavailable" ? "Source unavailable" : state.items.length ? `${state.items.length} retained rows` : "No matching retained runs";
      return el("div", { class: "run-source-card" },
        el("strong", { text: sourceLabel(state.source) }), badge(status),
        el("small", { text: state.loading ? "Refreshing independently" : coverage }),
        meta.observed_at ? timestamp(meta.observed_at, ctx.zone) : null,
        meta.partial ? el("small", { text: "Partial coverage" }) : null,
        meta.truncated || state.next_cursor ? el("small", { text: "More retained history is available" }) : null,
      );
    }));
}
function runDetails(current, ctx, selectedId) {
  const representations = current?.representations || (current ? [current] : []);
  if (!current) return empty(selectedId ? "The selected retained run is unavailable from the current authorized sources." : "No retained run is available from the configured sources.");
  return el("section", { class: "focus-card stack", "aria-label": "Selected run" },
    el("span", { class: "eyebrow", text: `RETAINED RUN / ${current.id.slice(0, 16)}` }),
    el("div", { class: "focus-top" }, el("div", {}, el("h2", { text: current.title || current.label || current.native_id || "Selected run" }), el("p", { text: `${current.source_label || current.source || "Owner source"} · ${current.native_id || current.id}` })), badge(current.status)),
    kv([["Freshness", badge(current.freshness || "fresh")], ["Updated", timestamp(current.updated_at, ctx.zone)], ["Native IDs", representations.map((row) => row.native_id || row.id).join(", ")], ["Correlation", current.correlation_id || "No owner-declared correlation"]]),
  );
}
function evidencePanel(current, ctx) {
  if (!current) return empty("Select a retained run to inspect its available references.");
  if (current.evidence_id) return button("Open retained evidence", () => evidenceDialog(current.evidence_id, ctx), "primary");
  const refs = Array.isArray(current.evidence_refs) ? current.evidence_refs : [];
  const operationEvidence = current.representations?.find((row) => row.source === "operations" && row.evidence_refs?.[0]?.artifact_id)?.evidence_refs?.[0]?.artifact_id;
  if (operationEvidence) return button("Open retained evidence", () => evidenceDialog(operationEvidence, ctx), "primary");
  return refs.length ? el("section", { class: "panel stack" }, el("h2", { text: "Owner artifact references" }), notice("This owner exposes references here. Artifact detail is not yet available through a run-detail API."), kv(refs.flatMap((ref, index) => [[`Artifact ${index + 1}`, ref.artifact_id || "Not reported"], [`Digest ${index + 1}`, ref.sha256 || "Not reported"]]))) : empty("This owner reported no retained evidence reference.");
}
function syncSelect(control, options, value) {
  const signature = JSON.stringify(options);
  if (control.dataset.options !== signature && document.activeElement !== control) {
    control.replaceChildren(...options.map(([key, label]) => el("option", { value: key, text: label })));
    control.dataset.options = signature;
  }
  if (document.activeElement !== control) control.value = value;
}
function focusKey(node) {
  if (!node.contains(document.activeElement)) return null;
  return document.activeElement.getAttribute("data-focus-key") || document.activeElement.getAttribute("href");
}
function restoreFocus(node, key) {
  if (!key) return;
  for (const item of node.querySelectorAll("[data-focus-key], a[href]")) {
    if (item.getAttribute("data-focus-key") === key || item.getAttribute("href") === key) {
      item.focus();
      return;
    }
  }
}

export async function workbenchView(ctx, id, requestedTab = "overview") {
  const tab = tabs.some(([name]) => name === requestedTab) ? requestedTab : "overview";
  const root = el("div", { class: "workbench-page workbench-flow stack", "data-story": "US-BENCH-01" });
  root.append(el("span", { class: "eyebrow", text: "WORKBENCH" }), heading("A place for the whole experiment.", "Follow active and retained owner runs without changing their authority.", [el("a", { class: "quiet-button button-link", href: route("playground"), text: "Open playground" }), el("a", { class: "primary button-link", href: workbenchRoute("run"), text: "+ New experiment" })]));
  const tablist = el("nav", { class: "local-tabs", role: "tablist", "aria-label": "Workbench run sections" }, ...tabs.map(([name, label]) => el("a", { id: `workbench-tab-${name}`, role: "tab", "aria-selected": name === tab ? "true" : "false", "aria-controls": "workbench-panel", tabindex: name === tab ? "0" : "-1", href: workbenchRoute(name, id), text: label })));
  tablist.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault(); const links = [...tablist.querySelectorAll("[role=tab]")], index = links.indexOf(event.target.closest("[role=tab]"));
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    links[next].focus();
  });
  root.append(tablist);
  const panel = el("section", { id: "workbench-panel", role: "tabpanel", "aria-labelledby": `workbench-tab-${tab}`, class: "stack" });
  root.append(panel);
  if (tab === "run") { panel.append(await experimentsView(ctx)); return root; }
  const filters = { source: "", state: "" };
  const coverage = el("div");
  const data = el("div", { class: "stack" });
  let latestStates = new Map(), latestRuns = [], sourceFilter, stateFilter, toolbar;
  const render = (states, runs) => {
    latestStates = states; latestRuns = runs;
    const current = id ? runs.find((run) => run.id === id || (run.source === "operations" && run.native_id === id)) : runs[0];
    const focused = focusKey(data);
    coverage.replaceChildren(sourceCoverage(states, ctx));
    data.replaceChildren();
    if (tab === "overview") {
      const active = runs.filter(activeRun);
      data.append(runDetails(current, ctx, id), el("section", { class: "panel stack" }, el("h2", { text: "Active runs" }), active.length ? runTable(active.slice(0, 20), ctx) : empty("No source currently reports an active run.")), el("section", { class: "panel stack" }, el("h2", { text: "Recent runs" }), runs.length ? runTable(runs.slice(0, 12), ctx) : empty("No retained runs are available yet.")));
    } else if (tab === "runs") {
      const visible = runs.filter((run) => (!filters.source || run.source === filters.source) && (!filters.state || run.status === filters.state));
      const sourceOptions = [["", "All sources"], ...[...states.values()].map((state) => [state.source.id, sourceLabel(state.source)])];
      const statesAvailable = [...new Set(runs.map((run) => run.status).filter(Boolean))].sort().map((value) => [value, value]);
      syncSelect(sourceFilter, sourceOptions, filters.source);
      syncSelect(stateFilter, [["", "All states"], ...statesAvailable], filters.state);
      data.append(el("section", { class: "panel stack" }, el("h2", { text: "All retained runs" }), visible.length ? runTable(visible, ctx) : empty("No retained runs match these filters.")));
    } else if (tab === "compare") data.append(el("section", { class: "panel stack" }, el("h2", { text: "Compare retained outcomes" }), notice("Comparison requires compatible measurement dimensions and remains unavailable until the comparison contract is supplied.")));
    else if (tab === "evidence") data.append(runDetails(current, ctx, id), evidencePanel(current, ctx));
    else if (tab === "events") {
      const operations = current?.representations?.filter((row) => row.source === "operations") || [];
      const operation = operations[0];
      const detail = operation ? button("Open operation detail", () => openOperation(operation.native_id, ctx), "primary") : null;
      if (detail) detail.dataset.focusKey = `operation:${operation.native_id}`;
      data.append(runDetails(current, ctx, id), el("section", { class: "panel stack" }, el("h2", { text: "Available run detail" }), detail || notice("This source exposes native IDs and artifact references only; it has no declared run-detail route.")));
    }
    restoreFocus(data, focused);
  };
  if (tab === "runs") {
    sourceFilter = select([["", "All sources"]], filters.source, (event) => { filters.source = event.target.value; render(latestStates, latestRuns); }, { "aria-label": "Run source" });
    stateFilter = select([["", "All states"]], filters.state, (event) => { filters.state = event.target.value; render(latestStates, latestRuns); }, { "aria-label": "Run state" });
    toolbar = el("div", { class: "run-toolbar" }, sourceFilter, stateFilter);
  }
  panel.append(coverage);
  if (toolbar) panel.append(toolbar);
  panel.append(data);
  const feed = createRunFeed(ctx, render);
  data.append(notice("Discovering authorized run sources…"));
  void feed.start();
  return root;
}
