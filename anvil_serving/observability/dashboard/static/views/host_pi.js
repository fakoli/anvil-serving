import { badge, button, el, empty, field, notice, select } from "./common.js";
import { workbenchRequest } from "./api.js";
import { projectFilesView } from "./project_files.js";

const nativeId = value => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$/.test(value);
export function hostThreadLink(projectId, sessionId) {
  const url = new URL(location.href);
  url.search = ""; url.searchParams.set("project", projectId); url.searchParams.set("pi-thread", sessionId);
  url.hash = "/playground";
  return url.pathname + url.search + url.hash;
}

export async function hostPiView(ctx, catalog) {
  const host = catalog.host_pi;
  const frame = el("iframe", { class: "host-pi-frame", title: "Pi conversations", src: host.origin + "/", referrerpolicy: "no-referrer", allow: "clipboard-write" });
  const authority = el("span", { class: "meta", text: host.authority });
  const root = el("div", { class: "host-pi-workspace" }, authority, frame);
  if (!host.bridge) return root;
  if (location.origin !== host.parent_origin) {
    root.prepend(notice("Project navigation is unavailable on this origin. Use the configured Workbench origin.", "warning")); return root;
  }
  const projects = catalog.projects || [], url = new URL(location.href);
  const projectId = url.searchParams.get("project") || projects[0]?.id;
  let project = projects.find(item => item.id === projectId);
  if (!project) { root.prepend(notice("Select an authorized project to organize native Pi threads.")); return root; }
  try {
    const preferences = await workbenchRequest(`projects/${encodeURIComponent(projectId)}/preferences`, { signal: ctx.signal });
    project = { ...project, roots: preferences.roots, primary_root_id: preferences.defaults.primary_root_id };
  } catch (error) { root.prepend(notice(error.message, "warning")); return root; }
  const base = `host-pi/projects/${encodeURIComponent(projectId)}/threads`;
  const key = `anvil-host-pi-start/${ctx.session.identity}/${projectId}`;
  let items = [], selected = url.searchParams.get("pi-thread"), search = "", archived = false, bridgeId = null, sent = 0, received = -1, busy = false;
  const retiredBridges = new Set();
  let pending = null, pendingError = "";
  try {
    const saved = sessionStorage.getItem(key);
    if (saved) {
      pending = JSON.parse(saved);
      if (pending.project_id !== projectId || !nativeId(pending.request_id) || !["create", "associate"].includes(pending.operation)
          || !nativeId(pending.root_id) || (pending.operation === "associate" && !nativeId(pending.native_id))) throw Error("Invalid retained thread request.");
    }
  } catch { pendingError = "The retained thread request is unavailable. Resolve browser storage before starting another thread."; }
  const status = el("div", { role: "status", class: "meta" });
  const rail = el("aside", { class: "conversation-rail panel stack", "aria-label": "Pi project threads" });
  const navigation = el("details", { class: "host-pi-navigation" },
    el("summary", { text: "Project threads" }), rail);
  const rows = el("div", { class: "small-stack" });
  const selectedInfo = el("div", { class: "stack" });
  const title = el("input", { type: "text", maxlength: 192 });
  const rootChoice = select((project.roots || []).map(item => [item.id, item.label]), pending?.root_id || project.primary_root_id, () => {});
  const create = button("New host thread", () => start(), "primary");
  const association = button("Associate with this project", () => start(selected), "secondary-button");
  function controls() {
    create.textContent = pending ? "Reconcile original thread" : "New host thread";
    create.disabled = busy || Boolean(pendingError);
    rootChoice.disabled = busy || Boolean(pending) || Boolean(pendingError);
    association.disabled = busy || Boolean(pending) || Boolean(pendingError);
  }
  function open(item) {
    selected = item.native_id;
    navigation.open = false;
    history.replaceState(null, "", hostThreadLink(projectId, selected));
    render();
    if (bridgeId) frame.contentWindow.postMessage({ v: 1, type: "open_session", bridge_id: bridgeId, native_id: selected, request_id: crypto.randomUUID(), sequence: ++sent }, host.origin);
  }
  function render() {
    const visible = items.filter(item => (archived || !item.archived) && `${item.title} ${item.source} ${item.provenance}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
    rows.replaceChildren(...visible.map(item => button(`${item.title} · ${item.running ? "running" : "retained"} · ${item.provenance}${item.archived ? " · archived" : ""}`, () => open(item), item.native_id === selected ? "thread-selected" : "quiet-button")), ...(!visible.length ? [empty("No matching threads.")] : []));
    const item = items.find(row => row.native_id === selected);
    selectedInfo.replaceChildren();
    if (!item) return;
    selectedInfo.append(badge(item.source), el("a", { href: hostThreadLink(projectId, item.native_id), text: "Link to this thread" }));
    if (item.provenance === "unassigned") {
      selectedInfo.append(notice("Owner-only native thread. Association preserves its existing directory and operator access."), association);
    } else {
      title.value = item.title;
      selectedInfo.append(field("Thread label", title), button("Save thread label", () => edit(item, "rename", { title: title.value }), "quiet-button"),
        button(item.archived ? "Restore thread" : "Archive thread", () => edit(item, "archive", { archived: !item.archived }), "quiet-button"),
        el("span", { class: "meta", text: "Labels and archive state apply only to your Workbench view. Native history is retained." }));
    }
  }
  async function refresh() {
    const result = await workbenchRequest(base, { signal: ctx.signal });
    if (ctx.signal.aborted) return;
    items = result.items; render();
  }
  async function edit(item, action, body) {
    if (busy) return;
    busy = true; controls();
    try { await workbenchRequest(`${base}/${encodeURIComponent(item.request_id)}/${action}`, { method: "POST", body, signal: ctx.signal }); await refresh(); }
    catch (error) { if (!ctx.signal.aborted) status.textContent = error.message; }
    finally { busy = false; controls(); }
  }
  async function start(native = null) {
    if (busy || pendingError) return;
    busy = true; controls();
    try {
      if (!pending) {
        const request = { operation: native ? "associate" : "create", project_id: projectId, request_id: crypto.randomUUID(), root_id: rootChoice.value, ...(native ? { native_id: native } : {}) };
        sessionStorage.setItem(key, JSON.stringify(request)); pending = request;
      }
      const { operation, project_id, ...body } = pending;
      const item = await workbenchRequest(base + (operation === "associate" ? "/associate" : ""), { method: "POST", body, signal: ctx.signal, timeout: 30000 });
      sessionStorage.removeItem(key); pending = null;
      await refresh(); open(item); status.textContent = "Native thread ready.";
    } catch (error) { if (!ctx.signal.aborted) status.textContent = error.message; }
    finally { busy = false; controls(); }
  }
  function receive(event) {
    if (event.origin !== host.origin || event.source !== frame.contentWindow || !event.data || typeof event.data !== "object") return;
    const message = event.data;
    if (message.v !== 1 || !Number.isSafeInteger(message.sequence) || !nativeId(message.bridge_id)) return;
    if (message.type === "ready" && message.sequence === 0 && Object.keys(message).length === 4) {
      if (message.bridge_id === bridgeId || retiredBridges.has(message.bridge_id)) return;
      if (bridgeId) retiredBridges.add(bridgeId);
      bridgeId = message.bridge_id; received = 0;
      const item = items.find(row => row.native_id === selected);
      if (item) open(item);
    } else if (message.bridge_id === bridgeId && message.type === "session_changed" && Object.keys(message).length === 5 && nativeId(message.native_id) && message.sequence > received) {
      received = message.sequence;
      selected = message.native_id;
      history.replaceState(null, "", hostThreadLink(projectId, selected)); render();
    }
  }
  window.addEventListener("message", receive);
  ctx.signal.addEventListener("abort", () => window.removeEventListener("message", receive), { once: true });
  const choice = select(projects.map(item => [item.id, item.label]), projectId, event => {
    const next = new URL(location.href); next.search = ""; next.searchParams.set("project", event.target.value);
    history.replaceState(null, "", next); ctx.refresh();
  });
  rail.append(field("Pi project", choice), field("New thread directory", rootChoice), create,
    field("Search threads", el("input", { type: "search", onInput: event => { search = event.target.value; render(); } })),
    field("Show archived threads", el("input", { type: "checkbox", onChange: event => { archived = event.target.checked; render(); } })),
    rows, selectedInfo, status, button("Refresh threads", () => refresh().catch(error => { status.textContent = error.message; }), "quiet-button"));
  const files = projectFilesView(ctx, project);
  const filePanel = el("details", { class: "host-pi-files" }, el("summary", { text: "Project files" }), files);
  filePanel.addEventListener("toggle", () => { files.open = filePanel.open; });
  root.replaceChildren(el("div", { class: "host-pi-tools" }, navigation, authority, filePanel), frame);
  try { await refresh(); status.textContent = pendingError || (pending ? "Reconcile the original request before starting another thread." : ""); }
  catch (error) { if (!ctx.signal.aborted) status.textContent = error.message; }
  controls(); return root;
}
