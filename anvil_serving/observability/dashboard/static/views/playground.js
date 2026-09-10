import { badge, button, el, empty, field, heading, jsonDetails, notice, select } from "./common.js";
import { workbenchRequest } from "./api.js";

let draft = { connector: "", model: "", preset: "", message: "", conversation: null, pending: null };
window.addEventListener("observatory-session-changed", () => { draft = { connector: "", model: "", preset: "", message: "", conversation: null, pending: null }; });
const active = data => ["running", "cancel_requested"].includes(data?.status);
export async function playgroundView(ctx) {
  const root = el("div", { class: "workbench-page stack", "data-story": "US-PLAY-01" }, heading("Playground", "Choose a model and preset, try a prompt, and keep the conversation."));
  let catalog, history, current = null;
  try {
    [catalog, history] = await Promise.all([workbenchRequest("catalog", { signal: ctx.signal }), workbenchRequest("conversations", { signal: ctx.signal })]);
    if (draft.conversation === null) draft.conversation = history.items?.[0]?.id || "";
    if (draft.conversation) current = await workbenchRequest(`conversations/${encodeURIComponent(draft.conversation)}`, { signal: ctx.signal });
  } catch (error) { root.append(notice(error.message, "danger")); return root; }
  const connectors = catalog.connectors || [], presets = catalog.presets || [];
  if (current) { draft.connector = current.connector_id; draft.model = current.model; draft.preset = current.effective?.preset_id || draft.preset; }
  if (!connectors.some(item => item.id === draft.connector)) draft.connector = connectors[0]?.id || "";
  const connector = connectors.find(item => item.id === draft.connector);
  if (!(connector?.models || []).includes(draft.model)) draft.model = connector?.models?.[0] || "";
  if (!presets.some(item => item.id === draft.preset)) draft.preset = presets[0]?.id || "";
  const rail = el("aside", { class: "conversation-rail panel stack", "aria-label": "Conversation history" });
  const output = el("div", { class: "conversation-output stack", "aria-live": "off" });
  const outcome = el("div", { class: "meta", role: "status" });
  const state = el("div", { class: "actions" });
  const sendButton = el("button", { type: "submit", class: "primary", text: "Send message" });
  const cancelButton = button("Stop response", async () => {
    if (!current) return;
    cancelButton.disabled = true;
    try { current = await workbenchRequest(`conversations/${encodeURIComponent(current.id)}/cancel`, { method: "POST", body: {}, signal: ctx.signal }); renderConversation(); schedule(); }
    catch (error) { outcome.textContent = error.message; cancelButton.disabled = false; }
  }, "secondary-button");
  const deleteButton = button("Delete conversation", async () => {
    if (!current || active(current)) return;
    deleteButton.disabled = true;
    try { await workbenchRequest(`conversations/${encodeURIComponent(current.id)}/delete`, { method: "POST", body: {}, signal: ctx.signal }); draft.conversation = ""; ctx.refresh(); }
    catch (error) { outcome.textContent = error.message; deleteButton.disabled = false; }
  }, "quiet-button");
  const connectorSelect = select(connectors.map(item => [item.id, item.label]), draft.connector, event => {
    draft.connector = event.target.value; draft.model = ""; ctx.render();
  });
  const modelSelect = select(connector?.models || [], draft.model, event => { draft.model = event.target.value; });
  const presetSelect = select(presets.map(item => [item.id, item.label]), draft.preset, event => { draft.preset = event.target.value; });
  const message = el("textarea", { maxlength: 16000, rows: 4, value: draft.message, placeholder: "Ask the selected model…", onInput: event => { draft.message = event.target.value; updateControls(); } });
  let poll = null, reading = false;
  function updateControls() {
    const blocked = active(current) || Boolean(draft.pending);
    connectorSelect.disabled = Boolean(current) || blocked;
    modelSelect.disabled = Boolean(current) || blocked;
    presetSelect.disabled = blocked;
    message.disabled = Boolean(draft.pending);
    sendButton.disabled = active(current) || !draft.message.trim() || !connector?.configured || !connector?.permitted || !draft.preset;
    sendButton.textContent = draft.pending ? "Retry same request" : "Send message";
    cancelButton.hidden = !active(current);
    cancelButton.disabled = current?.status === "cancel_requested";
    deleteButton.hidden = !current;
    deleteButton.disabled = active(current) || Boolean(draft.pending);
  }
  function renderConversation() {
    if (current) history.items = [current, ...(history.items || []).filter(item => item.id !== current.id)];
    renderRail();
    const messages = (current?.messages || []).map(item => el("article", { class: `message ${item.role}` }, el("strong", { text: item.role === "user" ? "You" : current.model }), el("pre", { text: item.content || "" }), item.effective ? jsonDetails(item.effective, "Request settings") : null));
    if (active(current) && current.output) messages.push(el("article", { class: "message assistant" }, el("strong", { text: current.model }), el("pre", { text: current.output })));
    output.replaceChildren(...messages, ...(!messages.length ? [empty("Your conversation will appear here.")] : []), ...(current?.error ? [notice(current.error, "warning")] : []));
    state.replaceChildren(current ? badge(current.status) : badge("new conversation"), ...(current?.usage ? [el("span", { class: "meta", text: `${current.usage.prompt_tokens ?? "?"} input · ${current.usage.completion_tokens ?? "?"} output tokens` })] : []));
    updateControls();
  }
  function schedule() {
    clearTimeout(poll);
    if (!active(current) || ctx.signal.aborted) return;
    poll = setTimeout(async () => {
      if (ctx.signal.aborted) return;
      if (document.hidden || reading) { schedule(); return; }
      reading = true;
      try { current = await workbenchRequest(`conversations/${encodeURIComponent(current.id)}`, { signal: ctx.signal }); renderConversation(); }
      catch (error) { if (!ctx.signal.aborted) outcome.textContent = error.message; }
      finally { reading = false; schedule(); }
    }, 1000);
  }
  ctx.signal.addEventListener("abort", () => clearTimeout(poll), { once: true });
  async function send(event) {
    event.preventDefault();
    if (sendButton.disabled) return;
    const body = draft.pending || { connector_id: draft.connector, model: draft.model, preset_id: draft.preset, message: draft.message, request_id: crypto.randomUUID(), ...(current ? { conversation_id: current.id } : {}) };
    draft.pending = body;
    sendButton.disabled = true;
    message.disabled = true;
    outcome.textContent = "Sending…";
    try {
      current = await workbenchRequest("messages", { method: "POST", body, signal: ctx.signal });
      draft.conversation = current.id; draft.message = ""; draft.pending = null; message.value = "";
      outcome.textContent = "Request accepted."; renderConversation(); schedule();
    } catch (error) {
      if (error.code !== "transport-unavailable" && error.name !== "AbortError") draft.pending = null;
      outcome.textContent = error.message; updateControls();
    }
  }
  function renderRail() {
    rail.replaceChildren(button("New conversation", () => { draft.conversation = ""; draft.pending = null; ctx.refresh(); }, "primary"), ...((history.items || []).map(item => button(`${item.title || item.model} · ${item.status}`, () => { draft.conversation = item.id; draft.pending = null; ctx.refresh(); }, item.id === current?.id ? "thread-selected" : "quiet-button"))));
  }
  const form = el("form", { class: "panel stack", onSubmit: send }, el("div", { class: "playground-controls" }, field("Connector", connectorSelect), field("Model", modelSelect), field("Request preset", presetSelect)), field("Message", message), el("div", { class: "actions" }, sendButton, cancelButton, deleteButton), outcome);
  if (!connectors.length || !presets.length) root.append(notice("A configured model connector and request preset are required. Review Connections in Settings.", "warning"));
  if (connector && (!connector.configured || !connector.permitted)) root.append(notice("This connection is unavailable or your account cannot send requests to it.", "warning"));
  root.append(el("div", { class: "playground-layout" }, rail, el("section", { class: "stack" }, state, output, form)));
  renderConversation(); schedule();
  return root;
}
