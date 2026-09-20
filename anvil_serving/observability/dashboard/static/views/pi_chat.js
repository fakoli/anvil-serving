import { badge, button, el, empty, field, jsonDetails, notice, select, table, words } from "./common.js";
import { query, workbenchRequest } from "./api.js";

const drafts = new Map();
if (typeof window !== "undefined") window.addEventListener("observatory-session-changed", () => drafts.clear());
const MAX_MESSAGES = 128;
const MAX_MESSAGE_CHARS = 32_768;

const pick = (value, ...keys) => keys.map((key) => value?.[key]).find((item) => typeof item === "string") || "";
const list = (value) => Array.isArray(value) ? value : [];
const active = (status) => ["reserved", "running", "starting"].includes(status);

function text(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(text).join("");
  if (!value || typeof value !== "object") return "";
  return pick(value, "text", "textDelta", "delta", "content", "message")
    || text(value.content) || text(value.parts) || text(value.delta);
}

const messageRole = (message) => pick(message, "role");
const messageText = (message) => text(message?.content) || pick(message, "text");

function sessionTitle(session) {
  return pick(session, "title", "name", "session_id") || "Untitled conversation";
}

function providerOptions(catalog) {
  const pi = catalog?.pi || {};
  return Object.entries(pi.models || {}).map(([id, models]) => ({
    id,
    label: id,
    models: list(models),
    thinking: list(pi.thinking),
  }));
}

export function conversationDraft(store, taskKey, sessionId = "new") {
  const key = `${taskKey}/${sessionId}`;
  if (!store.has(key)) store.set(key, { message: "", provider: "", model: "", thinking: "" });
  return store.get(key);
}

// Only target metadata is retained here; prompts and credentials stay out of browser storage.
export function retainStart(storage, key, body) {
  const saved = storage.getItem(key);
  if (saved) {
    const original = JSON.parse(saved);
    if (!original || original.project_id !== body.project_id || original.task_id !== body.task_id
        || !["request_id", "provider_id", "model_id", "thinking_level"].every(name => typeof original[name] === "string" && original[name].length > 0))
      throw new Error("The retained start does not match this task. Its owner must reconcile it before another start.");
    return original;
  }
  storage.setItem(key, JSON.stringify(body));
  return body;
}

export function piSessionLink(sessionId, projectId, taskId) {
  const url = new URL(location.href);
  url.search = "";
  url.searchParams.set("pi-session", sessionId);
  url.searchParams.set("pi-project", projectId);
  url.searchParams.set("pi-task", taskId);
  url.hash = "#/playground";
  return url.href;
}

function extensionControl(event, send, resolved = false) {
  const data = event.data || {};
  if (data.type !== "extension_ui_request" || !data.id) return null;
  const title = data.title || "Extension input";
  if (resolved) {
    return el("div", { class: "notice info", role: "status" }, el("strong", { text: title }), el("p", { text: "Response sent." }));
  }
  if (data.method === "confirm") {
    return el("div", { class: "notice warning", "data-extension-request": data.id }, el("strong", { text: title }), el("p", { text: data.message || "Confirmation requested." }), button("Confirm", () => send("extension_response", { request_id: data.id, response: { confirmed: true } }), "primary"), button("Cancel", () => send("extension_response", { request_id: data.id, response: { cancelled: true } }), "quiet-button"));
  }
  if (data.method === "select") {
    const choice = select(list(data.options), list(data.options)[0] || "");
    return el("form", { class: "notice warning", "data-extension-request": data.id, onSubmit: (submitted) => { submitted.preventDefault(); send("extension_response", { request_id: data.id, response: { value: choice.value } }); } }, el("strong", { text: title }), field("Choice", choice), el("button", { type: "submit", class: "primary", text: "Submit response" }));
  }
  if (data.method === "input" || data.method === "editor") {
    const input = el(data.method === "editor" ? "textarea" : "input", { value: data.prefill || "", placeholder: data.placeholder || "" });
    return el("form", { class: "notice warning", "data-extension-request": data.id, onSubmit: (submitted) => { submitted.preventDefault(); send("extension_response", { request_id: data.id, response: { value: input.value } }); } }, el("strong", { text: title }), field("Response", input), el("button", { type: "submit", class: "primary", text: "Submit response" }));
  }
  return el("div", { class: "notice info", text: `${title}: ${words(data.method || "extension update")}` });
}

