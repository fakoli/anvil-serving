import { button, copy, el, empty, field, notice, select } from "./common.js";
import { query, workbenchRequest } from "./api.js";

// Context reads never select the native writer's cwd or expand its authority.
export function projectFilesView(ctx, project) {
  const roots = project.roots || [];
  const panel = el("details", { class: "panel project-files" }, el("summary", { text: "Project files and worktrees" }));
  if (!roots.length) { panel.append(empty("No project roots are declared.")); return panel; }
  let rootId = project.primary_root_id || roots[0].id, directory = "", path = "", generation = 0;
  const status = el("div", { class: "meta", role: "status" });
  const tree = el("nav", { class: "small-stack", "aria-label": "Project files" });
  const preview = el("div", { class: "stack" });
  const choice = select(roots.map(root => [root.id, root.label]), rootId, event => {
    rootId = event.target.value; directory = ""; path = ""; preview.replaceChildren(); load();
  });
  const base = () => `projects/${encodeURIComponent(project.id)}/roots/${encodeURIComponent(rootId)}`;
  async function showFile(selected, kind = "text") {
    const current = ++generation; path = selected;
    preview.replaceChildren(notice("Loading file…"));
    try {
      const data = await workbenchRequest(query(`${base()}/${kind === "text" ? "text" : "diff"}`, { path, kind: kind === "text" ? null : kind }), { signal: ctx.signal });
      if (current !== generation || ctx.signal.aborted) return;
      preview.replaceChildren(copy("file reference", `@${rootId}:${path}`),
        el("div", { class: "actions" }, ...["text", "working", "staged"].map(mode => button(mode === "text" ? "File preview" : `${mode} diff`, () => showFile(selected, mode), "quiet-button"))),
        el("pre", { class: "evidence-json", text: data.content ?? data.diff ?? "" }));
    } catch (error) { if (current === generation && !ctx.signal.aborted) preview.replaceChildren(notice(error.message, "warning")); }
  }
  async function load() {
    const current = ++generation;
    status.textContent = "Loading project context…";
    try {
      const [listing, worktree] = await Promise.all([
        workbenchRequest(query(`${base()}/tree`, { path: directory }), { signal: ctx.signal }),
        workbenchRequest(`${base()}/worktree`, { signal: ctx.signal }).catch(error => ({ error: error.message })),
      ]);
      if (current !== generation || ctx.signal.aborted) return;
      status.textContent = worktree.error || `${worktree.branch} · ${worktree.dirty ? "tracked changes" : "tracked files clean"} · ${worktree.head.slice(0, 12)}`;
      const parent = directory ? [button("Parent directory", () => { directory = directory.split("/").slice(0, -1).join("/"); load(); }, "quiet-button")] : [];
      tree.replaceChildren(...parent, ...listing.items.map(item => button(`${item.kind === "directory" ? "▸ " : ""}${item.name}`, () => {
        const selected = [directory, item.name].filter(Boolean).join("/");
        if (item.kind === "directory") { directory = selected; load(); } else showFile(selected);
      }, "quiet-button")), ...(listing.truncated ? [notice("This directory exceeds the listing limit.")] : []));
    } catch (error) { if (current === generation && !ctx.signal.aborted) { status.textContent = error.message; tree.replaceChildren(); } }
  }
  panel.append(notice("Declared project context. Browsing a root does not move the active Pi session. Copy references into Pi only when needed."),
    field("Browse root", choice), status, tree, preview);
  panel.addEventListener("toggle", () => { if (panel.open) load(); });
  return panel;
}

// Managed task roots are a separate authority from declared host context.  They
// can be read only through a retained task binding and never run host Git.
export function frozenProjectFilesView(ctx, bindingId) {
  const panel = el("details", { class: "panel project-files" }, el("summary", { text: "Frozen task files" }));
  const status = el("div", { class: "meta", role: "status" });
  const tree = el("nav", { class: "small-stack", "aria-label": "Frozen task files" });
  const preview = el("div", { class: "stack" });
  let roots = [], rootId = "", directory = "", generation = 0;
  const base = () => `artifacts/${encodeURIComponent(bindingId)}/roots/${encodeURIComponent(rootId)}`;

  async function showFile(selected, mode = "text") {
    const current = ++generation;
    preview.replaceChildren(notice("Loading frozen task evidence…"));
    try {
      const endpoint = mode === "reviewed" ? "diff" : "text";
      const data = await workbenchRequest(query(`${base()}/${endpoint}`, mode === "reviewed" ? {} : { path: selected }), { signal: ctx.signal });
      if (current !== generation || ctx.signal.aborted) return;
      preview.replaceChildren(
        el("div", { class: "actions" },
          button("File preview", () => showFile(selected), "quiet-button"),
          button("Reviewed patch", () => showFile(selected, "reviewed"), "quiet-button")),
        el("pre", { class: "evidence-json", text: data.content ?? data.diff ?? "" }),
      );
    } catch (error) { if (current === generation && !ctx.signal.aborted) preview.replaceChildren(notice(error.message, "warning")); }
  }

  async function loadTree() {
    const current = ++generation;
    try {
      const [listing, identity] = await Promise.all([
        workbenchRequest(query(`${base()}/tree`, { path: directory }), { signal: ctx.signal }),
        workbenchRequest(`${base()}/worktree`, { signal: ctx.signal }),
      ]);
      if (current !== generation || ctx.signal.aborted) return;
      status.textContent = `Frozen baseline ${identity.baseline_sha.slice(0, 12)} · ${identity.transfer_state.replaceAll("_", " ")}`;
      const parent = directory ? [button("Parent directory", () => { directory = directory.split("/").slice(0, -1).join("/"); loadTree(); }, "quiet-button")] : [];
      tree.replaceChildren(...parent, ...listing.items.map(item => button(`${item.kind === "directory" ? "▸ " : ""}${item.name}`, () => {
        const selected = [directory, item.name].filter(Boolean).join("/");
        if (item.kind === "directory") { directory = selected; loadTree(); } else showFile(selected);
      }, "quiet-button")), ...(listing.truncated ? [notice("This directory exceeds the listing limit.")] : []));
    } catch (error) { if (current === generation && !ctx.signal.aborted) { status.textContent = error.message; tree.replaceChildren(); } }
  }

  async function loadRoots() {
    try {
      const data = await workbenchRequest(`artifacts/${encodeURIComponent(bindingId)}/roots`, { signal: ctx.signal });
      if (ctx.signal.aborted) return;
      roots = Array.isArray(data.roots) ? data.roots : [];
      if (!roots.length) { panel.append(empty("This task has no prepared frozen roots.")); return; }
      rootId = roots[0].root_id;
      const choice = select(roots.map(root => [root.root_id, `${root.root_id} · ${root.reviewed ? "reviewed" : "not reviewed"}`]), rootId, event => {
        rootId = event.target.value; directory = ""; preview.replaceChildren(); loadTree();
      });
      panel.append(notice("These are isolated task workspaces. File previews use descriptor-safe reads; patches come only from retained reviewed evidence."),
        field("Frozen root", choice), status, tree, preview);
      loadTree();
    } catch (error) { if (!ctx.signal.aborted) panel.append(notice(error.message, "warning")); }
  }
  panel.addEventListener("toggle", () => { if (panel.open && !roots.length) loadRoots(); });
  return panel;
}
