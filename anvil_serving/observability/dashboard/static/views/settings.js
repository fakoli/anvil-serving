import { button, el, field, heading, notice, select } from "./common.js";
import { workbenchRequest } from "./api.js";
import { connectAccessView } from "./connect_access.js";

const tabs = [
  ["general", "General"],
  ["connections", "Connections"],
  ["pi", "Pi environment"],
  ["access", "Access"],
  ["data", "Data"],
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

export async function workbenchSettingsView(ctx, requestedTab = "general") {
  const tab = tabs.some(([id]) => id === requestedTab)
    ? requestedTab
    : "general";
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
  const draft = drafts.get(owner) || {
    ...normalized,
    landing: normalized.landing === "workbench" ? "bench" : normalized.landing,
  };
  drafts.set(owner, draft);
  const root = el(
    "div",
    {
      class: "workbench-page settings-page stack",
      "data-story": "US-SETTINGS-01",
    },
    heading(
      "Make this workspace yours.",
      "Defaults for your next session. Connections and permissions stay with their owners.",
    ),
  );
  const rail = el(
    "nav",
    { class: "settings-rail", "aria-label": "Settings sections" },
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
      ctx.applyPreferences?.(result);
      feedback.replaceChildren(notice(label, "success"));
      ctx.announce(label);
    } catch (error) {
      feedback.replaceChildren(notice(error.message, "danger"));
    }
  };
  if (tab === "general") {
    content.append(
      el(
        "div",
        { class: "section-intro" },
        el("h2", { text: "General" }),
        el("p", {
          class: "muted",
          text: "Your preferred starting point, density, and time context.",
        }),
      ),
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
            [
              ["comfortable", "Comfortable"],
              ["compact", "Compact"],
            ],
            draft.density,
            (event) => {
              draft.density = event.target.value;
            },
          ),
        ),
        field(
          "Time zone",
          select(
            [
              ["UTC", "UTC"],
              ["America/Los_Angeles", "America / Los Angeles"],
            ],
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
      el(
        "div",
        { class: "actions" },
        button(
          "Save preferences",
          () => save("Workspace preferences saved."),
          "primary",
        ),
      ),
      feedback,
    );
  } else if (tab === "connections") {
    content.append(
      el("h2", { text: "Connections" }),
      el("p", {
        class: "muted",
        text: "Declared model connections available to this account.",
      }),
      ...(catalog.connectors || []).map((connection) =>
        el(
          "article",
          { class: "panel stack" },
          el("h3", { text: connection.label }),
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
        : null,
    );
  } else if (tab === "pi") {
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
    const thinking = select(levels, draft.pi_thinking, (event) => {
      draft.pi_thinking = event.target.value;
    });
    content.append(
      el("h2", { text: "Pi environment" }),
      el("p", {
        class: "muted",
        text: "Defaults for a new task conversation. Existing sessions keep their explicit target.",
      }),
      el(
        "div",
        { class: "settings-fields" },
        field("Provider", provider),
        field("Model", model),
        field("Thinking", thinking),
      ),
      button(
        "Save Pi defaults",
        () => save("Pi defaults saved."),
        "primary",
        !providers.length,
      ),
      feedback,
      !catalog.pi?.configured
        ? notice(
            "Pi execution is not configured. Declared choices do not establish runner readiness.",
            "warning",
          )
        : null,
    );
  } else if (tab === "access") {
    content.append(await connectAccessView(ctx));
  } else {
    content.append(
      el("h2", { text: "Private data" }),
      el(
        "section",
        { class: "panel stack" },
        el("p", {
          text: catalog.retention_days
            ? `Conversation retention: ${catalog.retention_days} days.`
            : "Retention is controlled by the private workspace configuration.",
        }),
        el("p", {
          class: "muted",
          text: "Manage retained conversations in Playground and task history in Anvil work.",
        }),
        el("a", {
          class: "text-link",
          href: "#/playground",
          text: "Open conversation history →",
        }),
      ),
    );
  }
  root.append(el("div", { class: "settings-layout" }, rail, content));
  return root;
}
export const settingsView = workbenchSettingsView;
window.addEventListener("observatory-session-changed", () => {
  drafts = new Map();
});