function trimMessages(items) {
  const messages = items.reduce((count, item) => count + (item.type === "message" ? 1 : 0), 0);
  if (messages > MAX_MESSAGES) items.splice(items.findIndex((item) => item.type === "message"), 1);
}

function mergeMessage(items, role, content, key = "") {
  if (!content) return;
  const prior = items.at(-1);
  if (role === "assistant" && key && prior?.type === "message" && prior.role === role && prior.key === key) {
    const next = content.startsWith(prior.content) ? content : prior.content + content;
    prior.content = next.slice(-MAX_MESSAGE_CHARS);
    return;
  }
  items.push({ type: "message", role, content: content.slice(-MAX_MESSAGE_CHARS), key });
  trimMessages(items);
}

export function transcriptItems(events, locallyResolved = []) {
  const items = [];
  const resolved = new Set([...locallyResolved, ...events
    .filter((event) => event.kind === "command_accepted" && event.data?.name === "extension_response")
    .map((event) => event.data?.request_id)
    .filter(Boolean)]);
  let assistantMessage = null;
  const finishAssistant = () => { assistantMessage = null; };
  const appendAssistant = (content) => {
    if (!content) return;
    if (!assistantMessage) {
      assistantMessage = { type: "message", role: "assistant", content: "", key: "" };
      items.push(assistantMessage);
      trimMessages(items);
    }
    assistantMessage.content = (assistantMessage.content + content).slice(-MAX_MESSAGE_CHARS);
  };
  const replaceAssistant = (content) => {
    if (!content) return;
    if (!assistantMessage) {
      assistantMessage = { type: "message", role: "assistant", content: "", key: "" };
      items.push(assistantMessage);
      trimMessages(items);
    }
    assistantMessage.content = content.slice(-MAX_MESSAGE_CHARS);
  };
  for (const event of events) {
    const data = event.data || {};
    if (event.kind === "command_accepted") {
      if (["prompt", "steer"].includes(data.name) && typeof data.message === "string") {
        finishAssistant();
        mergeMessage(items, "user", data.message, data.command_id || `command-${event.cursor}`);
      }
      continue;
    }
    if (data.type === "extension_ui_request" && data.id) {
      items.push({ type: "extension", event, resolved: resolved.has(data.id) });
      continue;
    }
    if (data.type === "turn_start") {
      finishAssistant();
      continue;
    }
    if (data.type === "message_start") {
      finishAssistant();
      if (messageRole(data.message) === "assistant") replaceAssistant(messageText(data.message));
      continue;
    }
    if (data.type === "message_update") {
      const update = data.assistantMessageEvent || {};
      if (update.type === "text_delta" && typeof update.delta === "string") appendAssistant(update.delta);
      else if (update.type === "text_end") replaceAssistant(text(update.content));
      else if (!update.type && messageRole(data.message) === "assistant") replaceAssistant(messageText(data.message));
      continue;
    }
    if (data.type === "message_end") {
      if (messageRole(data.message) === "assistant") replaceAssistant(messageText(data.message));
      finishAssistant();
      continue;
    }
    if (event.kind === "tool" || String(data.type || "").startsWith("tool_execution")) {
      finishAssistant();
      const name = pick(data, "toolName", "tool_name", "name") || "Tool event";
      items.push({ type: "tool", name, data });
    } else if (event.kind === "command_outcome" && data.ok === false) {
      items.push({ type: "notice", tone: "danger", message: data.error || "Pi rejected this command." });
    } else if (event.kind === "error" || data.type === "error") {
      finishAssistant();
      items.push({ type: "notice", tone: "danger", message: pick(data, "message", "error") || "Pi runner reported an error." });
    } else if (!["command_accepted", "lifecycle"].includes(event.kind)) {
      finishAssistant();
      items.push({ type: "event", name: words(event.kind || data.type || "Pi event"), data });
    }
  }
  return items;
}

