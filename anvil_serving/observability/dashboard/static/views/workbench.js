import { badge, button, el, empty, heading, kv, notice, route, select, table, timestamp } from "./common.js";
import { query, request } from "./api.js";
import { evidenceDialog, metadataDialog, openOperation } from "./operations.js";
import { experimentsView } from "./experiments.js";

const tabs = [["overview", "Overview"], ["run", "Run flow"], ["compare", "Compare"], ["evidence", "Evidence"], ["events", "Events"], ["runs", "All runs"]];
// The cadence leaves the independently enforced 2.5s browser deadline inside
// the visible 5s active and 10s discovery budgets.
const SOURCE_TIMEOUT = 2500, DISCOVERY_INTERVAL = 6500, ACTIVE_INTERVAL = 1500;
const HISTORY_LIMIT = 400, PAGE_LIMIT = 100;
const TERMINAL = new Set(["succeeded", "failed", "cancelled", "completed", "imported", "retained"]);
const BENCHMARK_CORRELATION = /^benchmark-job-[a-f0-9]{64}$/;
export const workbenchRoute = (tab, id) => id ? route("workbench", id, tab) : route("workbench", tab);

const activeRun = (run) => !TERMINAL.has(String(run.status || "").toLowerCase());
const sourceLabel = (source) => source.label || source.id || "Unknown source";
const sourceStatus = (state) => state.status || (state.loading ? "loading" : "unavailable");
function stale(state) {
  state.status = "stale";
  state.head = state.head.map((item) => ({ ...item, freshness: "stale" }));
  state.history = state.history.map((item) => ({ ...item, freshness: "stale" }));
  rebuild(state);
}
function rebuild(state) {
  const unique = new Map();
  for (const item of [...state.head, ...state.history])
    if (item && typeof item.id === "string" && !unique.has(item.id)) unique.set(item.id, item);
  state.items = [...unique.values()].slice(0, PAGE_LIMIT + HISTORY_LIMIT);
}
function validCorrelation(item) {
  return (item?.source === "operations" || item?.source === "benchmark")
    && typeof item.correlation_id === "string" && BENCHMARK_CORRELATION.test(item.correlation_id);
}
export function coalesceRuns(states) {
  const correlated = new Map(), rows = [];
  for (const state of states.values()) for (const item of state.items) {
    if (!item || typeof item !== "object" || typeof item.id !== "string") continue;
    const row = { ...item, source_label: sourceLabel(state.source), representations: [item] };
    if (!validCorrelation(item)) { rows.push(row); continue; }
    const group = correlated.get(item.correlation_id) || { operations: [], benchmarks: [] };
    group[item.source === "operations" ? "operations" : "benchmarks"].push(row);
    correlated.set(item.correlation_id, group);
  }
  for (const [correlation, group] of correlated) {
    if (group.operations.length === 1 && group.benchmarks.length === 1) {
      const operation = group.operations[0], benchmark = group.benchmarks[0];
      rows.push({ ...benchmark, id: operation.id, source_label: "Operation + benchmark", correlation_id: correlation,
        representations: [operation.representations[0], benchmark.representations[0]] });
    } else rows.push(...group.operations, ...group.benchmarks);
  }
  return rows.sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")) || String(left.id).localeCompare(String(right.id)));
}

