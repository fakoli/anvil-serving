import {
  el,
  list,
  heading,
  section,
  button,
  badge,
  kv,
  metric,
  empty,
  notice,
  field,
  select,
  copy,
  route,
  timestamp,
  show,
  jsonDetails,
} from "./common.js";
import { request, query } from "./api.js";
import { chartPanel } from "./charts.js";
import { actionButtons, operationRows, evidenceDialog } from "./operations.js";
export function gpuCard(gpu, ctx) {
  return el(
    "div",
    { class: "gpu" },
    el(
      "div",
      { class: "gpu-head" },
      el("strong", { text: gpu.label || gpu.id }),
      el("span", { class: "meta", text: gpu.role || "Role not reported" }),
    ),
    el("p", {
      class: "gpu-reading",
      text: `${metric(gpu.memory_used)} / ${metric(gpu.memory_total)}`,
    }),
    el("p", { class: "meta", text: `Utilization ${metric(gpu.utilization)}` }),
    el("p", {
      class: "meta",
      text: `Owner: ${list(gpu.owners).join(", ") || "No observed assignment"}`,
    }),
    el(
      "p",
      { class: "meta" },
      "Source ",
      timestamp(gpu.memory_used?.source_timestamp, ctx.zone),
    ),
  );
}
export function hostCard(host, ctx) {
  const serves = list(ctx.fleet.serves).filter((s) => s.host_id === host.id);
  return el(
    "article",
    { class: "card" },
    el(
      "div",
      { class: "card-head" },
      el(
        "div",
        {},
        el("a", {
          class: "card-title",
          href: route("workstations", host.id),
          text: host.display_name || host.id,
        }),
        el("p", {
          class: "meta",
          text: `${host.platform || "Platform unknown"} · ${host.mode || "Mode not reported"}`,
        }),
      ),
      badge(host.controller?.status),
    ),
    el(
      "div",
      { class: "status-row" },
      el("span", { class: "meta", text: "Controller" }),
      badge(host.controller?.status),
      el("span", { class: "meta", text: "Telemetry" }),
      badge(host.telemetry?.status),
    ),
    kv([
      ["Ownership", host.ownership_status],
      ["Declared serves", serves.length],
      [
        "Maintenance",
        host.maintenance === undefined || host.maintenance === null
          ? "Not reported"
          : typeof host.maintenance === "boolean"
            ? host.maintenance
              ? "Enabled"
              : "Disabled"
            : host.maintenance,
      ],
    ]),
    el(
      "div",
      { class: "gpu-list" },
      list(host.gpus).map((gpu) => gpuCard(gpu, ctx)),
    ),
    list(host.gpus).length
      ? null
      : el("p", { class: "meta", text: "No GPU observations reported." }),
    el(
      "p",
      { class: "freshness" },
      "Controller observed ",
      timestamp(host.controller?.observed_at, ctx.zone),
    ),
  );
}
export async function overviewView(ctx) {
  const hosts = list(ctx.fleet.hosts).filter(
      (h) => !ctx.host || h.id === ctx.host,
    ),
    serves = list(ctx.fleet.serves).filter(
      (s) =>
        (!ctx.host || s.host_id === ctx.host) &&
        (!ctx.serve || s.id === ctx.serve),
    );
  const coverage = ctx.fleet.coverage || {};
  const opsSlot = el(
    "div",
    { class: "panel" },
    el("p", { class: "meta", text: "Loading current operation attention…" }),
  );
  ctx.jobs.push(async () => {
    try {
      const ops = await request("operations", { signal: ctx.signal });
      if (ctx.signal.aborted) return;
      const active = list(ops.items)
        .filter(
          (op) =>
            (!ctx.host || op.host_id === ctx.host) &&
            (!ctx.serve || op.resource_id === ctx.serve) &&
            !["succeeded", "completed", "cancelled"].includes(op.status),
        )
        .slice(0, 8);
      opsSlot.replaceChildren(operationRows(active, ctx));
    } catch (error) {
      if (error.name !== "AbortError")
        opsSlot.replaceChildren(notice(error.message, "warning"));
    }
  });
  const stat = (label, value, note) =>
    el(
      "div",
      { class: "card stat" },
      el("p", { class: "stat-label", text: label }),
      el("p", { class: "stat-value", text: value }),
      el("p", { class: "stat-note", text: note }),
    );
  return el(
    "div",
    {},
    heading(
      "Fleet overview",
      "Current owner state, physical GPU assignments, and historical serving evidence.",
    ),
    coverage.status !== "complete"
      ? notice(
          `Coverage ${coverage.status || "unknown"}. Counts describe reported resources; unavailable sources may hide activity.`,
          "warning",
        )
      : null,
    el(
      "div",
      { class: "grid four" },
      stat(
        "Controllers reporting",
        hosts.filter((h) =>
          ["fresh", "complete", "healthy", "available"].includes(
            h.controller?.status,
          ),
        ).length,
        `${hosts.length} declared · count of confirmed owner observations`,
      ),
      stat(
        "Ready serves confirmed",
        serves.filter((s) => s.readiness === true || s.readiness === "ready")
          .length,
        `${serves.length} declared · ${serves.filter((s) => [null, undefined, "unknown", "unavailable"].includes(s.readiness)).length} not established`,
      ),
      stat(
        "Physical GPUs observed",
        hosts.reduce(
          (n, h) =>
            n +
            list(h.gpus).filter(
              (g) =>
                g.memory_used?.status === "fresh" ||
                g.utilization?.status === "fresh",
            ).length,
          0,
        ),
        "Each device shown independently",
      ),
      stat(
        "Source coverage",
        coverage.status || "Unknown",
        "Current collection, not chart range",
      ),
    ),
    section("Current state", badge(coverage.status)),
    el(
      "div",
      { class: "grid two host-grid" },
      hosts.length
        ? hosts.map((h) => hostCard(h, ctx))
        : empty("No authorized workstations are reported."),
    ),
    section(
      "Serving history",
      el("span", {
        class: "meta",
        text: `Window ${ctx.range} · gaps preserve missing evidence`,
      }),
    ),
    el(
      "div",
      { class: "grid two" },
      ["generation", "ttft", "queue", "gpu_memory"].map((id) =>
        chartPanel(id, ctx),
      ),
    ),
    section(
      "Operations requiring attention",
      el("a", { href: route("operations"), text: "View history →" }),
    ),
    opsSlot,
  );
}
export async function workstationsView(ctx, id) {
  if (id) return workstationDetail(ctx, id);
  const results = el("div", { class: "grid two host-grid" });
  const search = el("input", {
    type: "search",
    placeholder: "Find workstation",
    autocomplete: "off",
  });
  const platform = select(
    [
      ["", "All platforms"],
      ...new Set(
        list(ctx.fleet.hosts)
          .map((h) => h.platform)
          .filter(Boolean),
      ),
    ],
    "",
  );
  const state = select(
    [
      ["", "All controller states"],
      ...new Set(
        list(ctx.fleet.hosts)
          .map((h) => h.controller?.status)
          .filter(Boolean),
      ),
    ],
    "",
  );
  const render = () => {
    const found = list(ctx.fleet.hosts).filter(
      (h) =>
        (!ctx.host || h.id === ctx.host) &&
        `${h.display_name} ${h.id}`
          .toLowerCase()
          .includes(search.value.toLowerCase()) &&
        (!platform.value || h.platform === platform.value) &&
        (!state.value || h.controller?.status === state.value),
    );
    results.replaceChildren(
      ...(found.length
        ? found.map((h) => hostCard(h, ctx))
        : [empty("No workstations match these filters.")]),
    );
  };
  [search, platform, state].forEach((input) =>
    input.addEventListener("input", render),
  );
  render();
  return el(
    "div",
    {},
    heading(
      "Workstations",
      "Controller reachability, telemetry freshness, and GPU ownership are separate observations.",
    ),
    el(
      "div",
      { class: "filters" },
      field("Search", search),
      field("Platform", platform),
      field("Controller", state),
    ),
    results,
  );
}
async function workstationDetail(ctx, id) {
  const host = await request(`hosts/${encodeURIComponent(id)}`, {
    signal: ctx.signal,
  });
  const controlsSlot = el("div", {});
  ctx.jobs.push(async () => {
    try {
      const controls = await request(query("controls", { resource: id }), {
        signal: ctx.signal,
      });
      if (!ctx.signal.aborted)
        controlsSlot.replaceChildren(actionButtons(id, controls, ctx));
    } catch (error) {
      if (error.name !== "AbortError")
        controlsSlot.replaceChildren(notice(error.message, "warning"));
    }
  });
  const diagnostics = list(host.diagnostics);
  const diagResults = el("div", { class: "stack" }),
    search = el("input", { type: "search", placeholder: "Filter diagnostics" });
  const renderDiag = () => {
    const shown = diagnostics.filter((d) =>
      `${d.name || d.id} ${d.source || ""}`
        .toLowerCase()
        .includes(search.value.toLowerCase()),
    );
    diagResults.replaceChildren(
      ...(shown.length
        ? shown.map((d) =>
            el(
              "div",
              { class: "panel" },
              el("h3", { text: d.name || d.id }),
              badge(d.status),
              jsonDetails(d, "Bounded diagnostic metadata"),
            ),
          )
        : [empty("No bounded diagnostics are reported for this selection.")]),
    );
  };
  search.addEventListener("input", renderDiag);
  renderDiag();
  return el(
    "div",
    {},
    heading(
      host.display_name || id,
      `${host.platform || "Platform unknown"} · Current state`,
      [el("a", { href: route("workstations"), text: "← Workstations" })],
    ),
    el(
      "div",
      { class: "grid two" },
      el(
        "div",
        { class: "panel" },
        el("h2", { text: "Owner and reachability" }),
        el(
          "div",
          { class: "status-row" },
          badge(host.controller?.status),
          badge(host.ownership_status),
        ),
        kv([
          ["Controller identity", host.id],
          ["Controller version", host.controller?.version],
          [
            "Controller observed",
            timestamp(host.controller?.observed_at, ctx.zone),
          ],
          ["Controller detail", host.controller?.reason],
          ["Telemetry", badge(host.telemetry?.status)],
          [
            "Telemetry observed",
            timestamp(host.telemetry?.observed_at, ctx.zone),
          ],
          ["Mode", host.mode],
          ["Maintenance", show(host.maintenance)],
        ]),
      ),
      el(
        "div",
        { class: "panel" },
        el("h2", { text: "Host resources" }),
        el(
          "div",
          { class: "detail-block" },
          kv(
            Object.entries(host.resources || {}).map(([key, value]) => [
              {
                cpu_utilization: "CPU utilization",
                memory_total: "Installed memory",
                memory_used: "Memory in use",
                disk: "Disk I/O",
                network: "Network I/O",
              }[key] || key,
              value?.chart
                ? `${value.scope} · historical series below`
                : metric(value),
            ]),
          ),
        ),
        Object.keys(host.resources || {}).length
          ? null
          : empty(
              "CPU, memory, filesystem, and network observations are not reported.",
            ),
      ),
    ),
    section("Physical GPUs"),
    el(
      "div",
      { class: "grid two" },
      list(host.gpus).map((g) =>
        el(
          "article",
          { class: "panel" },
          gpuCard(g, ctx),
          el("div", { class: "detail-block" }, copy("GPU UUID", g.uuid)),
        ),
      ),
    ),
    section("Declared operating profiles"),
    el(
      "div",
      { class: "panel" },
      list(host.profiles).length
        ? el(
            "div",
            { class: "stack" },
            host.profiles.map((profile) =>
              el(
                "div",
                {},
                el("h3", {
                  text:
                    profile.label ||
                    profile.name ||
                    profile.id ||
                    show(profile),
                }),
                el("p", {
                  class: "meta",
                  text:
                    profile.description ||
                    "Declared profile. Its availability is determined by current owner policy.",
                }),
              ),
            ),
          )
        : empty("No owner profile observations are available."),
      controlsSlot,
    ),
    section("Host telemetry history"),
    el(
      "div",
      { class: "grid two" },
      [
        "host_cpu",
        "host_ram",
        "host_disk",
        "host_network",
        "gpu_utilization",
      ].map((chart) => chartPanel(chart, { ...ctx, host: id, serve: "" })),
    ),
    section("Diagnostics"),
    el("div", { class: "filters" }, field("Search name or source", search)),
    diagResults,
  );
}
export function serveCard(serve, ctx) {
  return el(
    "article",
    { class: "card" },
    el(
      "div",
      { class: "card-head" },
      el(
        "div",
        {},
        el("a", {
          class: "card-title",
          href: route("serves", serve.id),
          text: serve.display_name || serve.id,
        }),
        el("p", {
          class: "meta",
          text: `${serve.host_id} · ${serve.engine || "Engine not reported"}`,
        }),
      ),
      badge(serve.runtime_state),
    ),
    el("p", {
      class: "mono",
      text: serve.model || "Configured model not reported",
    }),
    el(
      "div",
      { class: "status-row" },
      el("span", { class: "meta", text: "Readiness" }),
      badge(serve.readiness),
      el("span", { class: "meta", text: "Admission" }),
      badge(serve.admission),
    ),
    kv([
      ["Aliases", list(serve.aliases).join(", ") || "None reported"],
      ["Queue", metric(serve.metrics?.queue)],
      ["Generation", metric(serve.metrics?.generation)],
      ["Declared GPUs", list(serve.gpu_ids).join(", ") || "Not reported"],
      ["Observed", timestamp(serve.observed_at, ctx.zone)],
    ]),
  );
}
export async function servesView(ctx, id, tab = "overview") {
  if (id) return serveDetail(ctx, id, tab);
  const results = el("div", { class: "grid two" });
  const search = el("input", {
    type: "search",
    placeholder: "Search serve, model, or alias",
  });
  const render = () => {
    const found = list(ctx.fleet.serves).filter(
      (s) =>
        (!ctx.host || s.host_id === ctx.host) &&
        (!ctx.serve || s.id === ctx.serve) &&
        `${s.display_name} ${s.model} ${list(s.aliases).join(" ")}`
          .toLowerCase()
          .includes(search.value.toLowerCase()),
    );
    results.replaceChildren(
      ...(found.length
        ? found.map((s) => serveCard(s, ctx))
        : [empty("No serves match the current scope.")]),
    );
  };
  search.addEventListener("input", render);
  render();
  return el(
    "div",
    {},
    heading(
      "Serves",
      "Configured identity, runtime, readiness, and admission for each declared serving instance.",
    ),
    el("div", { class: "filters" }, field("Search", search)),
    results,
  );
}
async function serveDetail(ctx, id, tab) {
  const serve = await request(`serves/${encodeURIComponent(id)}`, {
    signal: ctx.signal,
  });
  const detailCtx = { ...ctx, host: serve.host_id, serve: id };
  const actions = el("div", {});
  ctx.jobs.push(async () => {
    try {
      const controls = await request(query("controls", { resource: id }), {
        signal: ctx.signal,
      });
      if (!ctx.signal.aborted)
        actions.replaceChildren(
          actionButtons(id, controls, detailCtx, {
            exclude: ["configuration.apply", "experiment.start"],
          }),
        );
    } catch (error) {
      if (error.name !== "AbortError")
        actions.replaceChildren(notice(error.message, "warning"));
    }
  });
  const tabs = el(
    "div",
    { class: "tabs", "aria-label": "Serve views" },
    ["overview", "configuration", "metrics", "logs", "evidence"].map((name) =>
      el("a", {
        href: route("serves", id, name),
        "aria-current": tab === name ? "page" : undefined,
        text: name[0].toUpperCase() + name.slice(1),
      }),
    ),
  );
  let content;
  if (tab === "configuration")
    content = el(
      "div",
      { class: "panel" },
      el("h2", { text: "Configured and observed identity" }),
      kv([
        ["Configured model", serve.model],
        ["Observed model", serve.observed_model],
        ["Engine", serve.engine],
        ["Configuration", serve.configuration || "Not reported"],
      ]),
      el(
        "div",
        { class: "actions" },
        el("a", {
          href: route("configuration", id),
          text: "Open configuration editor →",
        }),
      ),
    );
  else if (tab === "metrics")
    content = el(
      "div",
      { class: "grid two" },
      [
        "generation",
        "ttft",
        "queue",
        "gpu_memory",
        "prompt",
        "kv_cache",
        "errors",
      ].map((chart) => chartPanel(chart, detailCtx)),
    );
  else if (tab === "logs") {
    const logs = serve.logs;
    content = el(
      "div",
      { class: "panel" },
      el("h2", { text: "Owner logs" }),
      notice(
        "Bounded metadata logs only. This view does not follow or execute arbitrary requests.",
      ),
      logs?.lines
        ? el(
            "div",
            {},
            el("p", {
              class: "meta",
              text: `${list(logs.lines).length} lines · ${logs.truncated ? "truncated" : "within limit"}`,
            }),
            el("pre", {
              class: "evidence-json",
              text: list(logs.lines).map(show).join("\n"),
            }),
          )
        : empty(
            logs?.reason ||
              "No supported bounded log interface is exposed by this owner.",
          ),
    );
  } else if (tab === "evidence")
    content = el(
      "div",
      { class: "panel" },
      el("h2", { text: "Retained evidence" }),
      list(serve.evidence).length
        ? el(
            "div",
            { class: "stack" },
            serve.evidence.map((e) =>
              button(
                e.label || e.id,
                () => evidenceDialog(e.id, ctx),
                "quiet-button",
              ),
            ),
          )
        : empty("No retained evidence is reported for this exact serve."),
    );
  else
    content = el(
      "div",
      { class: "grid two" },
      el(
        "div",
        { class: "panel" },
        el("h2", { text: "Current state" }),
        el(
          "div",
          { class: "status-row" },
          badge(serve.runtime_state),
          badge(serve.readiness),
          badge(serve.admission),
        ),
        kv([
          ["Configured model", serve.model],
          ["Observed model", serve.observed_model],
          ["Engine", serve.engine],
          ["Aliases", list(serve.aliases).join(", ")],
          ["GPU IDs", list(serve.gpu_ids).join(", ")],
          ["Ownership", serve.ownership_status],
          ["Observed", timestamp(serve.observed_at, ctx.zone)],
        ]),
        el("p", {
          class: "meta",
          text: "Readiness does not prove successful generation. See dated evidence for an actual test.",
        }),
      ),
      el(
        "div",
        { class: "panel" },
        el("h2", { text: "Current serving metrics" }),
        el(
          "div",
          { class: "detail-block" },
          kv(
            Object.entries(serve.metrics || {}).map(([key, value]) => [
              key,
              metric(value),
            ]),
          ),
        ),
      ),
    );
  return el(
    "div",
    {},
    heading(
      serve.display_name || id,
      `${serve.host_id} · ${serve.engine || "Engine not reported"}`,
      [el("a", { href: route("serves"), text: "← Serves" })],
    ),
    actions,
    tabs,
    content,
  );
}