function transcript(events, send, locallyResolved) {
  return transcriptItems(events, locallyResolved).map((item) => {
    if (item.type === "message") return el("article", { class: `message ${item.role}` }, el("strong", { text: item.role === "user" ? "You" : "Pi" }), el("pre", { text: item.content }));
    if (item.type === "extension") return extensionControl(item.event, send, item.resolved);
    if (item.type === "tool") return el("details", { class: "message tool-event" }, el("summary", { text: item.name }), jsonDetails(item.data, "Tool details"));
    if (item.type === "notice") return notice(item.message, item.tone);
    return el("details", { class: "message event" }, el("summary", { text: item.name }), jsonDetails(item.data, "Event details"));
  });
}

export function sessionPresentationChanged(before, after) {
  if (!before || !after) return before !== after;
  return ["status", "provider_id", "model_id", "thinking_level", "official_session_id"]
    .some((key) => before[key] !== after[key]);
}

export function pendingTranscriptControlIds(events, locallyResolved = []) {
  return transcriptItems(events, locallyResolved)
    .filter((item) => item.type === "extension" && !item.resolved && ["confirm", "select", "input", "editor"].includes(item.event.data?.method))
    .map((item) => item.event.data.id);
}

export function shouldPreserveTranscriptControls(events, locallyResolved = [], renderedIds = []) {
  const pending = new Set(pendingTranscriptControlIds(events, locallyResolved));
  return renderedIds.some((id) => pending.has(id));
}

export function replaceTranscript(output, nodes, activeElement, preserveControls = false) {
  if (preserveControls || output.contains(activeElement)) return false;
  output.replaceChildren(...nodes);
  return true;
}

function threadButtons(sessions, selected, choose, disabled) {
  const children = new Map();
  for (const item of sessions) {
    const parent = item.parent_session_id || "root";
    children.set(parent, [...(children.get(parent) || []), item]);
  }
  const render = (parent, depth = 0) => (children.get(parent) || []).flatMap((item) => {
    const control = button(`${depth ? "↳ " : ""}${sessionTitle(item)}`, () => choose(item.session_id), item.session_id === selected ? "primary" : "quiet-button", disabled);
    if (item.session_id === selected) control.setAttribute("aria-current", "page");
    return [control, ...render(item.session_id, depth + 1)];
  });
  return render("root");
}