function createRunFeed(ctx, update) {
  const states = new Map(); let stopped = false, catalogTimer = null, catalogController = null, catalogGeneration = 0;
  const render = () => { if (!stopped) update(states, coalesceRuns(states)); };
  const clear = (state) => {
    clearTimeout(state.timer); state.timer = null; state.generation += 1;
    state.controller?.abort(); state.controller = null;
    state.moreController?.abort(); state.moreController = null;
  };
  // Only the current owner head is actively polled. Older cursor pages are
  // retained evidence and display their freshness rather than a false live claim.
  const delayFor = (state) => state.source.kind === "imported" || !state.head.some(activeRun) ? DISCOVERY_INTERVAL : ACTIVE_INTERVAL;
  const schedule = (state, delay = delayFor(state)) => {
    clearTimeout(state.timer);
    if (!stopped && !document.hidden) state.timer = setTimeout(() => void readSource(state), delay);
  };
  const readSource = async (state) => {
    if (stopped || document.hidden || state.controller || state.moreController) return;
    const generation = ++state.generation, controller = new AbortController();
    state.loading = true; state.controller = controller; render();
    try {
      const value = await request(query(`runs/${encodeURIComponent(state.source.id)}`, { limit: PAGE_LIMIT }), { signal: controller.signal, timeout: SOURCE_TIMEOUT });
      if (stopped || state.generation !== generation) return;
      state.head = Array.isArray(value?.items) ? value.items : [];
      const headIds = new Set(state.head.map((item) => item?.id));
      state.history = state.history.map((item) => !headIds.has(item?.id) && activeRun(item) ? { ...item, freshness: "retained" } : item);
      // Once history is loaded, keep paging its original owner snapshot. A
      // newer head may be polled without creating gaps behind that snapshot.
      if (!state.snapshotActive && !state.historyExpired)
        state.next_cursor = typeof value?.next_cursor === "string" && value.next_cursor ? value.next_cursor : null;
      state.sources = Array.isArray(value?.sources) ? value.sources : [];
      state.status = state.sources.some((item) => item?.status === "stale") ? "stale" : state.sources.some((item) => item?.status === "unavailable") ? "unavailable" : "fresh";
      rebuild(state);
    } catch (error) {
      if (error.name === "AbortError" || stopped || state.generation !== generation) return;
      if (error.status === 403) { state.head = []; state.history = []; rebuild(state); clear(state); states.delete(state.source.id); render(); return; }
      if (state.items.length) stale(state); else state.status = "unavailable";
    } finally {
      if (stopped || state.generation !== generation) return;
      state.loading = false; state.controller = null; render(); schedule(state);
    }
  };
  const loadMore = async (state, restart = false) => {
    if (stopped || document.hidden || state.controller || state.moreController || (!restart && (!state.next_cursor || state.historyExpired))) return;
    const cursor = state.next_cursor;
    if (!cursor) return;
    const generation = ++state.generation, controller = new AbortController();
    state.moreLoading = true; state.moreController = controller; state.snapshotActive = true;
    if (restart) { state.history = []; state.historyExpired = false; rebuild(state); }
    render();
    try {
      const value = await request(query(`runs/${encodeURIComponent(state.source.id)}`, { limit: PAGE_LIMIT, cursor }), { signal: controller.signal, timeout: SOURCE_TIMEOUT });
      if (stopped || state.generation !== generation) return;
      // Keep the first page that issued this cursor too. It closes the gap
      // when a newer polling head has inserted rows before that snapshot.
      const snapshot = state.history.length ? state.history : state.head;
      const known = new Set([...state.head, ...snapshot].map((item) => item?.id));
      const incoming = Array.isArray(value?.items) ? value.items : [];
      const candidate = [...snapshot, ...incoming.filter((item) => item?.id && !known.has(item.id))];
      if (candidate.length > HISTORY_LIMIT) { state.historyExhausted = true; state.next_cursor = null; }
      else { state.history = candidate; state.next_cursor = typeof value?.next_cursor === "string" && value.next_cursor ? value.next_cursor : null; }
      state.sources = Array.isArray(value?.sources) ? value.sources : state.sources;
      rebuild(state);
    } catch (error) {
      if (error.name === "AbortError" || stopped || state.generation !== generation) return;
      if (error.status === 403) { state.head = []; state.history = []; rebuild(state); clear(state); states.delete(state.source.id); render(); return; }
      // Retain the last good page until the user explicitly restarts a cursor snapshot.
      if (error.status === 409 || error.status === 400) { state.historyExpired = true; state.next_cursor = null; }
      else if (state.items.length) stale(state); else state.status = "unavailable";
    } finally {
      if (stopped || state.generation !== generation) return;
      state.moreLoading = false; state.moreController = null; render(); schedule(state);
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
        const state = { source, head: [], history: [], items: [], status: "loading", sources: [], loading: false, moreLoading: false, controller: null, moreController: null, timer: null, next_cursor: null, snapshotActive: false, historyExpired: false, historyExhausted: false, generation: 0 };
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
  const restartHistory = (state) => {
    if (stopped || document.hidden || state.controller || state.moreController) return;
    state.history = []; state.snapshotActive = false; state.historyExpired = false; state.historyExhausted = false; state.next_cursor = null; rebuild(state); render();
    void readSource(state);
  };
  return { start: reconcileCatalog, loadMore, restartHistory };
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
    kv([["Freshness", badge(current.freshness || "fresh")], ["Updated", timestamp(current.updated_at, ctx.zone)], ["Native IDs", representations.map((row) => row.native_id || row.id).join(", ")], ["Correlation", validCorrelation(current) ? current.correlation_id : "No owner-declared benchmark correlation"]]),
  );
}
function evidenceReference(current) {
  const source = current?.representations?.find((row) => row.source === "evidence") || (current?.source === "evidence" ? current : null);
  const ref = source?.evidence_refs?.find((item) => item && typeof item.artifact_id === "string" && typeof item.sha256 === "string");
  return ref ? { artifact_id: ref.artifact_id, sha256: ref.sha256 } : null;
}
async function openEvidenceDetail(ref, ctx) {
  const pairs = [["Artifact", ref.artifact_id], ["Digest", ref.sha256]];
  try {
    const detail = await request(query("runs/evidence/detail", ref), { timeout: SOURCE_TIMEOUT, signal: ctx.signal });
    if (ctx.signal.aborted) return;
    const summary = detail?.summary && typeof detail.summary === "object" ? detail.summary : {};
    pairs.push(["Model", detail?.model], ["Suite", detail?.suite], ["Profile", detail?.profile], ["State", detail?.native_state], ["Evidence kind", detail?.evidence_kind], ["Locally measured", detail?.locally_measured === true ? "Yes" : detail?.locally_measured === false ? "No" : "Not reported"], ["Validation", summary.validation], ["Observed", detail?.observed_at ? timestamp(detail.observed_at, ctx.zone) : null]);
    metadataDialog("Retained evidence", pairs);
  } catch (error) { if (!ctx.signal.aborted && error.name !== "AbortError") metadataDialog("Retained evidence", pairs, notice(error.message, "danger")); }
}
function evidencePanel(current, ctx) {
  if (!current) return empty("Select a retained run to inspect its available references.");
  if (current.evidence_id) return button("Open retained evidence", () => evidenceDialog(current.evidence_id, ctx), "primary");
  const ref = evidenceReference(current);
  if (ref) return el("section", { class: "panel stack" }, el("h2", { text: "Retained evidence" }), kv([["Artifact", ref.artifact_id], ["Digest", ref.sha256]]), button("Open retained evidence detail", () => void openEvidenceDetail(ref, ctx), "primary"));
  const refs = Array.isArray(current.evidence_refs) ? current.evidence_refs : [];
  const operationEvidence = current.representations?.find((row) => row.source === "operations" && row.evidence_refs?.[0]?.artifact_id)?.evidence_refs?.[0]?.artifact_id;
  if (operationEvidence) return button("Open retained evidence", () => evidenceDialog(operationEvidence, ctx), "primary");
  return refs.length ? el("section", { class: "panel stack" }, el("h2", { text: "Owner artifact references" }), kv(refs.flatMap((ref, index) => [[`Artifact ${index + 1}`, ref.artifact_id || "Not reported"], [`Digest ${index + 1}`, ref.sha256 || "Not reported"]]))) : empty("This owner reported no retained evidence reference.");
}
function comparisonPanel(runs, ctx, comparison, rerender) {
  const choices = runs.filter((row) => row.source === "evidence").slice(0, PAGE_LIMIT + HISTORY_LIMIT).flatMap((row) => {
    const ref = evidenceReference(row); return ref ? [[row, ref]] : [];
  });
  if (!choices.length) return el("section", { class: "panel stack" }, el("h2", { text: "Compare retained outcomes" }), empty("Load retained evidence rows to compare compatible measured outcomes."));
  const choose = (ref) => {
    const key = `${ref.artifact_id}:${ref.sha256}`;
    if (comparison.refs.has(key)) comparison.refs.delete(key);
    else if (comparison.refs.size < 20) comparison.refs.set(key, ref);
    comparison.generation += 1; comparison.result = null; comparison.error = null; rerender();
  };
  const compare = async () => {
    const generation = ++comparison.generation, refs = [...comparison.refs.values()];
    comparison.loading = true; comparison.result = null; comparison.error = null; rerender();
    try {
      const result = await request(query("runs/evidence/compare", { refs: JSON.stringify(refs) }), { timeout: SOURCE_TIMEOUT, signal: ctx.signal });
      if (comparison.generation === generation) comparison.result = result;
    } catch (error) { if (comparison.generation === generation && error.name !== "AbortError") comparison.error = error.message; }
    finally { if (comparison.generation === generation) { comparison.loading = false; rerender(); } }
  };
  const result = comparison.result;
  return el("section", { class: "panel stack" }, el("h2", { text: "Compare retained outcomes" }), notice("Comparison checks declared compatible dimensions. It does not rank evidence or promote a model."),
    el("div", { class: "comparison-list", "aria-label": "Retained evidence selections" }, choices.map(([row, ref]) => {
      const key = `${ref.artifact_id}:${ref.sha256}`, checked = comparison.refs.has(key);
      return el("label", { class: "comparison-choice" }, el("input", { type: "checkbox", checked, disabled: comparison.loading || (!checked && comparison.refs.size >= 20), "data-focus-key": `compare:${key}`, onChange: () => choose(ref) }), el("span", {}, el("strong", { text: row.model || row.title || "Retained evidence" }), el("small", { class: "mono", text: ref.artifact_id })));
    })),
    el("div", { class: "actions" }, button(comparison.loading ? "Comparing…" : `Compare ${comparison.refs.size} selected`, () => void compare(), "primary", comparison.loading || comparison.refs.size < 2)),
    comparison.error ? notice(comparison.error, "danger") : null,
    result ? el("section", { class: "comparison-result stack" }, kv([["Comparable", badge(result.comparable ? "compatible" : "incompatible")], ["Different dimensions", result.differences?.join(", ") || "None reported"], ["Unknown dimensions", result.unknown_fields?.join(", ") || "None reported"], ["Invalid artifacts", result.invalid_artifacts?.join(", ") || "None reported"]]), result.artifacts?.length ? runTable(result.artifacts, ctx) : null) : null,
  );
}
function backfillControls(states, feed) {
  const pages = [...states.values()].filter((state) => state.next_cursor || state.historyExpired || state.historyExhausted || state.history.some(activeRun));
  if (!pages.length) return null;
  return el("section", { class: "panel run-backfill stack" }, el("h2", { text: "Retained history" }), ...pages.map((state) => el("div", { class: "run-backfill-row" }, el("span", { text: `${sourceLabel(state.source)} · ${state.history.length} retained snapshot rows` }), state.historyExpired || state.historyExhausted ? button("Restart history", () => void feed.restartHistory(state)) : state.next_cursor ? button(state.moreLoading ? "Loading…" : "Load more", () => void feed.loadMore(state), "", state.moreLoading) : badge("retained history"))), pages.some((state) => state.historyExpired) ? notice("A source snapshot expired. Its last successful rows remain visible; restart history to continue from the current snapshot.", "warning") : null, pages.some((state) => state.historyExhausted) ? notice("History reached the 500-row display limit. Restart to browse from a current head; no loaded row was silently dropped.", "warning") : null, pages.some((state) => state.history.some(activeRun)) ? notice("Active rows outside the current owner head are retained history. Their current state is not actively polled until they return to the head.", "warning") : null);
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
  const comparison = { refs: new Map(), result: null, error: null, loading: false, generation: 0 };
  let latestStates = new Map(), latestRuns = [], sourceFilter, stateFilter, toolbar, feed;
  const render = (states, runs) => {
    latestStates = states; latestRuns = runs;
    const current = id ? runs.find((run) => run.id === id || run.representations?.some((row) => row.id === id || (row.source === "operations" && row.native_id === id))) : runs.find(activeRun) || runs[0];
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
      data.append(el("section", { class: "panel stack" }, el("h2", { text: "All retained runs" }), visible.length ? runTable(visible, ctx) : empty("No retained runs match these filters.")), backfillControls(states, feed));
    } else if (tab === "compare") data.append(comparisonPanel(runs, ctx, comparison, () => render(latestStates, latestRuns)), backfillControls(states, feed));
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
  feed = createRunFeed(ctx, render);
  data.append(notice("Discovering authorized run sources…"));
  void feed.start();
  return root;
}
