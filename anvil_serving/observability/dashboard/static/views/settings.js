import { button, el, field, heading, notice, select } from "./common.js";
import { workbenchRequest } from "./api.js";
import { jevAssistanceView } from "./jev.js";
const tabs = [
  ["thread", "This thread"],
  ["project", "Project defaults"],
  ["service", "Pi service"],
  ["jev", "Jev assistance"],
];
const workspaces = [
  ["bench", "Workbench"],
  ["playground", "Playground"],
  ["models", "Models & recipes"],
  ["work", "Anvil work"],
  ["observability", "Observability"],
  ["compute", "Compute"],
];
let drafts = new Map();
let snapshots = new Map();
let projectDrafts = new Map();
let projectSnapshots = new Map();

export const pages = [
  "overview",
  "workstations",
  "serves",
  "workloads",
  "logs",
  "configuration",
  "experiments",
  "operations",
  "settings",
];
export function loadPreferences(saved = {}) {
  const landing = saved.landing === "bench" ? "workbench" : saved.landing;
  return {
    ...saved,
    landing: ["workbench", ...workspaces.map(([id]) => id)].includes(landing)
      ? landing
      : "workbench",
    density: saved.density === "compact" ? "compact" : "comfortable",
    zone: saved.zone === "America/Los_Angeles" ? saved.zone : "UTC",
    range: ["15m", "1h", "6h", "24h", "7d"].includes(saved.range)
      ? saved.range
      : "1h",
  };
}

export function clearDrafts() {
  drafts = new Map();
  snapshots = new Map();
  projectDrafts = new Map();
  projectSnapshots = new Map();
}

export function hasDirtyDraft() {
  return (
    [...drafts].some(
      ([owner, draft]) => snapshots.get(owner) !== JSON.stringify(draft),
    ) ||
    [...projectDrafts].some(
      ([key, draft]) => projectSnapshots.get(key) !== JSON.stringify(draft),
    )
  );
}

const append = (node, ...children) =>
  node.append(...children.flat(Infinity).filter((child) => child != null));
const replace = (node, ...children) =>
  node.replaceChildren(...children.flat(Infinity).filter((child) => child != null));