export async function piChatView(ctx, { projectId, taskId, detail, sessionId = "" }) {
  const root = el("section", { class: "pi-chat panel stack", "aria-label": "Pi task conversation" });
  const state = { sessions: [], selected: sessionId, cursor: 0, events: [], resolvedExtensions: new Set(), transcriptDirty: false, transcriptDeferred: false, pending: false, stopped: false, timer: null, polling: false, error: "" };
  const taskKey = `${ctx.session?.identity}/${projectId}/${taskId}`;
  const startKey = `anvil-pi-start/${taskKey}`;
  const newDraft = conversationDraft(drafts, taskKey);
  let catalog = null;
  let preferences = {};
  let projectPreferences = null;
  let transcriptRoot = null;
  const newClaimReady = detail?.execution?.ready !== false;
  const pendingStart = () => {
    try { return JSON.parse(sessionStorage.getItem(startKey) || "null"); } catch { return null; }
  };
  const clearTimer = () => { if (state.timer) clearTimeout(state.timer); state.timer = null; };
  const selected = () => state.sessions.find((item) => item.session_id === state.selected);
  const commandKey = () => `anvil-pi-command/${taskKey}/${state.selected}`;
  const selectedDraft = () => {
    const draft = conversationDraft(drafts, taskKey, state.selected || "new");
    if (state.selected && !("pendingCommand" in draft)) {
      draft.pendingCommand = null;
      try {
        const raw = sessionStorage.getItem(commandKey());
        if (raw) {
          const retained = JSON.parse(raw);
          if (retained.session_id !== state.selected || typeof retained.request_id !== "string" || !retained.request_id || typeof retained.name !== "string") throw Error("Invalid command receipt.");
          draft.pendingCommand = { request_id: retained.request_id, name: retained.name, metadataOnly: true };
        }
      } catch { draft.commandStorageError = "Command recovery metadata is unavailable. Resolve browser storage before sending another command."; }
    }
    return draft;
  };
  const schedulePoll = () => {
    clearTimer();
    if (!state.stopped && !document.hidden && active(selected()?.status)) state.timer = setTimeout(loadEvents, 1200);
  };
  const visibilityChanged = () => {
    if (document.hidden) clearTimer();
    else void loadEvents();
  };
  document.addEventListener("visibilitychange", visibilityChanged);
  ctx.signal.addEventListener("abort", () => {
    state.stopped = true; clearTimer(); document.removeEventListener("visibilitychange", visibilityChanged);
  }, { once: true });

  const commandsAllowed = () => selected()?.status === "running" && !state.pending && !selectedDraft().pendingCommand && !selectedDraft().commandStorageError;
  const replaceSession = (item) => { state.sessions = state.sessions.map((value) => value.session_id === item.session_id ? { ...value, ...item } : value); };
  const updateTranscript = () => {
    if (!transcriptRoot) return;
    const nodes = transcript(state.events, send, state.resolvedExtensions);
    if (!nodes.length) nodes.push(empty(selected() ? "No runner events have arrived." : "Choose a provider, model and thinking level to create a conversation."));
    const renderedControls = [...transcriptRoot.querySelectorAll("[data-extension-request]")]
      .map((node) => node.getAttribute("data-extension-request"));
    const preserveControls = shouldPreserveTranscriptControls(state.events, state.resolvedExtensions, renderedControls);
    if (replaceTranscript(transcriptRoot, nodes, document.activeElement, preserveControls)) {
      state.transcriptDirty = false;
      state.transcriptDeferred = false;
      return;
    }
    state.transcriptDirty = true;
    if (!state.transcriptDeferred) {
      state.transcriptDeferred = true;
      transcriptRoot.addEventListener("focusout", () => queueMicrotask(updateTranscript), { once: true });
    }
  };
  const send = async (name, payload = {}) => {
    if (state.pending || (!commandsAllowed() && !selectedDraft().pendingCommand)) return;
    const draft = selectedDraft();
    const prior = draft.pendingCommand;
    const body = { ...(prior || { name, payload, request_id: crypto.randomUUID() }), project_id: projectId, task_id: taskId };
    state.error = ""; state.pending = true; render();
    try {
      // Keep only the receipt identity across reloads, never command content.
      sessionStorage.setItem(commandKey(), JSON.stringify({ session_id: state.selected, request_id: body.request_id, name: body.name }));
      draft.pendingCommand = body;
      let result;
      if (prior) {
        try { result = await workbenchRequest(query(`pi/sessions/${encodeURIComponent(state.selected)}/commands/${encodeURIComponent(body.request_id)}`, { project: projectId, task: taskId }), { signal: ctx.signal }); }
        catch (error) { if (error.status !== 404) throw error; }
      }
      if (!result && body.metadataOnly) {
        sessionStorage.removeItem(commandKey()); draft.pendingCommand = null;
        throw new Error("No command receipt was found. Its content was not retained across reload. Inspect the stream before explicitly sending a new command.");
      }
      if (!result) result = await workbenchRequest(`pi/sessions/${encodeURIComponent(state.selected)}/command`, { method: "POST", body, signal: ctx.signal });
      if (!result.accepted) throw new Error("Pi command delivery remains uncertain. Inspect the retained stream or stop the runner before continuing; the command will not be replayed.");
      sessionStorage.removeItem(commandKey());
      draft.pendingCommand = null;
      name = body.name; payload = body.payload;
      if (["prompt", "steer"].includes(name) && result.accepted) selectedDraft().message = "";
      if (name === "extension_response" && payload && result.accepted) state.resolvedExtensions.add(payload.request_id);
      ctx.announce("Pi command accepted. Completion is reported by the runner event stream.");
      await loadEvents();
    } catch (error) { state.error = error.message; }
    finally { state.pending = false; render(); schedulePoll(); }
  };
  const loadEvents = async () => {
    if (!state.selected || state.stopped || state.polling || document.hidden) return;
    state.polling = true;
    const sessionId = state.selected;
    try {
      const page = await workbenchRequest(query(`pi/sessions/${encodeURIComponent(sessionId)}/events`, { project: projectId, task: taskId, cursor: state.cursor }), { signal: ctx.signal });
      if (sessionId !== state.selected) return;
      const prior = selected();
      const gapChanged = page.gap && !state.error;
      if (page.gap) state.error = "Earlier retained events expired; the visible cursor resumes from the available history.";
      const known = new Set(state.events.map((event) => event.cursor));
      const added = list(page.events).filter((event) => !known.has(event.cursor));
      state.events = [...state.events, ...added].slice(-256);
      state.cursor = Number.isFinite(page.next_cursor) ? page.next_cursor : state.cursor;
      if (page.session) replaceSession(page.session);
      if (!transcriptRoot || gapChanged || sessionPresentationChanged(prior, page.session)) render();
      else if (added.length || state.transcriptDirty) updateTranscript();
    } catch (error) { if (!state.stopped) { state.error = error.message; render(); } }
    finally { state.polling = false; schedulePoll(); }
  };
  const selectSession = (id) => {
    if (state.pending || id === state.selected) return;
    state.selected = id; state.cursor = 0; state.events = []; state.resolvedExtensions = new Set(); render(); void loadEvents();
  };
  const openNew = () => {
    if (state.pending) return;
    state.selected = ""; state.cursor = 0; state.events = []; state.resolvedExtensions = new Set(); state.error = ""; render();
  };
  const loadSessions = async () => {
    const data = await workbenchRequest(query("pi/sessions", { project: projectId, task: taskId }), { signal: ctx.signal });
    state.sessions = list(data.items);
    if (!state.sessions.some((item) => item.session_id === state.selected)) {
      if (sessionId) throw new Error("The linked Pi session is unavailable for this task.");
      state.selected = pendingStart() ? "" : state.sessions[0]?.session_id || "";
    }
    if (state.selected) { state.cursor = 0; state.events = []; state.resolvedExtensions = new Set(); await loadEvents(); }
  };
  const create = async (parentSessionId = undefined, target = newDraft) => {
    if (state.pending || (!parentSessionId && (!newClaimReady || projectPreferences?.stale) && !pendingStart()) || !target.provider || !target.model || !target.thinking) return;
    state.error = ""; state.pending = true; render();
    try {
      const previous = pendingStart();
      const body = retainStart(sessionStorage, startKey, { project_id: projectId, task_id: taskId, request_id: crypto.randomUUID(), provider_id: target.provider, model_id: target.model, thinking_level: target.thinking,
        ...(parentSessionId ? { parent_session_id: parentSessionId } : projectPreferences?.defaults || {}) });
      let data;
      if (previous) {
        // Reconcile before retrying; the owner receives the original frozen request only.
        try {
          const recovery = await workbenchRequest(query(`pi/starts/${encodeURIComponent(body.request_id)}`, { project: projectId, task: taskId }), { signal: ctx.signal });
          if (recovery.project_id !== projectId || recovery.task_id !== taskId || !Array.isArray(recovery.sessions) || recovery.sessions.length > 1)
            throw new Error("The retained start does not match this task.");
          data = recovery.sessions[0];
          if (data && (data.project_id !== projectId || data.task_id !== taskId || typeof data.session_id !== "string"))
            throw new Error("The retained session does not match this task.");
        } catch (error) { if (error.status !== 404) throw error; }
      }
      if (!data) data = await workbenchRequest("pi/sessions", { method: "POST", body, signal: ctx.signal, timeout: 30000 });
      sessionStorage.removeItem(startKey);
      state.sessions = [data, ...state.sessions.filter((item) => item.session_id !== data.session_id)]; state.selected = data.session_id; state.cursor = 0; state.events = []; state.resolvedExtensions = new Set();
      conversationDraft(drafts, taskKey, data.session_id).message = "";
      ctx.announce(parentSessionId ? "Pi branch created." : "Pi conversation created.");
      await loadEvents();
    } catch (error) { state.error = error.message; }
    finally { state.pending = false; render(); schedulePoll(); }
  };
  const resume = async () => {
    if (!selected() || state.pending) return;
    state.error = ""; state.pending = true; render();
    try { replaceSession(await workbenchRequest(`pi/sessions/${encodeURIComponent(state.selected)}/resume`, { method: "POST", body: { project_id: projectId, task_id: taskId }, signal: ctx.signal })); ctx.announce("Pi session recovery was requested."); await loadEvents(); }
    catch (error) { state.error = error.message; }
    finally { state.pending = false; render(); schedulePoll(); }
  };
  const lifecycle = async (action) => {
    const current = selected();
    if (!current || state.pending) return;
    state.error = ""; state.pending = true; render();
    try {
      const data = await workbenchRequest(`pi/sessions/${encodeURIComponent(current.session_id)}/${action}`, { method: "POST", body: { project_id: projectId, task_id: taskId }, signal: ctx.signal });
      if (action === "delete") { state.sessions = state.sessions.filter((item) => item.session_id !== current.session_id); state.selected = state.sessions[0]?.session_id || ""; state.cursor = 0; state.events = []; state.resolvedExtensions = new Set(); if (state.selected) void loadEvents(); }
      else replaceSession(data);
    } catch (error) { state.error = error.message; }
    finally { state.pending = false; render(); schedulePoll(); }
  };
  const branch = async () => {
    const parent = selected();
    if (!parent || state.pending) return;
    if (active(parent.status)) await lifecycle("stop");
    if (state.error) return;
    await create(parent.session_id, { provider: parent.provider_id, model: parent.model_id, thinking: parent.thinking_level });
  };
  const render = () => {
    const current = selected();
    const draft = selectedDraft();
    const providers = providerOptions(catalog);
    const retained = !current && pendingStart();
    if (retained) Object.assign(draft, { provider: retained.provider_id, model: retained.model_id, thinking: retained.thinking_level });
    if (!current) {
      const preferredProvider = preferences.pi_provider;
      draft.provider = providers.some((item) => item.id === draft.provider) ? draft.provider : (providers.some((item) => item.id === preferredProvider) ? preferredProvider : providers[0]?.id || "");
    }
    const provider = providers.find((item) => item.id === (current?.provider_id || draft.provider));
    const models = list(provider?.models);
    const levels = list(provider?.thinking);
    if (!current) {
      draft.model = models.includes(draft.model) ? draft.model : (models.includes(preferences.pi_model) ? preferences.pi_model : models[0] || "");
      draft.thinking = levels.includes(draft.thinking) ? draft.thinking : (levels.includes(preferences.pi_thinking) ? preferences.pi_thinking : levels[0] || "");
    }
    const target = current ? { provider: current.provider_id, model: current.model_id, thinking: current.thinking_level } : draft;
    const providerSelect = select(providers.map((item) => [item.id, item.label]), target.provider, (event) => { if (!current) { draft.provider = event.target.value; draft.model = ""; render(); } }, { disabled: state.pending || !!current || !!retained });
    const modelSelect = select(models, target.model, (event) => { if (current) void send("set_model", { provider: current.provider_id, model_id: event.target.value }); else { draft.model = event.target.value; } }, { disabled: state.pending || !!retained || (!!current && !commandsAllowed()) });
    const thinkingSelect = select(levels, target.thinking, (event) => { if (current) void send("set_thinking_level", { level: event.target.value }); else { draft.thinking = event.target.value; } }, { disabled: state.pending || !!retained || (!!current && !commandsAllowed()) });
    const message = el("textarea", { maxlength: 16000, value: draft.message, placeholder: current ? "Message the selected Pi session…" : "Create a conversation to send a message.", disabled: !commandsAllowed() });
    const submit = button("Send", () => send("prompt", { message: message.value.trim() }), "primary", !commandsAllowed() || !draft.message.trim());
    message.addEventListener("input", () => { draft.message = message.value; submit.disabled = steer.disabled = !message.value.trim() || !commandsAllowed(); });
    const steer = button("Steer", () => send("steer", { message: message.value.trim() }), "quiet-button", !commandsAllowed() || !draft.message.trim());
    const newButton = button("+ New conversation", openNew, current ? "quiet-button" : "primary", state.pending);
    if (!current) newButton.setAttribute("aria-current", "page");
    const rail = el("aside", { class: "pi-thread-rail", "aria-label": "Task conversation folders" }, el("div", { class: "meta", text: "Task conversations" }), ...threadButtons(state.sessions, state.selected, selectSession, state.pending), newButton);
    const toolbar = el("div", { class: "pi-toolbar" }, el("div", {}, el("strong", { text: current ? sessionTitle(current) : "New conversation" }), current ? badge(current.status) : null), current && !active(current.status) ? button("Resume", resume, "quiet-button", state.pending) : null, current ? button("Branch", branch, "quiet-button", state.pending) : null, current && active(current.status) ? button("Stop runner", () => lifecycle("stop"), "quiet-button", state.pending) : null, current && !active(current.status) ? button("Delete history", () => lifecycle("delete"), "quiet-button", state.pending) : null);
    const messages = transcript(state.events, send, state.resolvedExtensions);
    const output = el("div", { class: "conversation-output stack", "aria-live": "polite" }, ...messages, !messages.length ? empty(current ? "No runner events have arrived." : "Choose a provider, model and thinking level to create a conversation.") : null);
    transcriptRoot = output;
    state.transcriptDirty = false;
    state.transcriptDeferred = false;
    const createButton = button(retained ? "Reconcile original start" : "Start isolated run", () => create(), "primary", state.pending || ((!newClaimReady || projectPreferences?.stale) && !retained) || !draft.provider || !draft.model || !draft.thinking);
    const actions = current ? [submit, steer, button("Stop", () => send("abort"), "quiet-button", !commandsAllowed())] : [createButton];
    if (current && draft.pendingCommand) actions.push(button("Reconcile command", () => send(draft.pendingCommand.name, draft.pendingCommand.payload), "secondary-button", state.pending));
    const compose = el("form", { class: "stack", onSubmit: (submitted) => { submitted.preventDefault(); if (current && message.value.trim()) void send("prompt", { message: message.value.trim() }); else if (!current) void create(); } }, el("div", { class: "form-grid" }, field("Provider", providerSelect), field("Model", modelSelect), field("Thinking", thinkingSelect)), current ? field("Message", message) : null, el("div", { class: "actions" }, actions));
    const children = [toolbar, el("div", { class: "pi-chat-layout" }, rail, el("div", { class: "stack" }, output, compose))];
    if (!current) children.unshift(el("section", { class: "stack", "aria-label": "Run with Pi preview" },
      el("h2", { text: "Run with Pi" }),
      notice("Starting acquires a canonical task lease and creates an isolated workspace. The lease and frozen roots stay with this run; submission requires independent Anvil acceptance."),
      detail?.packet ? jsonDetails(detail.packet, "Preview canonical work packet") : notice("The canonical packet is unavailable.", "warning"),
      projectPreferences ? table(["Root", "Requested task access", "Primary directory"], projectPreferences.roots.map(item => [item.label,
        item.state_root || (retained || projectPreferences.defaults).writable_root_ids?.includes(item.id) ? "Write — subject to owner claim" : "Read-only context",
        (retained || projectPreferences.defaults).primary_root_id === item.id ? "Yes" : ""])) : null,
      el("a", { href: "#/settings/projects", text: "Change defaults for new project sessions" }),
      projectPreferences?.stale ? notice("Declared roots changed. Review and save the project defaults before starting a new task session.", "warning") : null,
      retained ? notice("A previous start has an unconfirmed outcome. Reconcile it before creating another run.", "warning") : null));
    if (current) children.push(el("nav", { class: "actions", "aria-label": "Run links" },
      el("a", { href: piSessionLink(current.session_id, projectId, taskId), text: "Open this session in Pi" }),
      current.run_id ? el("a", { href: `#/workbench/${encodeURIComponent(current.run_id)}`, text: "Open run in Workbench" }) : null,
      el("a", { href: `#/work/${encodeURIComponent(projectId)}/${encodeURIComponent(taskId)}/evidence`, text: "Review task evidence" })));
    children.push(notice("Managed task sessions accept text prompts. Attachments and queued follow-up are unavailable; use Steer during a turn or send after it finishes. Changing provider requires a new isolated conversation."));
    if (state.error) children.unshift(notice(state.error, "danger"));
    if (draft.commandStorageError) children.unshift(notice(draft.commandStorageError, "danger"));
    if (!newClaimReady && !current) children.push(notice(detail?.execution?.reason || "Task execution is not ready for a new Pi claim.", "warning"));
    root.replaceChildren(...children);
  };
  try {
    [catalog, preferences, projectPreferences] = await Promise.all([workbenchRequest("catalog", { signal: ctx.signal }), workbenchRequest("preferences", { signal: ctx.signal }),
      workbenchRequest(`projects/${encodeURIComponent(projectId)}/preferences`, { signal: ctx.signal })]);
    await loadSessions();
  } catch (error) { state.error = error.message; }
  render();
  schedulePoll();
  return root;
}
