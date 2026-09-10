import { badge, button, el, empty, heading, kv, metric, notice, route, section, table, timestamp } from "./common.js";
import { chartPanel } from "./charts.js";
import { hostCard, gpuCard } from "./resources.js";
import { logsView } from "./logs.js";
import { request } from "./api.js";
import { evidenceDialog } from "./operations.js";

const boards = [["fleet", "Fleet health"], ["models", "Model performance"], ["gpus", "Physical GPUs"], ["hosts", "Host resources"], ["benchmarks", "Benchmark history"], ["logs", "Container logs"], ["monitoring", "Monitoring health"]];
export async function observabilityView(ctx, tab = "fleet") {
  tab = boards.some(([id]) => id === tab) ? tab : "fleet";
  const root = el("div", { class: "workbench-page stack", "data-story": "US-OBS-01" }, heading("Observability", "Follow serving performance and fleet health. Each chart keeps its source, time range, and gaps."));
  root.append(el("nav", { class: "local-tabs", "aria-label": "Observability dashboards" }, ...boards.map(([id, title]) => button(title, () => { location.hash = route("observability", id); }, tab === id ? "primary" : "quiet-button"))));
  const hosts = (ctx.fleet.hosts || []).filter(host => !ctx.host || host.id === ctx.host);
  const serves = (ctx.fleet.serves || []).filter(serve => (!ctx.host || serve.host_id === ctx.host) && (!ctx.serve || serve.id === ctx.serve));
  const charts = ids => el("div", { class: "grid two" }, ...ids.map(id => chartPanel(id, ctx)));
  if (tab === "fleet") {
    root.append(el("div", { class: "grid three" }, ...hosts.map(host => hostCard(host, ctx))), charts(["generation", "ttft", "queue", "gpu_memory"]));
    if (!hosts.length) root.append(empty("No declared hosts are available in this scope."));
  } else if (tab === "models") {
    root.append(table(["Model", "Host", "Owner state", "Admission", "Generation"], serves.map(serve => [serve.observed_model || serve.model, serve.host_id, badge(serve.runtime_state), badge(serve.admission), metric(serve.metrics?.generation)])), charts(["generation", "ttft", "prompt", "queue", "kv_cache", "errors"]));
  } else if (tab === "gpus") {
    root.append(notice("Memory allocation and active GPU utilization are measured separately."), el("div", { class: "grid two" }, ...hosts.flatMap(host => (host.gpus || []).map(gpu => el("section", { class: "panel stack" }, section(host.display_name || host.id), gpuCard(gpu, ctx))))), charts(["gpu_memory", "gpu_utilization"]));
  } else if (tab === "hosts") {
    root.append(el("div", { class: "grid three" }, ...hosts.map(host => hostCard(host, ctx))), charts(["host_cpu", "host_ram", "host_disk", "host_network"]));
  } else if (tab === "benchmarks") {
    try {
      const data = await request("evidence", { signal: ctx.signal });
      const rows = (data.items || []).filter(item => (!ctx.host || item.host_id === ctx.host) && (!ctx.serve || item.resource_id === ctx.serve));
      root.append(notice("Compare records with matching model, hardware, context, concurrency, and correctness gates. Historical timing does not establish current readiness."));
      root.append(rows.length ? table(["Run", "Model", "Observed", "Correctness", ""], rows.map(item => [item.label || item.id, item.model || "See evidence", timestamp(item.observed_at, ctx.zone), badge(item.correctness?.status || item.verification?.status), button("Inspect evidence", () => evidenceDialog(item.id, ctx), "quiet-button")])) : empty("No retained benchmark evidence matches this scope."));
    } catch (error) { root.append(notice(error.message, "warning")); }
  } else if (tab === "logs") root.append(await logsView(ctx));
  else {
    const integration = ctx.settings?.integrations || {};
    root.append(el("section", { class: "panel stack" }, section("Collection health", badge(integration.status)), kv([["Source", integration.source || "Unavailable"], ["Last successful collection", timestamp(integration.observed_at, ctx.zone)], ["Grafana", integration.grafana_configured ? "Configured" : "Not configured"], ["Available charts", (integration.charts || []).length]])),
      table(["Host", "Controller", "Telemetry", "Last observation"], hosts.map(host => [host.display_name || host.id, badge(host.controller?.status), badge(host.telemetry?.status), timestamp(host.telemetry?.observed_at, ctx.zone)])),
      notice(ctx.fleet.coverage?.reason || "Unavailable collection is shown as unknown; it is never converted into a zero reading."));
  }
  return root;
}
