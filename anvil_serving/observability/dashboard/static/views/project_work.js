import { badge, button, el, empty, field, jsonDetails, notice, route, section, select, table, words } from "./common.js";
import { workbenchRequest } from "./api.js";
import { piChatView } from "./pi_chat.js";
import { renderedMarkdown } from "./documentation.js";
import { frozenProjectFilesView } from "./project_files.js";

const entries = (value) => Array.isArray(value) ? value : [];
const planLink = (projectId, planId, sectionId, taskId = "", tab = "overview") => {
  const url = new URL(location.href);
  const current = url.hash.replace(/^#\//, "").split("/").map((part) => {
    try { return decodeURIComponent(part); } catch { return ""; }
  });
  if (current[0] !== "work" || current[1] !== projectId || current[2] !== taskId)
    url.searchParams.delete("binding");
  url.hash = taskId ? route("work", projectId, taskId, tab) : route("work", projectId);
  if (planId) url.searchParams.set("plan", planId);
  else url.searchParams.delete("plan");
  if (sectionId) url.searchParams.set("plan-section", sectionId);
  else url.searchParams.delete("plan-section");
  return url;
};
let taskRunDialog;
let pendingTaskRunFocus = "";

function openTaskRunDialog(ctx, projectId, taskId, detail, returnUrl) {
  if (taskRunDialog?.dialog.open) {
    taskRunDialog.returnToOverview = false;
    taskRunDialog.dialog.close();
  }
  const dialog = el("dialog", { class: "work-run-dialog", "aria-labelledby": "work-run-dialog-title" });
  const controller = new AbortController();
  const state = { dialog, controller, returnToOverview: true, focusKey: `${projectId}/${taskId}` };
  taskRunDialog = state;
  const close = () => {
    controller.abort();
    if (taskRunDialog === state) taskRunDialog = null;
    if (state.returnToOverview) {
      pendingTaskRunFocus = state.focusKey;
      if (location.href !== returnUrl) location.href = returnUrl;
    }
    dialog.remove();
  };
  dialog.addEventListener("close", close, { once: true });
  dialog.addEventListener("cancel", () => controller.abort(), { once: true });
  ctx.signal.addEventListener("abort", () => {
    state.returnToOverview = false;
    controller.abort();
    if (dialog.open) dialog.close();
  }, { once: true });
  const title = el("h2", { id: "work-run-dialog-title", tabindex: "-1", text: "Run with Pi" });
  const body = el("div", { class: "work-run-dialog-body stack" }, notice("Loading the task-bound Pi session…"));
  dialog.append(el("div", { class: "dialog-header" }, el("div", {}, title, el("p", { class: "meta", text: "Task-bound Pi session · isolated workspace" })), button("Close Run with Pi", () => dialog.close(), "icon-button")), body);
  document.body.append(dialog);
  dialog.showModal();
  title.focus();
  void piChatView({ ...ctx, signal: controller.signal }, { projectId, taskId, detail }).then((chat) => {
    if (!controller.signal.aborted && dialog.open) body.replaceChildren(chat);
  }).catch((error) => {
    if (!controller.signal.aborted && dialog.open) body.replaceChildren(notice(error.message, "danger"));
  });
}
const planError = (error) => ({
  project_projection_not_converged: "The State reader reported an inconsistent projection. An operator must inspect State health before this plan can be read.",
  project_state_unavailable: "The project State source is unavailable. Retry after it is restored.",
  project_state_incompatible: "This State reader is incompatible with the project schema.",
  project_source_too_large: "The persisted plan exceeds the display limit.",
  project_permission_denied: "You do not have permission to read this project plan.",
  project_read_unsupported: "This configured Anvil source does not support plan reads.",
}[error.code] || error.message || "The project plan could not be read.");

export function matchesWorkSearch(item, query) {
  const needle = query.trim().toLocaleLowerCase();
  return !needle || [item.id, item.title, item.status].some(value => String(value || "").toLocaleLowerCase().includes(needle));
}

