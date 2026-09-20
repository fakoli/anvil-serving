import { button, el, field, jsonDetails, notice, select, words } from "./common.js";
import { workbenchRequest } from "./api.js";

// These are selected optional snippets only. No file discovery or prompt writes.
export function jevAssistanceView(ctx, catalog) {
  const root = el("section", { class: "panel stack", "aria-label": "Optional Jev assistance" });
  const policy = catalog.jev || {};
  const resources = catalog.advisory_resources || [];
  const choices = [["skill_suggestion", "Suggest an available skill"], ["context_ranking", "Rank optional context"], ["incident_triage", "Triage a selected observation"]];
  let revision = 0;
  let pending = false;
  let baseline = [];
  const feedback = el("div", { role: "status", "aria-live": "polite" });
  const ordered = el("ol");
  const details = el("div");
  const invalidate = () => {
    revision++;
    details.replaceChildren();
    ordered.replaceChildren();
    feedback.replaceChildren(notice(pending ? "Selection changed. Earlier advice is discarded; previously sent text cannot be recalled." : "No Jev advice is applied."));
  };
  const capability = select(choices, "skill_suggestion", () => { invalidate(); draw(); });
  const resource = select(resources.map(row => [row.id, row.label]), resources[0]?.id || "", invalidate);
  const intent = el("textarea", { rows: 3, maxlength: 4096, onInput: invalidate });
  const consent = el("input", { type: "checkbox", onChange: invalidate });
  const candidates = el("div", { class: "stack" });
  const rows = [];
  const add = () => {
    if (rows.length >= 24) return;
    const id = `candidate${rows.length + 1}`;
    const name = el("input", { type: "text", maxlength: 80, onInput: invalidate });
    const text = el("textarea", { rows: 3, maxlength: 3900, onInput: invalidate });
    const included = el("input", { type: "checkbox", checked: true, onChange: invalidate });
    rows.push({ id, name, text, included });
    candidates.append(el("fieldset", { class: "stack" }, el("legend", { text: `Optional candidate ${rows.length}` }),
      field("Include this candidate", included), field("Name", name), field("Safe description or selected snippet", text)));
    invalidate();
  };
  const drawOrder = order => ordered.replaceChildren(...order.map(id => {
    const row = baseline.find(candidate => candidate.id === id);
    return el("li", {}, el("strong", { text: row?.label || id }), el("p", { text: row?.text || "" }));
  }));
  const run = button("Request Jev advice", async () => {
    if (!consent.checked) { feedback.replaceChildren(notice("Select permission to export this text before requesting advice.")); return; }
    const kind = capability.value;
    const generation = crypto.randomUUID();
    const current = ++revision;
    baseline = rows.filter(row => row.included.checked).map(row => ({ id: row.id, label: row.name.value.trim(), text: row.text.value.trim() }));
    const input = kind === "incident_triage" ? { observation: intent.value } : {
      intent: intent.value,
      candidates: baseline.map(row => kind === "skill_suggestion" ? { id: row.id, description: `${row.label}: ${row.text}` } : { id: row.id, text: row.text }),
    };
    pending = true;
    run.disabled = true;
    feedback.replaceChildren(notice("Requesting optional Jev advice…"));
    try {
      const data = await workbenchRequest(`advisories/${kind}`, { method: "POST", signal: ctx.signal,
        body: { resource_id: resource.value, generation, allow_export: true, input } });
      if (ctx.signal.aborted || current !== revision || data.generation !== generation || !consent.checked) return;
      const annotation = data.annotation;
      const labels = { skill_suggestion: "Suggested with Jev", context_ranking: "Ranked with Jev", incident_triage: "Jev triage suggestion" };
      feedback.replaceChildren(notice(`${annotation.used ? labels[kind] : "Jev advice unavailable"} · ${annotation.model} · ${words(annotation.status)} · ${annotation.request_started ? "Cloud request attempted" : "No cloud request reported"}`));
      if (kind === "context_ranking") drawOrder(data.order || baseline.map(row => row.id));
      if (annotation.used && kind === "skill_suggestion") {
        const selected = annotation.answers.selection.choice;
        feedback.append(el("p", { text: selected === "none" ? "No suitable skill suggested." : baseline.find(row => row.id === selected)?.label || selected }));
      }
      if (data.next_check) feedback.append(el("p", { text: data.next_check }));
      details.replaceChildren(jsonDetails(annotation, "Jev provenance and experimental confidence"));
    } catch (error) {
      if (!ctx.signal.aborted && current === revision) feedback.replaceChildren(notice("Jev advice unavailable. The original selection is unchanged; a cloud request may have been sent.", "warning"));
    } finally { pending = false; draw(); }
  }, "primary");
  const draw = () => {
    const incident = capability.value === "incident_triage";
    candidates.hidden = incident;
    addButton.hidden = incident;
    run.disabled = pending || !resources.length || !policy.enabled || !policy.allow_api || !policy.allow_export || !(policy.capabilities || []).includes(capability.value);
  };
  const addButton = button("Add optional candidate", add, "quiet-button");
  add(); add();
  root.append(el("h2", { text: "Optional Jev assistance" }), notice(policy.disclosure || "Selected text is sent to TypeSafe/Jev in the cloud. Advice is experimental and cannot authorize execution or approval."),
    el("p", { text: `Operator policy: ${policy.enabled ? "enabled" : "off"}. Enabled capabilities: ${(policy.capabilities || []).map(words).join(", ") || "none"}.` }),
    field("Authorized source", resource), field("Assistance", capability), field("Intent or selected safe observation", intent), candidates, addButton,
    notice("Required instructions, explicit skill selections, attachments and active work packets stay pinned with their existing owner. Only the optional selection below can be reordered."),
    field("Permit TypeSafe/Jev to process only this selected text", consent, "Review for confidential text and credentials. Read permission alone does not permit cloud export."),
    el("div", { class: "actions" }, run,
      button("Restore original order", () => { revision++; drawOrder(baseline.map(row => row.id)); feedback.replaceChildren(notice("Original order restored. Earlier Jev advice is historical only.")); }, "quiet-button"),
      button("Disable for this selection", () => { consent.checked = false; invalidate(); }, "quiet-button")), feedback, ordered, details,
    el("p", { class: "meta", text: "Operator off switch: anvil-serving workbench jev disable --confirm. Nothing is installed, invoked, attached to a prompt or applied to a resource by this panel." }));
  draw();
  return root;
}