export async function workbenchSettingsView(ctx, requestedTab = "thread") {
  const legacyTab = {
    general: "service",
    connections: "service",
    data: "service",
    pi: "service",
    projects: "project",
  };
  requestedTab = legacyTab[requestedTab] || requestedTab;
  const tab = tabs.some(([id]) => id === requestedTab)
    ? requestedTab
    : "thread";
  const owner = ctx.session?.csrf_token || "current-session";
  let saved, catalog;
  try {
    [saved, catalog] = await Promise.all([
      workbenchRequest("preferences", { signal: ctx.signal }),
      workbenchRequest("catalog", { signal: ctx.signal }),
    ]);
  } catch (error) {
    return el(
      "div",
      { class: "workbench-page stack" },
      heading("Settings"),
      notice(error.message, "danger"),
    );
  }
  const normalized = loadPreferences(saved);
  let draft = drafts.get(owner);
  if (!draft) {
    draft = {
      ...normalized,
      landing: normalized.landing === "workbench" ? "bench" : normalized.landing,
    };
    const providers = Object.keys(catalog.pi?.models || {});
    draft.pi_provider = providers.includes(draft.pi_provider)
      ? draft.pi_provider
      : providers[0] || "";
    const models = catalog.pi?.models?.[draft.pi_provider] || [];
    draft.pi_model = models.includes(draft.pi_model)
      ? draft.pi_model
      : models[0] || "";
    const levels = catalog.pi?.thinking || [];
    draft.pi_thinking = levels.includes(draft.pi_thinking)
      ? draft.pi_thinking
      : levels[0] || "";
    drafts.set(owner, draft);
    snapshots.set(owner, JSON.stringify(draft));
  }
  const root = el(
    "div",
    {
      class: "workbench-page settings-page stack",
      "data-story": "US-SETTINGS-01",
    },
    heading(
      "Settings",
      "Choose defaults with the owner that applies them. Existing sessions keep their explicit bindings.",
    ),
  );
  const rail = el(
    "nav",
    { class: "settings-navigation", "aria-label": "Settings scope" },
    ...tabs.map(([id, label]) =>
      el("a", {
        href: `#/settings/${id}`,
        "aria-current": tab === id ? "page" : null,
        text: label,
      }),
    ),
  );
  const content = el("section", {
    class: "settings-content stack",
    "aria-label": tabs.find(([id]) => id === tab)[1],
  });
  const feedback = el("div", {
    class: "settings-feedback",
    role: "status",
    "aria-live": "polite",
  });
  const save = async (label) => {
    feedback.replaceChildren();
    try {
      const result = await workbenchRequest("preferences", {
        method: "POST",
        body: draft,
        signal: ctx.signal,
      });
      snapshots.set(owner, JSON.stringify(draft));
      ctx.applyPreferences?.(result);
      feedback.replaceChildren(notice(label, "success"));
      ctx.announce(label);
    } catch (error) {
      feedback.replaceChildren(notice(error.message, "danger"));
    }
  };
  if (tab === "jev") {
    content.append(jevAssistanceView(ctx, catalog));
  } else if (tab === "thread") {
    append(
      content,
      el("h2", { text: "This thread" }),
      notice(
        "The current Pi owner does not expose mutable per-thread settings here. Existing conversations retain their declared model and thinking target.",
      ),
      el("p", {
        class: "muted",
        text: "Use Pi service for defaults that apply when you start a new task conversation.",
      }),
      el("a", {
        class: "text-link",
        href: "#/playground",
        text: "Open a conversation →",
      }),
    );
  } else if (tab === "project") {
    append(
      content,
      el("h2", { text: "Project defaults" }),
      notice(
        "These defaults apply only to new isolated task sessions. Existing sessions retain their frozen roots and permissions.",
      ),
    );
    const projects = catalog.projects || [];
    const editor = el("div", { class: "stack" });
    let generation = 0;
    const load = async projectId => {
      const current = ++generation;
      editor.replaceChildren(notice("Loading declared roots…"));
      try {
        const data = await workbenchRequest(`projects/${encodeURIComponent(projectId)}/preferences`, { signal: ctx.signal });
        if (ctx.signal.aborted || current !== generation) return;
        const projectKey = `${owner}:${projectId}`;
        let rootDraft = projectDrafts.get(projectKey);
        if (!rootDraft) {
          rootDraft = {
            primary_root_id: data.defaults.primary_root_id,
            writable_root_ids: [...data.defaults.writable_root_ids],
          };
          projectDrafts.set(projectKey, rootDraft);
          projectSnapshots.set(projectKey, JSON.stringify(rootDraft));
        }
        const writable = () => new Set(rootDraft.writable_root_ids);
        const checks = data.roots.map(row => ({ row, input: el("input", {
          type: "checkbox",
          checked: row.state_root || writable().has(row.id),
          disabled: row.state_root || row.task_access !== "read-write",
          onChange: event => {
            const selected = new Set(rootDraft.writable_root_ids);
            if (event.target.checked) selected.add(row.id); else selected.delete(row.id);
            rootDraft.writable_root_ids = [...selected];
          },
        }) }));
        const primary = select(data.roots.filter(row => row.task_access === "read-write").map(row => [row.id, row.label]), rootDraft.primary_root_id,
          event => {
            rootDraft.primary_root_id = event.target.value;
            const selected = checks.find(item => item.row.id === event.target.value);
            if (selected) {
              selected.input.checked = true;
              rootDraft.writable_root_ids = [...new Set([...rootDraft.writable_root_ids, event.target.value])];
            }
          });
        const outcome = el("div", { role: "status" });
        const saveRoots = button("Save project defaults", async () => {
          saveRoots.disabled = true;
          try {
            rootDraft.writable_root_ids = checks
              .filter(
                (item) =>
                  item.input.checked &&
                  !item.row.state_root &&
                  item.row.task_access === "read-write",
              )
              .map((item) => item.row.id);
            await workbenchRequest(`projects/${encodeURIComponent(projectId)}/preferences`, { method: "POST", signal: ctx.signal,
              body: rootDraft });
            projectSnapshots.set(projectKey, JSON.stringify(rootDraft));
            outcome.replaceChildren(notice("Project defaults saved for new sessions.", "success"));
          } catch (error) { outcome.replaceChildren(notice(error.message, "danger")); }
          finally { saveRoots.disabled = false; }
        }, "primary");
        replace(editor, data.stale ? notice("Declared roots changed. Review and save these updated defaults before starting a new task session.", "warning") : undefined,
          field("Primary writable directory", primary), el("h3", { text: "Writable task roots" }),
          ...checks.map(({ row, input }) => field(row.label, input, row.state_root ? "Canonical task root — included in every managed task claim." : row.task_access === "read-write" ? "Select to request write access for new task sessions." : "Read-only context.")),
          notice("Owner claims and isolation are verified when a task starts. Host Pi sessions use operator account access; these task permissions do not sandbox host tools."), saveRoots, outcome);
      } catch (error) { if (current === generation) editor.replaceChildren(notice(error.message, "danger")); }
    };
    if (projects.length) {
      append(content,field("Project", select(projects.map(row => [row.id, row.label]), projects[0].id, event => void load(event.target.value))), editor);
      await load(projects[0].id);
    } else append(content,notice("No project roots are declared for this account."));
  } else if (tab === "service") {
    const wasClean = snapshots.get(owner) === JSON.stringify(draft);
    const providers = Object.keys(catalog.pi?.models || {});
    draft.pi_provider = providers.includes(draft.pi_provider)
      ? draft.pi_provider
      : providers[0] || "";
    const model = select([], "");
    const updateModels = () => {
      const choices = catalog.pi?.models?.[draft.pi_provider] || [];
      draft.pi_model = choices.includes(draft.pi_model)
        ? draft.pi_model
        : choices[0] || "";
      model.replaceChildren(
        ...choices.map((id) => el("option", { value: id, text: id })),
      );
      model.value = draft.pi_model;
    };
    const provider = select(providers, draft.pi_provider, (event) => {
      draft.pi_provider = event.target.value;
      updateModels();
    });
    model.addEventListener("change", (event) => {
      draft.pi_model = event.target.value;
    });
    updateModels();
    const levels = catalog.pi?.thinking || [];
    draft.pi_thinking = levels.includes(draft.pi_thinking)
      ? draft.pi_thinking
      : levels[0] || "";
    if (wasClean) snapshots.set(owner, JSON.stringify(draft));
    const thinking = select(levels, draft.pi_thinking, (event) => {
      draft.pi_thinking = event.target.value;
    });
    append(content,
      el("h2", { text: "Pi service" }),
      el("p", {
        class: "muted",
        text: "The Pi owner declares available choices. New conversation defaults never retarget an existing session.",
      }),
      el("h3", { text: "Workspace preferences" }),
      el(
        "div",
        { class: "settings-fields" },
        field(
          "Default workspace",
          select(workspaces, draft.landing, (event) => {
            draft.landing = event.target.value;
          }),
        ),
        field(
          "Density",
          select(
            [["comfortable", "Comfortable"], ["compact", "Compact"]],
            draft.density,
            (event) => {
              draft.density = event.target.value;
            },
          ),
        ),
        field(
          "Time zone",
          select(
            [["UTC", "UTC"], ["America/Los_Angeles", "America / Los Angeles"]],
            draft.zone,
            (event) => {
              draft.zone = event.target.value;
            },
          ),
        ),
        field(
          "Default range",
          select(["15m", "1h", "6h", "24h", "7d"], draft.range, (event) => {
            draft.range = event.target.value;
          }),
        ),
      ),
      el("h3", { text: "New conversation defaults" }),
      el(
        "div",
        { class: "settings-fields" },
        field("Provider", provider),
        field("Model", model),
        field("Thinking", thinking),
      ),
      button(
        "Save service defaults",
        () => save("Workspace and new conversation defaults saved."),
        "primary",
        !providers.length,
      ),
      feedback,
      el("h3", { text: "Connections" }),
      ...(catalog.connectors || []).map((connection) =>
        el(
          "article",
          { class: "connection-row" },
          el("h4", { text: connection.label }),
          el("p", {
            class: "meta",
            text: `${connection.configured ? "Configured" : "Credential unavailable"} · ${connection.permitted ? "Requests permitted" : "Read only"}`,
          }),
          el("p", {
            text: (connection.models || []).join(" · ") || "No declared models",
          }),
        ),
      ),
      !(catalog.connectors || []).length
        ? notice("No model connections are declared for this account.")
        : undefined,
      el("p", {
        class: "muted",
        text: catalog.retention_days
          ? `Conversation retention: ${catalog.retention_days} days.`
          : "Retention is controlled by the private workspace configuration.",
      }),
      !catalog.pi?.configured
        ? notice(
            "Pi execution is not configured. Declared choices do not establish runner readiness.",
            "warning",
          )
        : undefined,
    );
  }
  append(root, el("div", { class: "settings-layout" }, rail, content));
  return root;
}
export const settingsView = workbenchSettingsView;
window.addEventListener("observatory-session-changed", clearDrafts);
window.addEventListener("beforeunload", (event) => {
  if (hasDirtyDraft()) {
    event.preventDefault();
    event.returnValue = "";
  }
});