export async function projectWorkView(ctx, projectId, taskId, tab = "overview") {
  const root = el("div", { class: "workbench-page stack", "data-story": "US-WORK-01" });
  const catalog = await workbenchRequest("catalog", { signal: ctx.signal });
  const projects = entries(catalog.projects);
  if (!projects.length) { root.append(empty("No Anvil projects are connected to this account.")); return root; }
  const selected = projects.find(project => project.id === projectId) || (!projectId && projects[0]);
  if (!selected) { root.append(notice("This project is unavailable.", "warning")); return root; }
  const data = await workbenchRequest(`projects/${encodeURIComponent(selected.id)}`, { signal: ctx.signal, timeout: 30000 });
  const plans = entries(data.prds), tasks = entries(data.tasks);
  const params = new URL(location.href).searchParams;
  const explicitTask = Boolean(taskId);
  let selectedPlan = params.get("plan") || "";
  const taskPlan = (task) => {
    const identifier = String(task?.id || "");
    const separator = identifier.lastIndexOf(":");
    return separator > 0 ? identifier.slice(0, separator) : "default";
  };
  if (!selectedPlan && taskId) selectedPlan = taskPlan(tasks.find(task => task.id === taskId));
  if (!selectedPlan) selectedPlan = plans[0]?.id || "";
  if (!taskId) taskId = tasks.find(task => taskPlan(task) === selectedPlan)?.id || "";
  if (taskId && !params.get("plan")) history.replaceState(null, "", planLink(selected.id, selectedPlan, undefined, taskId, tab));
  const content = el("section", { class: "work-reader stack", role: "region", "aria-label": "Selected project plan or task" });
  const planList = el("nav", { class: "stack work-selections", "aria-label": "Project plans" });
  const taskList = el("nav", { class: "stack work-selections", "aria-label": "Project tasks" });
  const outlineList = el("nav", { class: "stack work-plan-outline", "aria-label": "Selected plan outline" }, empty("Open a plan to show its outline."));
  const inspector = el("aside", { class: "panel stack work-task-inspector", "aria-label": "Selected task inspector" });
  const search = el("input", { type: "search", value: params.get("work-search") || "", placeholder: "Title, ID or state", onInput: () => {
    const url = new URL(location.href);
    if (search.value) url.searchParams.set("work-search", search.value); else url.searchParams.delete("work-search");
    history.replaceState(null, "", url); draw();
  } });
  const prdFilter = select([["", "All plans"], ...plans.map(prd => [prd.id, prd.title || prd.id])], selectedPlan, () => {
    selectedPlan = prdFilter.value;
    history.replaceState(null, "", planLink(selected.id, selectedPlan, undefined, taskId, tab));
    draw();
    const prd = plans.find(item => item.id === selectedPlan);
    if (prd) void readPlan(prd);
    else outlineList.replaceChildren(empty("Choose a plan to show its outline."));
  });
  const rail = el("aside", { class: "panel stack work-rail", "aria-label": "Plan selection" },
    field("Project", select(projects.map(project => [project.id, project.label]), selected.id, event => { location.href = planLink(event.target.value).href; })),
    field("Search plans and tasks", search), el("h2", { text: "Plans" }), planList,
    el("h2", { text: "Outline" }), outlineList);
  const taskPanel = el("section", { class: "work-plan-tasks stack", "aria-label": "Plan tasks" },
    el("h2", { text: "Tasks" }), field("Filter tasks by plan", prdFilter), taskList);
  const desktop = matchMedia("(min-width: 1000px)").matches;
  const navigation = el("details", { class: "work-navigation", open: desktop }, el("summary", { text: "Plans & tasks" }), rail);
  const inspectorNavigation = el("details", { class: "work-inspector-navigation", open: desktop || explicitTask }, el("summary", { text: "Selected task" }), inspector);
  const widthChanged = (event) => {
    navigation.open = event.matches;
    inspectorNavigation.open = event.matches || explicitTask;
  };
  const desktopWidth = matchMedia("(min-width: 1000px)");
  desktopWidth.addEventListener("change", widthChanged);
  ctx.signal.addEventListener("abort", () => desktopWidth.removeEventListener("change", widthChanged), { once: true });
  function draw() {
    const visiblePlans = plans.filter(item => matchesWorkSearch(item, search.value));
    planList.replaceChildren(...visiblePlans.map(prd => el("a", { href: planLink(selected.id, prd.id, undefined, taskId, tab).href, class: "work-choice", "aria-current": selectedPlan === prd.id ? "page" : null,
      onClick: event => { if (!event.ctrlKey && !event.metaKey && !event.shiftKey) { event.preventDefault(); void readPlan(prd); } } },
      el("strong", { text: prd.title || prd.id }), badge(prd.status))), ...(!visiblePlans.length ? [empty("No matching plans.")] : []));
    const visible = tasks.filter(task => (
      !prdFilter.value
      || task.id.startsWith(`${prdFilter.value}:`)
      || (prdFilter.value === "default" && !task.id.includes(":"))
    ) && matchesWorkSearch(task, search.value));
    const taskChoices = visible.map(task => {
      const url = planLink(selected.id, selectedPlan || taskPlan(task));
      url.hash = route("work", selected.id, task.id, "overview");
      return el("a", { href: url.href, class: "work-choice", "aria-current": taskId === task.id ? "page" : null },
        el("strong", { text: task.title || task.id }), el("span", { class: "meta", text: task.id }), badge(task.status));
    });
    taskList.replaceChildren(...taskChoices, ...(!taskChoices.length ? [empty("No matching open tasks.")] : []));
  }
  async function readPlan(prd, sectionId) {
    selectedPlan = prd.id;
    history.replaceState(null, "", planLink(selected.id, prd.id, sectionId, taskId, tab));
    prdFilter.value = prd.id; draw();
    content.replaceChildren(notice("Loading the persisted project plan…"));
    try {
      const document = await workbenchRequest(`projects/${encodeURIComponent(selected.id)}/prds/${encodeURIComponent(prd.id)}`, { signal: ctx.signal, timeout: 30000 });
      if (ctx.signal.aborted || selectedPlan !== prd.id) return;
      const rendered = renderedMarkdown(document.content);
      const headings = [...rendered.querySelectorAll("[data-anchor]")];
      outlineList.replaceChildren(...(headings.length ? headings.map(item => el("a", { class: "work-outline-choice", href: planLink(selected.id, prd.id, item.dataset.anchor, taskId, tab).href, text: item.textContent })) : [empty("This plan has no outline.")]));
      content.replaceChildren(...[
        el("div", { class: "work-plan-kicker" }, el("span", { class: "eyebrow", text: `Plan · ${prd.id}` }), badge(prd.status)),
        el("div", { class: "actions" }, el("span", { class: "meta", text: `Persisted revision ${document.prd_revision} · ${document.source_digest.slice(0, 12)}` }), el("a", { href: planLink(selected.id, prd.id, undefined, taskId, tab).href, text: "Plan link" })),
        rendered,
        taskPanel,
      ].filter(Boolean));
      if (sectionId) headings.find(item => item.dataset.anchor === sectionId)?.scrollIntoView({ block: "start" });
    } catch (error) { if (!ctx.signal.aborted && selectedPlan === prd.id) {
      outlineList.replaceChildren(empty("The plan outline is unavailable until the persisted plan can be read."));
      content.replaceChildren(...[notice(planError(error), "danger"), button("Retry plan read", () => readPlan(prd, sectionId), "secondary-button"), taskPanel].filter(Boolean));
    } }
  }
  root.append(el("div", { class: "work-layout work-layout--three-pane" }, navigation, content, inspectorNavigation));
  draw();
  const linkedPlan = plans.find(prd => prd.id === selectedPlan);
  if (linkedPlan) await readPlan(linkedPlan, params.get("plan-section"));
  else content.append(empty("Choose a plan to read."), taskPanel);
  if (taskId) inspector.append(await taskWorkView(ctx, selected.id, taskId, tab));
  else inspector.append(empty("Select a task to inspect readiness, start Pi, or review evidence."));
  if (data.truncated) rail.append(notice("Showing the first 200 open tasks. Narrow the project in Anvil to inspect additional work.", "warning"));
  return root;
}

