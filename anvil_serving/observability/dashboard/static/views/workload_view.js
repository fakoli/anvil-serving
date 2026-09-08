import {
  el,
  list,
  heading,
  field,
  select,
  button,
  notice,
  empty,
  badge,
  kv,
  timestamp,
  show,
} from "./common.js";
import { request, query } from "./api.js";
import { validateSnapshot } from "./workload_schema.js";
import { metadataDialog } from "./operations.js";
const filters = {
  owner: "",
  kind: "",
  state: "",
  active_only: "false",
  recent_seconds: "3600",
  limit: "200",
};
const owners = [
  "router",
  "controller",
  "benchmark",
  "media",
  "recipe",
  "manifest",
];
const kinds = [
  "router-request",
  "controller-operation",
  "benchmark-job",
  "media-job",
  "recipe-serve",
];
const states = [
  "checking",
  "admitted",
  "dispatched",
  "streaming",
  "queued",
  "running",
  "terminal",
  "configured",
  "absent",
  "unavailable",
  "unsupported",
];
const omitted = (t) =>
  `${t.returned} returned · ${t.omitted === null ? "unknown omission count" : `${t.omitted} omitted`}`;
export async function workloadsView(ctx) {
  const params = { ...filters };
  if (ctx.host) params.host = ctx.host;
  let data = await request(query("workloads", params), { signal: ctx.signal });
  try {
    data = validateSnapshot({ ok: true, data });
  } catch {
    throw new Error(
      "Invalid canonical workload evidence. No workload state can be established.",
    );
  }
  const filterBar = el("div", { class: "filters" });
  for (const [key, label, options] of [
    ["owner", "Owner", [["", "All owners"], ...owners]],
    ["kind", "Kind", [["", "All kinds"], ...kinds]],
    ["state", "State", [["", "All states"], ...states]],
    [
      "active_only",
      "Activity",
      [
        ["false", "All observations"],
        ["true", "Active only"],
      ],
    ],
    [
      "recent_seconds",
      "Recent window",
      [
        ["300", "5 minutes"],
        ["3600", "1 hour"],
        ["86400", "24 hours"],
      ],
    ],
    [
      "limit",
      "Result limit",
      [
        ["50", "50 records"],
        ["100", "100 records"],
        ["200", "200 records"],
      ],
    ],
  ])
    filterBar.append(
      field(
        label,
        select(options, filters[key], (event) => {
          filters[key] = event.target.value;
          ctx.refresh();
        }),
      ),
    );
  const content = el("div", { class: "stack" });
  for (const node of data.nodes) {
    const sources = el("div", { class: "stack" });
    for (const source of node.sources) {
      const cards = source.records.map((record) => {
        const progress = record.progress;
        return el(
          "article",
          { class: "panel" },
          el(
            "div",
            { class: "card-head" },
            el(
              "div",
              {},
              button(
                record.label,
                () =>
                  metadataDialog(record.label, [
                    ["ID", record.id],
                    ["Host", record.host],
                    ["Owner", record.owner],
                    ["Kind", record.kind],
                    ["State", record.state],
                    ["Phase", record.phase],
                    ["Outcome", record.outcome],
                    ["Source authority", record.source_authority],
                    ["Observation quality", record.observation_quality],
                    ["Created", timestamp(record.created_at, ctx.zone)],
                    ["Updated", timestamp(record.updated_at, ctx.zone)],
                    [
                      "Source time",
                      timestamp(record.source_timestamp, ctx.zone),
                    ],
                  ]),
                "text-link-button",
              ),
              el("p", {
                class: "meta",
                text: `${record.owner} · ${record.kind}`,
              }),
            ),
            badge(record.observation_quality),
          ),
          kv([
            ["Current phase", `${record.state} / ${record.phase}`],
            ["Outcome", record.outcome],
            ["Source update", timestamp(record.source_timestamp, ctx.zone)],
            [
              "Progress",
              progress
                ? `${progress.completed} / ${progress.total ?? "unknown"} ${progress.unit}`
                : `Not reported · ${record.phase}`,
            ],
          ]),
          progress && progress.total > 0
            ? el("progress", {
                max: progress.total,
                value: progress.completed,
                "aria-label": `${record.label} progress`,
              })
            : null,
        );
      });
      sources.append(
        el(
          "section",
          { class: "stack" },
          el(
            "div",
            { class: "card-head" },
            el(
              "div",
              {},
              el("h3", { text: `${source.owner} · ${source.status}` }),
              el("p", { class: "meta", text: omitted(source.truncation) }),
            ),
            timestamp(source.collection_timestamp, ctx.zone),
          ),
          source.error
            ? notice(`Source evidence unavailable: ${source.error}`, "warning")
            : null,
          cards.length
            ? el("div", { class: "grid two" }, cards)
            : empty(
                source.status === "complete"
                  ? "No matching workloads reported."
                  : "Incomplete source evidence; no matching records reported.",
              ),
        ),
      );
    }
    content.append(
      el(
        "section",
        { class: "stack" },
        el(
          "div",
          { class: "section-heading" },
          el("h2", { text: node.host }),
          badge(node.status),
        ),
        sources,
      ),
    );
  }
  return el(
    "div",
    {},
    heading(
      "Workloads",
      "Canonical owner evidence. Workload visibility grants no generic start, stop, or cancel authority.",
      [button("Refresh", ctx.refresh, "quiet-button")],
    ),
    filterBar,
    notice(
      `Fleet ${data.status} · ${omitted(data.truncation)}. Collected ${data.collection_timestamp}.`,
      data.status === "complete" ? "" : "warning",
    ),
    filters.active_only === "true"
      ? notice(
          "Active-only excludes stale observations. An empty result does not prove no work is running.",
          "warning",
        )
      : null,
    content.childElementCount
      ? content
      : empty(
          data.status === "complete"
            ? "No matching workloads reported."
            : "Workload evidence unavailable or incomplete.",
        ),
  );
}
