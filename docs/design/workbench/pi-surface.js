/* Design-only Pi session surface. All records and responses are simulated. */
"use strict";
const PiSurface = (() => {
  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (char) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[char],
    );
  const state = {
    session: "T-04 · tool-contract review",
    model: "OpenAI / GPT-6 Astra",
    thinking: "High",
    draft:
      "Inspect the long-context tool-contract fixture and propose the smallest safe repair.",
    steer: "",
    extensionText: "Review the generated patch before submitting it.",
    busy: false,
    label: "Idle",
    messages: [
      {
        role: "operator",
        text: "Review T-04: validate long-context tool behavior. Preserve the declared tool contract.",
      },
      {
        role: "pi",
        text: "I found a synthetic long-context fixture failure. I will inspect the tool contract before suggesting an edit.",
      },
    ],
    tools: [
      {
        name: "read",
        detail: "tests/tool_contract.py · 84 lines",
        open: false,
      },
      {
        name: "grep",
        detail: "long-context tool contract · 3 matching files",
        open: false,
      },
    ],
    context: "Sample 18.4k / 114k",
    diff: false,
    thread: "validation",
    folders: { task: true, planning: false },
    newName: "",
  };
  const threads = {
    validation: structuredClone(state),
    followup: {
      ...structuredClone(state),
      session: "Tool contract follow-up",
      draft: "Compare the expected tool result with the long-context fixture.",
      messages: [
        {
          role: "operator",
          text: "T-04 follow-up: compare the expected tool result with the long-context fixture.",
        },
        {
          role: "pi",
          text: "I will keep this review bounded to the declared tool contract.",
        },
      ],
      tools: [
        {
          name: "read",
          detail: "tests/tool_contract.py · expected result fixture",
          open: false,
        },
      ],
    },
  };
  function snapshot() {
    return structuredClone(state);
  }
  function saveThread(key = state.thread) {
    const { folders, newName, ...session } = snapshot();
    threads[key] = {
      ...session,
      thread: key,
      parentThread: threads[key]?.parentThread ?? null,
    };
  }
  function activateThread(key) {
    const folders = structuredClone(state.folders);
    const newName = state.newName;
    Object.assign(state, structuredClone(threads[key]));
    state.thread = key;
    state.folders = folders;
    state.newName = newName;
  }
  const pill = (text, tone = "") =>
    `<span class="pill ${tone}">${esc(text)}</span>`;
  const button = (text, action, kind = "quiet", disabled = false) =>
    `<button type="button" class="${kind}" data-action="${action}"${disabled ? " disabled" : ""}>${esc(text)}</button>`;
  const message = (entry) =>
    `<article class="chat-message pi-message ${entry.role}"><div class="eyebrow">${entry.role === "pi" ? "PI · SIMULATED" : "OPERATOR"}</div><p>${esc(entry.text)}</p></article>`;
  const tool = (item, index) =>
    `<details class="pi-tool"${item.open ? " open" : ""}><summary id="pi-tool-${index}" data-action="pi-tool-${index}"><span>Tool · ${esc(item.name)}</span>${pill("Recorded", "neutral")}</summary><pre>${esc(item.detail)}\nNo command was run in this design study.</pre></details>`;
  const threadTitle = (key, thread) =>
    key === "validation"
      ? "Long-context validation"
      : key === "followup"
        ? "Tool contract follow-up"
        : thread.session;
  const threadRows = () =>
    Object.entries(threads)
      .map(
        ([key, thread]) =>
          `<button id="pi-thread-${esc(key)}" type="button" class="pi-thread-row${state.thread === key ? " selected" : ""}" data-action="pi-thread-${esc(key)}"${state.thread === key ? ' aria-current="page"' : ""} ${state.busy ? "disabled" : ""}>${esc(threadTitle(key, thread))}<small>${state.thread === key ? "Open" : "Saved"}</small></button>`,
      )
      .join("");
  function render(options = {}) {
    const embedded = options.embedded === true;
    const busyControls = state.busy
      ? `${button("Stop", "pi-stop", "quiet")} ${button("Finish demo response", "pi-finish", "primary")}`
      : `<button id="pi-send-button" type="button" class="primary" data-action="pi-send"${state.draft.trim() ? "" : " disabled"}>Send demo message</button>`;
    return `
      <section class="panel pi-embedded${embedded ? " is-embedded" : ""}">
        <div class="panel-head pi-toolbar"><div class="pi-toolbar-model"><span class="eyebrow">PI WEB</span>${pill("Integration study · simulated", "warn")}</div><label>Model<select id="pi-model"${state.busy ? " disabled" : ""}><option${state.model === "OpenAI / GPT-6 Astra" ? " selected" : ""}>OpenAI / GPT-6 Astra</option><option disabled>Claude model · unavailable</option></select></label><label>Thinking<select id="pi-thinking"${state.busy ? " disabled" : ""}>${["Minimal", "Low", "Medium", "High", "XHigh"].map((level) => `<option${state.thinking === level ? " selected" : ""}>${level}</option>`).join("")}</select></label><div class="chips pi-toolbar-status">${pill(state.context, "neutral")}${pill(state.label, state.busy ? "warn" : "")}</div></div>
        <div class="panel-body pi-web-layout"><aside class="pi-thread-sidebar"><div class="pi-sidebar-head"><span>CONVERSATIONS</span><button id="pi-new-conversation" type="button" class="small quiet" data-action="pi-new-conversation" aria-label="New conversation"${state.busy ? " disabled" : ""}>+</button></div><div class="pi-project-label">anvil-serving</div><div class="pi-folder-child"><button id="pi-folder-task" type="button" class="pi-folder" data-action="pi-folder-task" aria-expanded="${state.folders.task}">${state.folders.task ? "⌄" : "›"} Task conversations · T-04</button>${state.folders.task ? threadRows() : ""}</div><button id="pi-folder-planning" type="button" class="pi-folder" data-action="pi-folder-planning" aria-expanded="${state.folders.planning}">${state.folders.planning ? "⌄" : "›"} Planning · T-04</button>${state.folders.planning ? `<p class="pi-empty-folder">No saved planning conversations.</p>` : ""}</aside>
          <div class="pi-conversation"><div class="pi-thread-title"><div><span class="eyebrow">${esc(state.session)}</span><h2 id="pi-thread-heading" tabindex="-1">${state.thread === "validation" ? "Long-context validation" : esc(state.session)}</h2></div><button id="pi-session-menu" type="button" class="small quiet" data-action="pi-session-menu" aria-label="Conversation options">•••</button></div><div class="pi-thread">${state.messages.map(message).join("")}${state.busy ? `<article class="chat-message pi-message pi"><div class="eyebrow">PI · SIMULATED</div><p>Reviewing the synthetic long-context fixture…</p></article>` : ""}${state.tools.length ? `<div class="pi-inline-tools">${state.tools.map(tool).join("")}</div>` : ""}</div>${state.diff ? `<pre class="pi-diff">- preserveToolResult(result)\n+ preserveToolResult(result, contextWindow)\n\nSynthetic review patch · not applied</pre>` : ""}
            <form id="pi-form"><label for="pi-draft">Message Pi</label><textarea id="pi-draft" maxlength="4000" ${state.busy ? "disabled" : ""}>${esc(state.draft)}</textarea><div class="pi-composer-foot"><span class="section-copy">Simulated conversational turn for this task.</span>${busyControls}</div></form>${state.busy ? `<div class="pi-steer"><label for="pi-steer">Steer while running</label><div><input id="pi-steer" maxlength="500" value="${esc(state.steer)}" placeholder="e.g. inspect the failing assertion first">${button("Send steering note", "pi-steer", "small quiet")}</div></div>` : ""}
            <div class="pi-subtoolbar">${button(state.diff ? "Hide changes" : "Changes", "pi-diff", "small quiet")}${button("Extension review", "pi-extension-demo", "small quiet")}<details><summary>More</summary><a href="#settings/environment">Environment settings ↗</a><a href="https://github.com/agegr/pi-web" target="_blank" rel="noreferrer">Pi Web source ↗</a></details></div>
          </div>
        </div>
      </section>`;
  }
  function repaint(api, focusId = "") {
    const active = document.activeElement;
    const preferred = focusId || active?.id || active?.dataset?.action || "";
    api.render();
    api.renderHUD?.();
    queueMicrotask(() => {
      const target =
        preferred &&
        (document.getElementById(preferred) ||
          document.querySelector(`[data-action="${preferred}"]`));
      (target || document.getElementById("pi-thread-heading"))?.focus();
    });
  }
  function action(name, api) {
    if (!name?.startsWith("pi-")) return false;
    if (name === "pi-send") {
      if (state.busy || !state.draft.trim()) return true;
      if (state.model !== "OpenAI / GPT-6 Astra") {
        api.announce(
          "Selected cloud model is unavailable; no simulated turn started.",
        );
        return true;
      }
      state.busy = true;
      state.label = "Working";
      state.messages.push({
        role: "operator",
        text: state.draft || "Continue the bounded task.",
      });
      state.draft = "";
      api.announce("Simulated Pi turn started. No model or runner was called.");
      repaint(api, "pi-stop");
      return true;
    }
    if (name === "pi-finish") {
      state.busy = false;
      state.label = "Awaiting review";
      state.messages.push({
        role: "pi",
        text: "The synthetic review found one long-context tool-contract assertion to tighten. No file was changed and no test was run.",
      });
      state.tools.push({
        name: "test",
        detail: "Not executed · design-only interface",
        open: false,
      });
      api.announce("Simulated Pi response completed. No model was called.");
      repaint(api, "pi-draft");
      return true;
    }
    if (name === "pi-stop") {
      state.busy = false;
      state.label = "Stopped";
      state.messages.push({
        role: "pi",
        text: "The simulated turn was stopped before any change or test result.",
      });
      api.announce("Simulated Pi turn stopped.");
      repaint(api, "pi-draft");
      return true;
    }
    if (name === "pi-steer") {
      if (state.steer.trim()) {
        state.messages.push({
          role: "operator",
          text: `Steering note: ${state.steer.trim()}`,
        });
        state.steer = "";
        api.announce("Steering note added to the simulated event stream.");
        repaint(api);
      }
      return true;
    }
    if (name === "pi-diff") {
      state.diff = !state.diff;
      repaint(api, "pi-thread-heading");
      return true;
    }
    if (name.startsWith("pi-tool-")) {
      const index = Number(name.slice("pi-tool-".length));
      if (state.tools[index])
        state.tools[index].open = !state.tools[index].open;
      repaint(api);
      return true;
    }
    if (name === "pi-folder-task" || name === "pi-folder-planning") {
      state.folders[name === "pi-folder-task" ? "task" : "planning"] =
        !state.folders[name === "pi-folder-task" ? "task" : "planning"];
      repaint(api);
      return true;
    }
    if (name.startsWith("pi-thread-")) {
      if (state.busy) {
        api.announce("Stop the simulated turn before switching conversations.");
        return true;
      }
      const target = name.slice("pi-thread-".length);
      if (!threads[target] || target === state.thread) return true;
      saveThread();
      activateThread(target);
      api.announce("Switched synthetic T-04 conversation.");
      repaint(api, `pi-thread-${target}`);
      return true;
    }
    if (name === "pi-new-conversation") {
      if (state.busy) {
        api.announce("Stop the simulated turn before creating a conversation.");
        return true;
      }
      document.getElementById("pi-thread-heading")?.focus();
      api.openDialog(
        "New T-04 conversation",
        `<label>Conversation name<input id="pi-new-thread-name" maxlength="80" value="${esc(state.newName)}" placeholder="e.g. Fixture assertion review"></label><p class="section-copy">The new simulated conversation remains inside Task conversations · T-04.</p>`,
        button("Cancel", "close") +
          button(
            "Create conversation",
            "pi-new-conversation-confirm",
            "primary",
          ),
      );
      return true;
    }
    if (name === "pi-new-conversation-confirm") {
      const title = state.newName.trim() || "Fixture assertion review";
      const key = `new-${Object.keys(threads).length}`;
      saveThread();
      threads[key] = {
        ...snapshot(),
        session: title,
        thread: key,
        parentThread: null,
        draft: "",
        steer: "",
        extensionText: "",
        messages: [],
        tools: [],
        diff: false,
        context: "Sample usage · not reported",
        label: "Idle",
        busy: false,
      };
      api.closeDialog?.();
      activateThread(key);
      state.newName = "";
      api.announce("New synthetic T-04 conversation created.");
      repaint(api, "pi-thread-heading");
      return true;
    }
    if (name === "pi-session-resume") {
      if (state.busy) {
        api.announce("Stop the simulated turn before switching sessions.");
        return true;
      }
      const parent = threads[state.thread]?.parentThread;
      if (!parent) {
        api.closeDialog?.();
        api.announce("This is the parent conversation.");
        return true;
      }
      saveThread();
      api.closeDialog?.();
      activateThread(parent);
      api.announce("Resumed the synthetic parent conversation.");
      repaint(api, `pi-thread-${parent}`);
      return true;
    }
    if (name === "pi-session-new") {
      if (state.busy) {
        api.announce("Stop the simulated turn before creating a new branch.");
        return true;
      }
      api.closeDialog?.();
      saveThread();
      const parent = state.thread;
      const key = `branch-${Object.keys(threads).length}`;
      const source = structuredClone(threads[parent]);
      threads[key] = {
        ...source,
        thread: key,
        parentThread: parent,
        session: `${threadTitle(parent, source)} · branch`,
        draft: "",
        steer: "",
        extensionText: "",
        diff: false,
        context: "Sample usage · not reported",
        tools: source.tools.map((tool) => ({ ...tool, open: false })),
        busy: false,
        label: "Idle",
      };
      activateThread(key);
      api.announce(
        "Synthetic review branch opened. No workspace was provisioned.",
      );
      repaint(api, "pi-thread-heading");
      return true;
    }
    if (name === "pi-session-menu") {
      document.getElementById("pi-thread-heading")?.focus();
      api.openDialog(
        "Session menu",
        `<p class="section-copy">This menu demonstrates meaningful session outcomes.</p><div class="form-grid"><div><h3>Resume investigation</h3><p>Returns to the saved synthetic task transcript.</p></div><div><h3>New branch</h3><p>Starts a clean review branch from task context.</p></div></div><div class="notice">Unavailable: delete, export, and remote-session controls. They require a real session store and authorization policy.</div>`,
        button("Resume", "pi-session-resume", "quiet", state.busy) +
          button("New branch", "pi-session-new", "primary", state.busy),
      );
      return true;
    }
    if (name === "pi-extension-demo") {
      document.getElementById("pi-thread-heading")?.focus();
      api.openDialog(
        "Extension confirmation · simulated",
        `<p class="section-copy">An extension requests input before it may record its suggested review note.</p><div class="notice">${esc(state.extensionText || "No extension prompt supplied.")}</div><p class="section-copy">This represents Pi RPC extension UI. It does not grant an action or execute a tool.</p>`,
        button("Cancel", "close") +
          button("Record note", "pi-extension-allow", "primary"),
      );
      return true;
    }
    if (name === "pi-extension-allow") {
      api.closeDialog();
      state.messages.push({
        role: "pi",
        text: "Review note recorded in demo: " + state.extensionText,
      });
      api.announce("Simulated extension input recorded.");
      repaint(api);
      return true;
    }
    if (name === "pi-widget") {
      api.openDialog(
        "Extension status widget · simulated",
        `<div class="notice">Review gate · waiting for an independently captured test result</div><p class="section-copy">A browser adapter can render text widgets and status from Pi RPC. Terminal-native custom components need explicit translation.</p>`,
        button("Close", "close"),
      );
      return true;
    }
    return true;
  }
  function input(event) {
    const target = event.target;
    if (!target?.id?.startsWith("pi-")) return false;
    if (target.id === "pi-draft") {
      state.draft = target.value;
      const send = document.getElementById("pi-send-button");
      if (send) send.disabled = !state.draft.trim();
    }
    if (target.id === "pi-steer") state.steer = target.value;
    if (target.id === "pi-extension-input") state.extensionText = target.value;
    if (target.id === "pi-new-thread-name") state.newName = target.value;
    return true;
  }
  function change(event, api) {
    const target = event.target;
    if (target?.id !== "pi-model" && target?.id !== "pi-thinking") return false;
    if (state.busy) return true;
    if (target.id === "pi-model") state.model = target.value;
    if (target.id === "pi-thinking") state.thinking = target.value;
    repaint(api, target.id);
    return true;
  }
  function submit(event, api) {
    if (event.target?.id !== "pi-form") return false;
    event.preventDefault();
    return action("pi-send", api);
  }
  function status() {
    return { busy: state.busy, label: state.label };
  }
  return { render, action, input, change, submit, status };
})();
