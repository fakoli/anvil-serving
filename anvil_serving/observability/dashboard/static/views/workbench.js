import {
  badge,
  button,
  el,
  empty,
  heading,
  kv,
  notice,
  route,
  table,
  timestamp,
} from "./common.js";
import { request } from "./api.js";
import { evidenceDialog, openOperation } from "./operations.js";
import { experimentsView } from "./experiments.js";

const tabs = [
  ["overview", "Overview"],
  ["run", "Run flow"],
  ["compare", "Compare"],
  ["evidence", "Evidence"],
  ["events", "Events"],
  ["runs", "All runs"],
];
export const workbenchRoute = (tab, id) =>
  id ? route("workbench", id, tab) : route("workbench", tab);

function runTable(runs, ctx) {
  return table(
    ["Run", "Outcome", "Verification", "Updated", ""],
    runs.map((run) => [
      el(
        "div",
        {},
        el("strong", { text: run.label || run.action_id || run.id }),
        el("small", { class: "mono", text: run.resource_id || run.id }),
      ),
      badge(run.execution_outcome || run.status),
      badge(run.verification?.status),
      timestamp(run.updated_at, ctx.zone),
      el("a", {
        class: "text-link",
        href: workbenchRoute("events", run.id),
        text: "Open run →",
      }),
    ]),
  );
}

export async function workbenchView(ctx, id, requestedTab = "overview") {
  const tab = tabs.some(([name]) => name === requestedTab)
    ? requestedTab
    : "overview";
  const root = el("div", {
    class: "workbench-page workbench-flow stack",
    "data-story": "US-BENCH-01",
  });
  let data;
  try {
    data =
      ctx.operations || (await request("operations", { signal: ctx.signal }));
  } catch (error) {
    data = { items: [], error: error.message };
  }
  const runs = data.items || [];
  const current = runs.find((run) => run.id === id) || runs[0];
  root.append(
    el("span", { class: "eyebrow", text: "WORKBENCH" }),
    heading(
      "A place for the whole experiment.",
      "Plan the test, follow the run, and keep its evidence together.",
      [
        el("a", {
          class: "quiet-button button-link",
          href: route("playground"),
          text: "Open playground",
        }),
        el("a", {
          class: "primary button-link",
          href: workbenchRoute("run"),
          text: "+ New experiment",
        }),
      ],
    ),
  );
  if (current) {
    root.append(
      el(
        "section",
        { class: "focus-card", "aria-label": "Selected run" },
        el("span", {
          class: "eyebrow",
          text: `RETAINED RUN / ${current.id.slice(0, 16)}`,
        }),
        el(
          "div",
          { class: "focus-top" },
          el(
            "div",
            {},
            el("h2", {
              text: current.label || current.action_id || "Selected operation",
            }),
            el("p", {
              text:
                [current.resource_id, current.host_id]
                  .filter(Boolean)
                  .join(" · ") ||
                "Exact target retained by the operation owner",
            }),
          ),
          badge(current.status),
        ),
        el("p", {
          class: "meta",
          text: "This run retains its original target. Compute selection scopes new work and current instruments.",
        }),
      ),
    );
  }
  const tablist = el(
    "nav",
    {
      class: "local-tabs",
      role: "tablist",
      "aria-label": "Workbench run sections",
    },
    ...tabs.map(([name, label]) =>
      el("a", {
        id: `workbench-tab-${name}`,
        role: "tab",
        "aria-selected": name === tab ? "true" : "false",
        "aria-controls": "workbench-panel",
        tabindex: name === tab ? "0" : "-1",
        href: workbenchRoute(name, current?.id),
        text: label,
      }),
    ),
  );
  tablist.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const links = [...tablist.querySelectorAll("[role=tab]")];
    const index = links.indexOf(event.target.closest("[role=tab]"));
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? tabs.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
            tabs.length;
    links[next].focus();
  });
  root.append(tablist);
  const panel = el("section", {
    id: "workbench-panel",
    role: "tabpanel",
    "aria-labelledby": `workbench-tab-${tab}`,
    class: "stack",
  });
  root.append(panel);
  if (tab === "run") {
    panel.append(await experimentsView(ctx));
    return root;
  }
  if (data.error) panel.append(notice(data.error, "warning"));
  if (!current) {
    panel.append(
      el(
        "section",
        { class: "panel empty-run" },
        el("span", { class: "eyebrow", text: "READY WHEN YOU ARE" }),
        el("h2", { text: "Your next experiment starts here." }),
        el("p", {
          class: "muted",
          text: "Run a declared check to retain its exact target, outcome, and evidence.",
        }),
        el("a", {
          class: "primary button-link",
          href: workbenchRoute("run"),
          text: "Choose a declared test",
        }),
      ),
    );
    return root;
  }
  if (tab === "overview") {
    const active = runs.filter(
      (run) =>
        !["succeeded", "failed", "cancelled", "completed"].includes(run.status),
    );
    const verified = runs.filter(
      (run) => run.verification?.status === "passed",
    );
    const values = [
      ["Retained runs", runs.length, "Within your access scope"],
      ["Active operations", active.length, "Owner-reported lifecycle"],
      ["Verified outcomes", verified.length, "Independent owner checks"],
      [
        "Evidence references",
        runs.filter((run) => run.evidence_id).length,
        "Retained with their original target",
      ],
    ];
    panel.append(
      el(
        "div",
        { class: "run-metrics" },
        ...values.map(([label, value, note]) =>
          el(
            "div",
            { class: "run-metric" },
            el("span", { class: "metric-label", text: label }),
            el("strong", { class: "metric-value", text: value }),
            el("small", { text: note }),
          ),
        ),
      ),
      el(
        "section",
        { class: "panel stack" },
        el(
          "div",
          { class: "section-heading" },
          el("h2", { text: "Recent runs" }),
          el("a", {
            class: "text-link",
            href: workbenchRoute("runs", current.id),
            text: "All retained runs →",
          }),
        ),
        runTable(runs.slice(0, 8), ctx),
      ),
    );
  } else if (tab === "compare") {
    panel.append(
      el(
        "section",
        { class: "panel stack" },
        el("h2", { text: "Compare retained outcomes" }),
        el("p", {
          class: "muted",
          text: "Operation outcomes are shown as recorded. Open the linked evidence to compare exact model, hardware, and request dimensions.",
        }),
        runTable(runs.slice(0, 20), ctx),
      ),
    );
  } else if (tab === "evidence") {
    panel.append(
      el(
        "section",
        { class: "panel stack" },
        el("h2", { text: "Evidence" }),
        el("p", {
          class: "muted",
          text: "Retained evidence describes this operation's original target and checks.",
        }),
        current.evidence_id
          ? button(
              "Open retained evidence",
              () => evidenceDialog(current.evidence_id, ctx),
              "primary",
            )
          : empty("This operation has no retained evidence reference."),
      ),
    );
  } else if (tab === "events") {
    panel.append(
      el(
        "section",
        { class: "panel stack" },
        el("h2", { text: current.label || "Operation events" }),
        kv([
          ["Status", badge(current.status)],
          ["Native state", current.native_state || "Not reported"],
          ["Verification", badge(current.verification?.status)],
          ["Updated", timestamp(current.updated_at, ctx.zone)],
        ]),
        button(
          "Open operation detail",
          () => openOperation(current.id, ctx),
          "primary",
        ),
      ),
    );
  } else panel.append(el("section", { class: "panel" }, runTable(runs, ctx)));
  return root;
}
