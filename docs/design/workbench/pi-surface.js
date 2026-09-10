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
    session: "T-04 · protocol repair",
    model: "OpenAI / GPT-6 Astra",
    thinking: "High",
    draft:
      "Inspect the failing protocol fixture and propose the smallest safe repair.",
    steer: "",
    extensionText: "Review the generated patch before submitting it.",
    busy: false,
    label: "Idle",
    messages: [
      {
        role: "operator",
        text: "Continue T-04 in the isolated checkout. Preserve the declared route contract.",
      },
      {
        role: "pi",
        text: "I found the failure in a synthetic fixture. I will inspect the route boundary before suggesting an edit.",
      },
    ],
    tools: [
      {
        name: "read",
        detail: "tests/router/test_protocol.py · 84 lines",
        open: false,
      },
      {
        name: "grep",
        detail: "route contract · 3 matching files",
        open: false,
      },
    ],
    context: "Sample 18.4k / 114k",
    diff: false,
  };
  let savedSession = structuredClone(state);
  let reviewSession = null;
  function snapshot() {
    return structuredClone(state);
  }
  const pill = (text, tone = "") =>
    `<span class="pill ${tone}">${esc(text)}</span>`;
  const button = (text, action, kind = "quiet", disabled = false) =>
    `<button type="button" class="${kind}" data-action="${action}"${disabled ? " disabled" : ""}>${esc(text)}</button>`;
  const message = (entry) =>
    `<article class="chat-message pi-message ${entry.role}"><div class="eyebrow">${entry.role === "pi" ? "PI · SIMULATED" : "OPERATOR"}</div><p>${esc(entry.text)}</p></article>`;
  const tool = (item, index) =>
    `<details class="pi-tool"${item.open ? " open" : ""}><summary data-action="pi-tool-${index}"><span>Tool · ${esc(item.name)}</span>${pill("Recorded", "neutral")}</summary><pre>${esc(item.detail)}\nNo command was run in this design study.</pre></details>`;
  function render() {
    const busyControls = state.busy
      ? `${button("Stop", "pi-stop", "quiet")} ${button("Finish demo response", "pi-finish", "primary")}`
      : button("Run simulated turn", "pi-send", "primary");
    return `
      <div class="intro pi-intro"><div><div class="eyebrow">PI SESSIONS / ISOLATED TASK SPACE</div><h1>Keep the coding conversation in view.</h1><p>Follow a bounded Pi task with its model choice, context, changes, and recorded tool activity.</p></div><div class="intro-actions">${pill("Proposed Pi Web integration · simulated session", "warn")}</div></div>
      <section class="panel pi-console"><div class="panel-head"><div><h2>${esc(state.session)}</h2><p>${state.busy ? "Turn in progress · simulated manually" : "Saved simulated session · no runner connected"}</p></div><div class="chips">${pill(state.context, "neutral")}${pill(state.label, state.busy ? "warn" : "")}</div></div>
        <div class="panel-body pi-topline"><label>Cloud operator model<select id="pi-model"><option${state.model === "OpenAI / GPT-6 Astra" ? " selected" : ""}>OpenAI / GPT-6 Astra</option><option disabled>Claude model · unavailable</option></select><small>For Pi reasoning; separate from local benchmark targets.</small></label><label>Thinking<select id="pi-thinking">${["Minimal", "Low", "Medium", "High", "XHigh"].map((level) => `<option${state.thinking === level ? " selected" : ""}>${level}</option>`).join("")}</select><small>Applies to the next simulated turn.</small></label><div class="pi-session-actions"><span class="eyebrow">SESSION</span>${button("Resume investigation", "pi-session-resume", "small quiet", state.busy)}${button(reviewSession ? "Open review branch" : "New branch", "pi-session-new", "small quiet", state.busy)}${button("Menu", "pi-session-menu", "small quiet")}</div></div>
      </section>
      <div class="pi-layout">
        <section class="panel pi-conversation"><div class="panel-head"><div><h2>Conversation</h2><p>Structured runner events, rendered for review.</p></div></div><div class="panel-body"><div class="pi-thread">${state.messages.map(message).join("")}${state.busy ? `<article class="chat-message pi-message pi"><div class="eyebrow">PI · SIMULATED</div><p>Reviewing the fixture boundary…</p></article>` : ""}</div><form id="pi-form"><label for="pi-draft">Message Pi</label><textarea id="pi-draft" maxlength="4000" ${state.busy ? "disabled" : ""}>${esc(state.draft)}</textarea><div class="pi-composer-foot"><span class="section-copy">Demo only. No model, task runner, or workspace is connected.</span>${busyControls}</div></form>${state.busy ? `<div class="pi-steer"><label for="pi-steer">Steer while running</label><div>${`<input id="pi-steer" maxlength="500" value="${esc(state.steer)}" placeholder="e.g. inspect the failing assertion first">`}${button("Send steering note", "pi-steer", "small quiet")}</div></div>` : ""}</div></section>
        <aside class="pi-side">
          <section class="panel"><div class="panel-head"><div><h2>Activity</h2><p>Inspectable tool events</p></div></div><div class="panel-body pi-tools">${state.tools.map(tool).join("")}</div></section>
          <section class="panel"><div class="panel-head"><div><h2>Change review</h2><p>Recorded work product</p></div>${button(state.diff ? "Hide diff" : "Show diff", "pi-diff", "small quiet")}</div><div class="panel-body">${state.diff ? `<pre class="pi-diff">- return fallbackTier();\n+ return selectedTier();\n\nSynthetic review patch · not applied</pre>` : `<p class="section-copy">No applied changes. The runner should attach a diff and independently captured test result here.</p>`}</div></section>
          <section class="panel"><div class="panel-head"><div><h2>Extensions</h2><p>Review assistant · sample</p></div></div><div class="panel-body"><label for="pi-extension-input">Review note<input id="pi-extension-input" maxlength="300" value="${esc(state.extensionText)}"></label><div class="chips">${button("Request confirmation", "pi-extension-demo", "small quiet")}${button("Status widget", "pi-widget", "small quiet")}</div><p class="section-copy">Extensions can ask for input while you work. Review notes stay with this session.</p></div></section>
        </aside>
      </div>
      <section class="panel pi-context"><div class="panel-head"><div><h2>Task context</h2><p>Project, environment and recovery</p></div></div><div class="panel-body"><div class="form-grid"><div><span class="eyebrow">WORKSPACE</span><p>Isolated T-04 checkout</p></div><div><span class="eyebrow">LOCAL TARGET</span><p>Not selected here · benchmark control stays separate</p></div><div><span class="eyebrow">RECOVERY</span><p>Session file + event ledger proposed</p></div></div><details style="margin:15px 0"><summary class="text-link">Integration notes</summary><p class="section-copy">This concept has no runner attached. Production integration must resolve the approved work packet, claim and isolated environment before tool execution.</p></details><p class="section-copy">Design reference: <a class="text-link" href="https://github.com/agegr/pi-web" target="_blank" rel="noreferrer">Pi Web source ↗</a></p></div></section>`;
  }
  function repaint(api, focusId = "") {
    api.render();
    api.renderHUD?.();
    if (focusId)
      requestAnimationFrame(() => document.getElementById(focusId)?.focus());
  }
  function action(name, api) {
    if (!name?.startsWith("pi-")) return false;
    if (name === "pi-send") {
      if (state.busy) return true;
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
      repaint(api);
      return true;
    }
    if (name === "pi-finish") {
      state.busy = false;
      state.label = "Awaiting review";
      state.messages.push({
        role: "pi",
        text: "The synthetic review found one route-boundary assertion to tighten. No file was changed and no test was run.",
      });
      state.tools.push({
        name: "test",
        detail: "Not executed · design-only interface",
        open: false,
      });
      api.announce("Simulated Pi response completed. No model was called.");
      repaint(api);
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
      repaint(api);
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
      repaint(api);
      return true;
    }
    if (name.startsWith("pi-tool-")) {
      const index = Number(name.slice("pi-tool-".length));
      if (state.tools[index])
        state.tools[index].open = !state.tools[index].open;
      repaint(api);
      return true;
    }
    if (name === "pi-session-resume") {
      if (state.busy) {
        api.announce("Stop the simulated turn before switching sessions.");
        return true;
      }
      if (state.session === savedSession.session) {
        api.closeDialog?.();
        api.announce("The investigation is already open.");
        return true;
      }
      api.closeDialog?.();
      reviewSession = snapshot();
      Object.assign(state, structuredClone(savedSession));
      api.announce("Resumed the synthetic protocol-repair session.");
      repaint(api);
      return true;
    }
    if (name === "pi-session-new") {
      if (state.busy) {
        api.announce("Stop the simulated turn before creating a new branch.");
        return true;
      }
      api.closeDialog?.();
      if (state.session !== savedSession.session) {
        api.announce("The review branch is already open.");
        return true;
      }
      savedSession = snapshot();
      if (reviewSession) Object.assign(state, structuredClone(reviewSession));
      else {
        state.session = "T-04 · review branch";
        state.messages = [
          {
            role: "operator",
            text: "New synthetic review branch created from the saved task context.",
          },
        ];
        state.tools = [];
        state.diff = false;
        state.draft = "";
        state.steer = "";
        state.extensionText = "";
        state.label = "Idle";
        state.context = "Sample context · not reported";
      }
      api.announce(
        "Synthetic review branch opened. No workspace was provisioned.",
      );
      repaint(api);
      return true;
    }
    if (name === "pi-session-menu") {
      api.openDialog(
        "Session menu",
        `<p class="section-copy">This menu demonstrates meaningful session outcomes.</p><div class="form-grid"><div><h3>Resume investigation</h3><p>Returns to the saved synthetic task transcript.</p></div><div><h3>New branch</h3><p>Starts a clean review branch from task context.</p></div></div><div class="notice">Unavailable: delete, export, and remote-session controls. They require a real session store and authorization policy.</div>`,
        button("Resume", "pi-session-resume", "quiet", state.busy) +
          button("New branch", "pi-session-new", "primary", state.busy),
      );
      return true;
    }
    if (name === "pi-extension-demo") {
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
    if (target.id === "pi-draft") state.draft = target.value;
    if (target.id === "pi-steer") state.steer = target.value;
    if (target.id === "pi-extension-input") state.extensionText = target.value;
    return true;
  }
  function change(event, api) {
    const target = event.target;
    if (!target?.id?.startsWith("pi-")) return false;
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
