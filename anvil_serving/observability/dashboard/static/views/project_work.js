import { badge, button, el, empty, field, heading, jsonDetails, notice, route, section, select, table, words } from "./common.js";
import { workbenchRequest } from "./api.js";
import { piChatView } from "./pi_chat.js";
import { renderedMarkdown } from "./documentation.js";

const entries = (value) => Array.isArray(value) ? value : [];

export async function projectWorkView(ctx, projectId, taskId, tab = "overview") {
  const root = el("div", { class: "workbench-page stack", "data-story": "US-WORK-01" });
  if (!taskId) {
    const catalog = await workbenchRequest("catalog", { signal: ctx.signal });
    const projects = entries(catalog.projects);
    root.append(heading("Anvil work", "Move from a project plan to a focused task, a Pi conversation, and verified evidence."));
    if (!projects.length) { root.append(empty("No Anvil projects are connected to this account. Project connections are configured by the Workbench operator.")); return root; }
    const selected = projects.find(project => project.id === projectId) || projects[0];
    const data = await workbenchRequest(`projects/${encodeURIComponent(selected.id)}`, { signal: ctx.signal, timeout: 30000 });
    root.append(field("Project", select(projects.map(project => [project.id, project.label]), selected.id, event => { location.hash = route("work", event.target.value); })));
    const tasks = entries(data.tasks);
    const prdFilter = select([["", "All plans"], ...entries(data.prds).map(prd => [prd.id, prd.title || prd.id])], "", () => draw());
    const taskList = el("div", { class: "stack" });
    const planReader = el("div", { class: "stack", role: "region", "aria-label": "Selected project plan" });
    let selectedPlan = null;
    async function readPlan(prd) {
      selectedPlan = prd.id;
      planReader.replaceChildren(notice("Loading the persisted project plan…"));
      try {
        const document = await workbenchRequest(`projects/${encodeURIComponent(selected.id)}/prds/${encodeURIComponent(prd.id)}`, { signal: ctx.signal, timeout: 30000 });
        if (ctx.signal.aborted || selectedPlan !== prd.id) return;
        planReader.replaceChildren(section(prd.title || prd.id, button("Close plan", () => { selectedPlan = null; planReader.replaceChildren(); }, "quiet-button")),
          el("span", { class: "meta", text: `Persisted revision ${document.prd_revision} · ${document.source_digest?.slice(0, 12) || "Digest unavailable"}` }), renderedMarkdown(document.content));
        prdFilter.value = prd.id; draw();
      } catch (error) { if (!ctx.signal.aborted && selectedPlan === prd.id) planReader.replaceChildren(notice(error.message, "danger")); }
    }
    function draw() {
      const visible = tasks.filter(task => !prdFilter.value || task.id.startsWith(`${prdFilter.value}:`));
      taskList.replaceChildren(visible.length ? table(["Task", "State", "Priority", ""], visible.map(task => [el("div", { class: "small-stack" }, task.title, el("span", { class: "meta", text: task.id })), badge(task.status), words(task.priority), button("Open task", () => { location.hash = route("work", selected.id, task.id, "overview"); }, "quiet-button")])) : empty("No open tasks in this plan."));
    }
    root.append(el("section", { class: "panel stack" }, section("Project plans"), entries(data.prds).length ? table(["Plan", "State", ""], entries(data.prds).map(prd => [prd.title || prd.id, badge(prd.status), button("Read plan", () => readPlan(prd), "quiet-button")])) : empty("This project has no PRDs.")), planReader,
      el("section", { class: "panel stack" }, section("Open tasks"), field("Filter by plan", prdFilter), taskList));
    draw();
    if (data.truncated) root.append(notice("Showing the first 200 open tasks. Narrow the project in Anvil to inspect additional work.", "warning"));
    return root;
  }
  let detail;
  try { detail = await workbenchRequest(`projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`, { signal: ctx.signal, timeout: 30000 }); }
  catch (error) { return el("div", { class: "workbench-page stack" }, heading("Anvil work", "Task data is supplied by the configured State adapter."), notice(error.message, "danger")); }
  const task = detail.task || { id: taskId };
  const navigate = (name) => { location.hash = `#/work/${encodeURIComponent(projectId)}/${encodeURIComponent(taskId)}/${name}`; };
  root.append(heading(task.title || task.id, `Anvil work · ${detail.prd?.id || "PRD not reported"}`), el("nav", { class: "local-tabs", "aria-label": "Task sections" }, button("Overview", () => navigate("overview"), tab === "overview" ? "primary" : "quiet-button"), button("Agent session", () => navigate("agent"), tab === "agent" ? "primary" : "quiet-button"), button("Evidence", () => navigate("evidence"), tab === "evidence" ? "primary" : "quiet-button")));
  if (tab === "agent") { root.append(await piChatView(ctx, { projectId, taskId, detail })); return root; }
  if (tab === "evidence") {
    const packet = detail.packet;
    const panel = el("section", { class: "panel stack" }, section("Evidence"));
    let bindingId;
    let evidence;
    try {
      const sessions = await workbenchRequest(`pi/sessions?project=${encodeURIComponent(projectId)}&task=${encodeURIComponent(taskId)}`, { signal: ctx.signal });
      bindingId = entries(sessions.items)[0]?.binding_id;
      if (bindingId) evidence = await workbenchRequest(`artifacts/${encodeURIComponent(bindingId)}`, { signal: ctx.signal });
    } catch (error) { panel.append(notice(error.message, "warning")); }
    const artifact = evidence?.artifact;
    const digest = artifact?.artifact_digest || evidence?.verification?.artifact_digest || evidence?.submission?.artifact_digest;
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
    else panel.append(
      evidence ? table(["Baseline", "Packet", "Acceptance"], [[evidence.baseline_sha || "Not reported", evidence.packet_digest || "Not reported", words(evidence.acceptance || "unreviewed")]]) : empty("No captured evidence is available."),
      artifact ? el("pre", { class: "code-block", text: artifact.patch || "Patch retained privately; preview unavailable." }) : null,
      artifact ? table(["Path", "Status", "Mode"], entries(artifact.files).map((item) => [item.path, item.status, item.mode])) : null,
      el("div", { class: "actions" },
        button("Capture patch", () => act("review"), "secondary-button", busy || Boolean(evidence?.submission)),
        button("Verify & transfer reviewed patch", () => act("verify", { artifact_digest: digest }), "secondary-button", busy || !digest || Boolean(evidence?.verification?.passed)),
        button("Submit evidence to Anvil", () => act("submit", { artifact_digest: digest }), "primary", busy || !digest || !evidence?.verification?.passed || Boolean(evidence?.submission)),
        button("Release task lease", () => act("release"), "quiet-button", busy || evidence?.status === "released"),
      ), outcome,
      evidence?.verification ? jsonDetails(evidence.verification, evidence.verification.passed ? "Passed verification output" : "Failed verification output") : null,
      notice("Capture and transfer require the Pi runner to be stopped. Verification uses only the frozen packet commands in an isolated sandbox; Anvil acceptance remains a separate review.", "info"),
    );
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
  root.append(el("section", { class: "panel stack" }, section("Task state", badge(task.status || "unknown")), table(["Priority", "Feature", "PRD", "Execution"], [[words(task.priority || "not reported"), task.feature_id || "Not reported", detail.prd?.id || "Not reported", detail.execution?.ready ? "Ready" : detail.execution?.reason || "Not ready"]]), detail.execution?.ready === false ? notice(detail.execution.reason || "The owner has not admitted this task for execution.", "warning") : null));
  root.append(el("section", { class: "panel stack" }, section("Claims"), entries(detail.active_claims).length ? table(["Claim", "Status"], entries(detail.active_claims).map((claim) => [claim.id || "Not reported", claim.status || "active"])) : empty("No active claim is reported.")));
  if (detail.packet) root.append(el("section", { class: "panel stack" }, section("Canonical packet"), jsonDetails(detail.packet, "Work packet")));
  return root;
}
