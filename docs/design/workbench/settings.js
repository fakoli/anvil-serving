/* Offline Settings concept. Drafts and saved preferences last until reload. */
"use strict";
const SettingsSurface = (() => {
  const esc = (value) =>
    String(value).replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const sections = {
    general: {
      label: "General",
      icon: "◫",
      caption: "Workspace & appearance",
      description:
        "A familiar starting point, with the amount of detail you need.",
    },
    connections: {
      label: "Connections",
      icon: "◇",
      caption: "Services & providers",
      description:
        "The services behind your workspace, with ownership and connection state in view.",
    },
    environment: {
      label: "Pi environments",
      icon: "π",
      caption: "Session defaults",
      description:
        "Set the starting envelope for new agent sessions. Each launch still has its own review.",
    },
    access: {
      label: "Access & sessions",
      icon: "⌘",
      caption: "Anvil Connect",
      description:
        "Control resource grants and issued browser or terminal sessions through Anvil Connect.",
    },
    data: {
      label: "Data & evidence",
      icon: "▤",
      caption: "Storage & retention",
      description:
        "Know where conversations, run results, and project evidence belong.",
    },
  };
  const saved = {
    general: {
      name: "Local research",
      start: "bench",
      density: "comfortable",
      showHud: true,
    },
    environment: { cpus: 2, memory: 4, minutes: 30 },
  };
  const drafts = structuredClone(saved);
  let section = "general";
  const savedNotices = { general: "", environment: "" };
  const dirty = (key) =>
    saved[key] && JSON.stringify(saved[key]) !== JSON.stringify(drafts[key]);
  const option = (value, label, selected) =>
    `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`;
  const group = (title, description, content) =>
    `<section class="settings-group"><div class="settings-group-head"><h3>${title}</h3><p>${description}</p></div>${content}</section>`;
  const row = (title, description, control) =>
    `<div class="setting-row"><div><h4>${title}</h4><p>${description}</p></div><div class="setting-control">${control}</div></div>`;
  const savebar = () =>
    `<div class="settings-savebar"><span id="settings-save-state" role="status">${dirty(section) ? "Unsaved changes" : savedNotices[section] || "Changes are saved in this preview only"}</span><div><button type="button" class="quiet small" data-action="settings-discard" ${dirty(section) ? "" : "disabled"}>Discard</button><button type="submit" class="primary" id="settings-save" ${dirty(section) ? "" : "disabled"}>Save changes</button></div></div>`;
  function general() {
    const d = drafts.general;
    return `<form id="settings-form">${group(
      "Workspace",
      "A name and starting page for this research space.",
      row(
        "Workspace name",
        "Shown in navigation and breadcrumbs.",
        `<label class="sr-only" for="settings-name">Workspace name</label><input id="settings-name" data-setting="name" maxlength="40" required value="${esc(d.name)}">`,
      ) +
        row(
          "Start page",
          "Opened when you select the Anvil wordmark.",
          `<label class="sr-only" for="settings-start">Start page</label><select id="settings-start" data-setting="start">${option("bench", "Workbench", d.start)}${option("playground", "Playground", d.start)}${option("work", "Anvil work", d.start)}${option("observability/fleet", "Observability", d.start)}${option("compute", "Compute", d.start)}</select>`,
        ),
    )}
      ${group(
        "Appearance",
        "Keep the instruments readable and the workspace calm.",
        row(
          "Color theme",
          "Shared with the modern Anvil Serving documentation.",
          `<div class="theme-choice"><span class="theme-swatch" aria-hidden="true"></span><span>Anvil Dark<small>Navy · cyan · amber</small></span><span class="theme-selected">Selected</span></div>`,
        ) +
          row(
            "Interface density",
            "Adjust panel spacing across the workspace.",
            `<label class="sr-only" for="settings-density">Interface density</label><select id="settings-density" data-setting="density">${option("comfortable", "Comfortable", d.density)}${option("compact", "Compact", d.density)}</select>`,
          ) +
          row(
            "Operation indicators",
            "Show compute, memory, and activity above your work. Settings keeps this area quiet.",
            `<label class="setting-toggle"><input type="checkbox" role="switch" id="settings-hud" data-setting="showHud" ${d.showHud ? "checked" : ""}><span>Show system HUD</span></label>`,
          ),
      )}${savebar()}</form>`;
  }
  const connections = [
    {
      name: "Anvil Serving",
      mark: "A",
      description: "Models, recipes, fleet & evaluation",
      status: "Sample connection",
      owner: "Serving controller",
      reference: "serving-primary",
      detail:
        "Declared resource owners supply model, recipe, lifecycle and evaluation operations. Provider secrets stay with the service.",
    },
    {
      name: "Anvil State",
      mark: "S",
      description: "PRDs, work packets, claims & evidence",
      status: "Sample connection",
      owner: "Anvil State engine",
      reference: "project-state",
      detail:
        "The exact project checkout resolves its existing state. This connection uses supported reads and mutations; it does not open a state database from the browser.",
    },
    {
      name: "Pi session service",
      mark: "π",
      description: "Conversation, tools & session recovery",
      status: "Not configured",
      owner: "Pi session service",
      reference: "pi-runner",
      detail:
        "Pi Web is the proposed session UI. Its service, identity handoff and isolated environment must pass the integration spike before it serves agent conversations inside Anvil work.",
    },
    {
      name: "Telemetry",
      mark: "⌁",
      description: "Metrics, logs & Grafana context",
      status: "Sample connection",
      owner: "Telemetry services",
      reference: "telemetry-primary",
      detail:
        "Scoped metrics and logs retain source timestamps. Grafana checks its own access session; the workspace opens an authorized location and time range.",
    },
  ];
  function connectionView() {
    return `<div class="settings-connections">${connections.map((c, i) => `<section class="connection-row"><span class="connection-mark" aria-hidden="true">${c.mark}</span><div><h3>${c.name}</h3><p>${c.description}</p><small>${c.owner}</small></div><div class="connection-actions"><span class="pill ${i === 2 ? "warn" : "neutral"}">${c.status}</span><button type="button" class="small quiet" data-action="settings-connection-${i}">Inspect connection</button></div></section>`).join("")}</div><div class="settings-note"><span aria-hidden="true">↳</span><p>Choose models and request presets in Playground. Settings describes the service connection; it does not load or change a running model.</p><a href="#playground" class="text-link">Open playground ↗</a></div>`;
  }
  function environment() {
    const d = drafts.environment;
    return `<div class="environment-profile"><div class="profile-icon">π</div><div><div class="eyebrow">DEFAULT PROFILE</div><h3>Isolated task workspace</h3><p>Per-task checkout · explicit cloud operator connection</p></div><span class="pill warn">Runner not connected</span></div><form id="settings-form">${group(
      "Resource envelope",
      "Defaults for the next session proposal. Existing sessions keep their reviewed settings.",
      row(
        "CPU limit",
        "Leave room for serving and other host workloads.",
        `<label class="sr-only" for="settings-cpus">CPU limit</label><div class="input-unit"><input id="settings-cpus" type="number" data-setting="cpus" min="1" max="16" step="1" required value="${d.cpus}"><span>CPUs</span></div>`,
      ) +
        row(
          "Memory limit",
          "Agent tools and the task environment share this allowance.",
          `<label class="sr-only" for="settings-memory">Memory limit</label><div class="input-unit"><input id="settings-memory" type="number" data-setting="memory" min="1" max="64" step="1" required value="${d.memory}"><span>GiB</span></div>`,
        ) +
        row(
          "Session duration",
          "Reviewed again before the runner starts work.",
          `<label class="sr-only" for="settings-minutes">Session duration</label><div class="input-unit"><input id="settings-minutes" type="number" data-setting="minutes" min="5" max="120" step="5" required value="${d.minutes}"><span>minutes</span></div>`,
        ),
    )}
      <div class="settings-note"><span aria-hidden="true">↳</span><p>The Pi conversation chooses its operator model. Benchmark targets remain separate. Tool grants and network policy come from the approved session plan.</p><a href="#work" class="text-link">Open Anvil work ↗</a></div>${savebar()}</form>`;
  }
  function dataView() {
    return (
      group(
        "Records & ownership",
        "Different kinds of work keep their own source of truth.",
        row(
          "Conversations & presets",
          "Private session content; separate from machine telemetry.",
          '<span class="settings-value">Workbench session store<small>Proposed · page memory in this concept</small></span>',
        ) +
          row(
            "Run artifacts",
            "Retain failed and interrupted outcomes alongside successful runs.",
            '<span class="settings-value">Evaluation & Evidence<small>Owned by Anvil Serving</small></span>',
          ) +
          row(
            "Task evidence links",
            "Run identity, artifact digest, producing authority and review state.",
            '<span class="settings-value">Anvil State<small>Independent acceptance gates</small></span>',
          ) +
          row(
            "Pi session history",
            "Conversation checkpoints, changes and captured tool results.",
            '<span class="settings-value">Pi session service<small>Runner retention policy</small></span>',
          ),
      ) +
      `<div class="settings-note"><span aria-hidden="true">↳</span><p>Retention and export controls will use those owners’ supported policies. This preview keeps changes until reload and has no saved credentials or service connection.</p><a href="#docs" class="text-link">Read the handbook ↗</a></div>`
    );
  }
  function render(current, api) {
    section = sections[current] ? current : "general";
    const active = sections[section];
    const views = {
      general,
      connections: connectionView,
      environment,
      access: () => api.access(true),
      data: dataView,
    };
    return `<div class="settings-heading"><div><div class="eyebrow">YOUR WORKSPACE</div><h1>Settings</h1><p>Make this space work the way you do.</p></div><span class="settings-scope"><span class="avatar">OP</span><span>${esc(saved.general.name)}<small>Personal workspace · demo</small></span></span></div>
      <div class="settings-layout"><aside class="settings-navigation"><div class="eyebrow">WORKSPACE</div><nav aria-label="Settings sections">${Object.entries(
        sections,
      )
        .map(
          ([id, s], i) =>
            `${i === 3 ? '<div class="eyebrow settings-admin-label">ADMINISTRATION</div>' : ""}<a href="#settings/${id}" ${section === id ? 'aria-current="page"' : ""}><span aria-hidden="true">${s.icon}</span><span>${s.label}<small>${s.caption}</small></span><span class="settings-draft-dot" data-dirty="${id}" ${dirty(id) ? "" : "hidden"} aria-label="Unsaved changes"></span></a>`,
        )
        .join(
          "",
        )}</nav><p class="settings-rail-note">Drafts stay here as you move between sections.</p></aside>
      <div class="settings-content"><div class="settings-section-heading"><div><h2 tabindex="-1">${active.label}</h2><p>${active.description}</p></div>${section === "access" ? '<span class="pill neutral">Administrator</span>' : ""}</div>${views[section]()}</div></div>`;
  }
  function input(event) {
    const el = event.target,
      key = el.dataset.setting;
    if (!key || !drafts[section]) return false;
    drafts[section][key] =
      el.type === "checkbox"
        ? el.checked
        : el.type === "number"
          ? Number(el.value)
          : el.value;
    const changed = dirty(section);
    document
      .querySelectorAll('#settings-save,[data-action="settings-discard"]')
      .forEach((b) => (b.disabled = !changed));
    document.querySelector("#settings-save-state").textContent = changed
      ? "Unsaved changes"
      : "No changes to save";
    const dot = document.querySelector(`[data-dirty="${section}"]`);
    if (dot) dot.hidden = !changed;
    return true;
  }
  function action(name, api) {
    if (name === "settings-discard") {
      drafts[section] = structuredClone(saved[section]);
      savedNotices[section] = "Changes discarded";
      api.render();
      api.announce(savedNotices[section]);
      document
        .querySelector(".settings-section-heading h2")
        ?.focus({ preventScroll: true });
      return true;
    }
    if (name.startsWith("settings-connection-")) {
      const c = connections[Number(name.slice("settings-connection-".length))];
      api.openDialog(
        c.name,
        `${api.kv([
          ["Owner", c.owner],
          ["Reference", c.reference],
          ["State", c.status],
        ])}<p class="section-copy" style="margin-top:16px">${c.detail}</p><div class="notice">Connection configuration and testing require the live owner adapter. This concept does not contact a service.</div>`,
        api.btn("Close", "close"),
      );
      return true;
    }
    return false;
  }
  function submit(event, api) {
    if (event.target.id !== "settings-form") return false;
    event.preventDefault();
    if (section === "general") {
      drafts.general.name = drafts.general.name.trim();
      if (!drafts.general.name) {
        const el = document.querySelector("#settings-name");
        el.setCustomValidity("Enter a workspace name.");
        el.reportValidity();
        el.setCustomValidity("");
        return true;
      }
    }
    saved[section] = structuredClone(drafts[section]);
    savedNotices[section] = "Saved in this preview · resets on reload";
    api.render();
    api.announce(savedNotices[section]);
    document
      .querySelector(".settings-section-heading h2")
      ?.focus({ preventScroll: true });
    return true;
  }
  return {
    render,
    input,
    action,
    submit,
    preferences: () => ({ ...saved.general }),
    environment: () => ({ ...saved.environment }),
  };
})();
