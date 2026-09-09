import { request, query } from "./api.js";
import { el, heading, field, select, notice, button, timestamp, table, safeLink } from "./common.js";

// Search text remains in this tab's memory only and is cleared on session changes.
let filters = { container: "", stream: "", search: "" }, scope = "";
window.addEventListener("observatory-session-changed", () => {
  filters = { container: "", stream: "", search: "" };
  scope = "";
});

export async function logsView(ctx) {
  const currentScope = `${ctx.host}/${ctx.serve}`;
  if (scope !== currentScope) {
    filters.container = "";
    scope = currentScope;
  }
  const root = el("div", { class: "stack" }, heading("Container logs", "Search collected stdout and stderr. Entries are newest first; quiet containers may have no matching logs."));
  const output = el("div", { class: "stack" });
  const base = { host: ctx.host, serve: ctx.serve, range: ctx.range };
  let sources;
  try {
    sources = await request(query("logs/sources", base), { signal: ctx.signal });
  } catch (error) {
    if (ctx.signal.aborted) return root;
    root.append(notice(error.message, "warning"), button("Retry logs", ctx.refresh));
    return root;
  }
  const containers = [...new Set(sources.containers.map((item) => item.name))];
  if (filters.container && !containers.includes(filters.container)) filters.container = "";
  const container = select([["", "All containers"], ...containers], filters.container);
  const stream = select([["", "Both streams"], ["stdout", "stdout"], ["stderr", "stderr"]], filters.stream);
  const search = el("input", { type: "search", value: filters.search, maxlength: 128, autocomplete: "off", placeholder: "Find text in log messages" });
  const submit = el("button", { type: "submit", class: "primary", text: "Search logs" });
  let active = null, serial = 0;
  ctx.signal.addEventListener("abort", () => active?.abort(), { once: true });
  const read = async () => {
    active?.abort();
    active = new AbortController();
    const signal = active.signal, generation = ++serial;
    filters = { container: container.value, stream: stream.value, search: search.value };
    submit.disabled = true;
    output.replaceChildren(notice("Reading logs…"));
    try {
      const data = await request(query("logs", { ...base, ...filters }), { signal });
      if (ctx.signal.aborted || signal.aborted || generation !== serial) return;
      const rows = data.items.map((item) => [
        timestamp(Number(BigInt(item.timestamp_ns) / 1000000n) / 1000, ctx.zone),
        item.host_id, item.container, item.stream,
        el("pre", { class: "log-message", text: item.line + (item.line_truncated ? "\n[Line shortened for display]" : "") }),
      ]);
      output.replaceChildren(
        el("p", { class: "meta" }, `${rows.length} entries · read `, timestamp(data.observed_at, ctx.zone)),
        rows.length ? table(["Time", "Workstation", "Container", "Stream", "Message"], rows, "Collected container logs") : notice(data.reason || "No matching entries."),
      );
      if (data.limit_reached) output.prepend(notice(`Showing the newest ${data.limit} matches. Narrow the time range or search to see more specific entries.`, "warning"));
      ctx.announce(`${rows.length} log entries loaded`);
    } catch (error) {
      if (!ctx.signal.aborted && !signal.aborted && generation === serial)
        output.replaceChildren(notice(error.message, "danger", "alert"));
    } finally {
      if (generation === serial) submit.disabled = false;
    }
  };
  root.append(el("form", { class: "panel log-filters", onSubmit: (event) => { event.preventDefault(); read(); } },
    field("Container", container), field("Stream", stream), field("Contains text", search), submit), output);
  const grafana = ctx.fleet?.links?.grafana;
  if (grafana) root.append(safeLink(grafana, "Open Grafana"));
  await read();
  return root;
}