async function taskWorkView(ctx, projectId, taskId, tab) {
  const root = el("div", { class: "stack work-task-detail" });
  let detail;
  try { detail = await workbenchRequest(`projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`, { signal: ctx.signal, timeout: 30000 }); }
  catch (error) { return el("div", { class: "work-task-error stack" }, notice(error.message, "danger")); }
  const task = detail.task || { id: taskId };
  const navigate = (name) => { location.hash = route("work", projectId, taskId, name); };
  const focusKey = `${projectId}/${taskId}`;
  if (pendingTaskRunFocus && pendingTaskRunFocus !== focusKey) pendingTaskRunFocus = "";
  const run = button("Run with Pi", () => navigate("agent"), "primary work-task-run");
  run.dataset.taskRun = focusKey;
  if (tab === "overview" && pendingTaskRunFocus === focusKey) {
    ctx.jobs.push(async () => {
      if (pendingTaskRunFocus === focusKey && run.isConnected) run.focus();
      if (pendingTaskRunFocus === focusKey) pendingTaskRunFocus = "";
    });
  }
  root.append(
    el("div", { class: "work-task-inspector-head stack" },
      el("span", { class: "eyebrow", text: `Selected task · ${task.id}` }),
      el("h2", { text: task.title || task.id }),
      el("span", { class: "meta", text: detail.prd?.id || "Plan not reported" }),
      badge(task.status || "unknown"),
    ),
    el("nav", { class: "local-tabs work-task-tabs", "aria-label": "Task sections" },
      button("Overview", () => navigate("overview"), tab === "overview" ? "primary" : "quiet-button"),
      button("Evidence", () => navigate("evidence"), tab === "evidence" ? "primary" : "quiet-button"),
    ),
    run,
  );
  if (tab === "agent") {
    const returnUrl = new URL(location.href);
    returnUrl.hash = route("work", projectId, taskId, "overview");
    root.append(notice("Run with Pi opens the task-bound conversation in a full dialog. The task inspector remains available when it closes.", "info"));
    openTaskRunDialog(ctx, projectId, taskId, detail, returnUrl.href);
    return root;
  }
  if (tab === "evidence") {
    const packet = detail.packet;
    const panel = el("section", { class: "panel stack" }, section("Evidence"));
    let bindingId;
    let evidence;
    try {
      const sessions = await workbenchRequest(`pi/sessions?project=${encodeURIComponent(projectId)}&task=${encodeURIComponent(taskId)}`, { signal: ctx.signal });
      const requestedBinding = new URL(location.href).searchParams.get("binding");
      const bindings = entries(sessions.items).map(item => item?.binding_id).filter(value => typeof value === "string");
      if (requestedBinding && !bindings.includes(requestedBinding)) {
        throw Error("The selected task run is no longer available for this project and task.");
      }
      bindingId = requestedBinding || bindings[0];
      if (bindingId) evidence = await workbenchRequest(`artifacts/${encodeURIComponent(bindingId)}`, { signal: ctx.signal });
    } catch (error) { panel.append(notice(error.message, "warning")); }
    const rootSet = Boolean(evidence?.rootset_digest);
    const artifact = evidence?.artifact;
    const reviewRoots = entries(evidence?.review?.roots);
    const digest = rootSet
      ? evidence?.review?.manifest_digest || evidence?.verification?.manifest_digest
      : artifact?.artifact_digest || evidence?.verification?.artifact_digest || evidence?.submission?.artifact_digest;
    const verified = rootSet ? evidence?.verification?.passed === true : evidence?.verification?.passed === true;
    const releasePending = evidence?.status === "submitted_release_pending";
    const submitted = evidence?.status === "submitted" || (evidence?.submission?.status === "submitted" && !releasePending);
    const busy = evidence?.job?.status === "running";
    const outcome = el("div", { class: "meta", role: "status", text: busy ? `${words(evidence.job.action)} is running in the task sandbox…` : evidence?.job?.error || "" });
    const act = async (name, body = {}) => {
      if (!bindingId) return;
      outcome.textContent = "Working…";
      try {
        await workbenchRequest(`artifacts/${encodeURIComponent(bindingId)}/${name}`, { method: "POST", body, signal: ctx.signal, timeout: 30000 });
        ctx.refresh();
      } catch (error) { outcome.textContent = error.message; }
    };
    if (!bindingId) panel.append(empty("Start an isolated agent session first. Evidence remains tied to its Anvil lease."));
    else panel.append(...[
      evidence ? table(["Baseline", "Packet", "Acceptance"], [[rootSet ? "Per frozen root" : evidence.baseline_sha || "Not reported", evidence.packet_digest || "Not reported", words(evidence.acceptance || "unreviewed")]]) : empty("No captured evidence is available."),
      artifact ? el("pre", { class: "code-block", text: artifact.patch || "Patch retained privately; preview unavailable." }) : null,
      artifact ? table(["Path", "Status", "Mode"], entries(artifact.files).map((item) => [item.path, item.status, item.mode])) : null,
      rootSet ? table(["Frozen root", "Baseline", "Reviewed files", "Transfer"], reviewRoots.map((item) => [item.root_id, item.baseline_sha?.slice(0, 12) || "Not reported", entries(item.files).length ? entries(item.files).map(file => file.path).join(", ") : "No changes", evidence?.transfer?.[item.root_id]?.state || "not reviewed"])) : null,
      el("div", { class: "actions" },
        button(rootSet ? "Capture frozen root patches" : "Capture patch", () => act("review"), "secondary-button", busy || submitted || releasePending),
        button(rootSet ? "Verify & transfer frozen root patches" : "Verify & transfer reviewed patch", () => act("verify", { artifact_digest: digest }), "secondary-button", busy || !digest || verified || submitted || releasePending),
        button(releasePending ? "Retry owner release reconciliation" : "Submit evidence to Anvil", () => act("submit", { artifact_digest: digest }), "primary", busy || !digest || !verified || submitted),
        button("Release task lease", () => act("release"), "quiet-button", busy || releasePending || evidence?.status === "released"),
      ), outcome,
      evidence?.verification ? jsonDetails(evidence.verification, evidence.verification.passed ? "Passed verification output" : "Failed verification output") : null,
      rootSet && bindingId ? frozenProjectFilesView(ctx, bindingId) : null,
      notice("Capture and transfer require the Pi runner to be stopped. Verification uses only the frozen packet commands in an isolated sandbox; Anvil acceptance remains a separate review.", "info"),
    ].filter(Boolean));
    if (busy) ctx.jobs.push(async () => {
      while (!ctx.signal.aborted) {
        await new Promise(resolve => {
          const done = () => { clearTimeout(timer); ctx.signal.removeEventListener("abort", done); resolve(); };
          const timer = setTimeout(done, 1500); ctx.signal.addEventListener("abort", done, { once: true });
        });
        if (ctx.signal.aborted) return;
        if (document.hidden) continue;
        try {
          const current = await workbenchRequest(`artifacts/${encodeURIComponent(bindingId)}`, { signal: ctx.signal });
          if (current.job?.status !== "running") { ctx.refresh(); return; }
        } catch (error) { if (!ctx.signal.aborted) outcome.textContent = error.message; return; }
      }
    });
    root.append(...[panel, entries(packet?.evidence || detail.evidence).length ? table(["Kind", "Reference"], entries(packet?.evidence || detail.evidence).map((item) => [words(item.kind || item.type || "evidence"), item.reference || item.url || item.id || "Not reported"])) : null, packet ? jsonDetails(packet, "Canonical work packet") : notice("A canonical work packet was not supplied.", "warning")].filter(Boolean));
    return root;
  }
  const roots = entries(detail.packet?.roots || detail.execution?.roots || detail.roots);
  const dependencies = entries(detail.dependencies || task.dependencies || task.blockers);
  const claims = entries(detail.active_claims);
  const rows = [
    ["Dependencies", dependencies.length ? `${dependencies.length} reported` : "None reported"],
    ["Readiness", detail.execution?.ready ? "Ready" : detail.execution?.reason || "Not ready"],
    ["Workspace", detail.execution?.workspace || detail.execution?.worktree || "Isolated workspace"],
    ["Roots", roots.length ? roots.map(item => item.root_id || item.id || "Declared root").join(", ") : "Not reported"],
    ["Claim", claims.length ? claims.map(claim => claim.status || claim.id || "active").join(", ") : "No active claim"],
  ];
  root.append(el("section", { class: "work-task-overview stack" },
    el("dl", { class: "work-task-facts" }, rows.flatMap(([label, value]) => [el("dt", { text: label }), el("dd", { text: value })])),
    detail.execution?.ready === false ? notice(detail.execution.reason || "The owner has not admitted this task for execution.", "warning") : null,
    detail.packet ? el("p", { class: "meta", text: "The canonical packet remains the authority for start, roots, and verification." }) : null,
  ));
  return root;
}
