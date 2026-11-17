// Interactive charts for docs/benchmarks/results.md.
// Reads the record index the MkDocs hook builds from retained evidence.
// The record table follows this script in the page, so wait for the full document.
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("[data-av-filter]").forEach(function (input) {
    const rows = document.querySelectorAll("#" + input.dataset.avFilter + " tbody tr");
    input.addEventListener("input", function () {
      const terms = input.value.toLowerCase().split(/\s+/).filter(Boolean);
      rows.forEach(function (row) {
        const text = row.textContent.toLowerCase();
        row.hidden = !terms.every(function (term) { return text.includes(term); });
      });
    });
  });
});

(function () {
  "use strict";

  const root = document.getElementById("benchmark-explorer");
  if (!root || typeof vegaEmbed !== "function") return;

  const status = root.querySelector("[data-status]");
  const controls = {
    record: root.querySelector("[data-filter=record]"),
    hardware: root.querySelector("[data-filter=hardware]"),
    context: root.querySelector("[data-filter=context]"),
    eligible: root.querySelector("[data-filter=eligible]"),
  };
  let points = [];

  function addOptions(select, values, label) {
    values.forEach(function (value) {
      const option = document.createElement("option");
      option.value = String(value);
      option.textContent = label ? label(value) : String(value);
      select.appendChild(option);
    });
  }

  function themeConfig() {
    const style = getComputedStyle(document.body);
    const fg = style.getPropertyValue("--md-default-fg-color").trim() || "#333";
    const muted = style.getPropertyValue("--md-default-fg-color--lighter").trim() || "#999";
    return {
      background: null,
      axis: { labelColor: fg, titleColor: fg, gridColor: muted, domainColor: muted, tickColor: muted },
      legend: { labelColor: fg, titleColor: fg, labelLimit: 280 },
      view: { stroke: null },
    };
  }

  function selected() {
    return points.filter(function (point) {
      return (!controls.record.value || point.record === controls.record.value)
        && (!controls.hardware.value || point.hardware === controls.hardware.value)
        && (!controls.context.value || String(point.context_tokens) === controls.context.value)
        && (!controls.eligible.checked || point.performance_eligible !== false);
    });
  }

  function tooltip() {
    return [
      { field: "title", title: "Finding" },
      { field: "series", title: "Configuration" },
      { field: "hardware", title: "Hardware" },
      { field: "engine", title: "Engine" },
      { field: "concurrency", title: "Concurrency" },
      { field: "context_tokens", title: "Context tokens" },
      { field: "per_user_decode_tok_s_p50", title: "Per-user decode p50 (tok/s)", format: ".1f" },
      { field: "aggregate_output_tok_s", title: "Aggregate output (tok/s)", format: ".1f" },
      { field: "ttft_p50_ms", title: "TTFT p50 (ms)", format: ".0f" },
      { field: "ttft_p95_ms", title: "TTFT p95 (ms)", format: ".0f" },
      { field: "completion", title: "Completed / requested" },
    ];
  }

  function render() {
    const data = selected();
    const oneRecord = Boolean(controls.record.value);
    const colorField = oneRecord ? "series" : "hardware";
    const charted = data.filter(function (p) { return p.per_user_decode_tok_s_p50 != null && p.aggregate_output_tok_s != null; });
    status.textContent = data.length + " measurements match; " + charted.length
      + " record both per-user decode and aggregate output rate and appear in the trade-off chart.";
    const color = { field: colorField, type: "nominal", title: oneRecord ? "Configuration" : "Hardware" };
    const config = themeConfig();

    vegaEmbed("#benchmark-tradeoff", {
      $schema: "https://vega.github.io/schema/vega-lite/v5.json",
      width: "container", height: 360, config: config,
      data: { values: charted },
      encoding: {
        x: { field: "per_user_decode_tok_s_p50", type: "quantitative", title: "Per-user decode p50 (tok/s) →  more interactive" },
        y: { field: "aggregate_output_tok_s", type: "quantitative", title: "Aggregate output (tok/s) →  more throughput" },
        color: color,
      },
      layer: [
        { mark: { type: "line", opacity: 0.5 }, encoding: { detail: { field: "sweep" }, order: { field: "concurrency" } } },
        { mark: { type: "point", filled: true, size: 70, cursor: "pointer" },
          encoding: { tooltip: tooltip(), href: { field: "href" } } },
      ],
    }, { renderer: "svg", actions: { export: true, source: false, compiled: false, editor: false } });

    vegaEmbed("#benchmark-ttft", {
      $schema: "https://vega.github.io/schema/vega-lite/v5.json",
      width: "container", height: 260, config: config,
      data: { values: data.filter(function (p) { return p.ttft_p50_ms != null; }) },
      encoding: {
        x: { field: "concurrency", type: "quantitative", scale: { type: "log", base: 2 }, title: "Concurrent requests" },
        y: { field: "ttft_p50_ms", type: "quantitative", scale: { type: "log" }, title: "Time to first token p50 (ms, log)" },
        color: color,
      },
      layer: [
        { mark: { type: "line", opacity: 0.5 }, encoding: { detail: { field: "sweep" }, order: { field: "concurrency" } } },
        { mark: { type: "point", filled: true, size: 60, cursor: "pointer" }, encoding: { tooltip: tooltip(), href: { field: "href" } } },
      ],
    }, { renderer: "svg", actions: { export: true, source: false, compiled: false, editor: false } });
  }

  fetch("index.json")
    .then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    })
    .then(function (index) {
      const records = {};
      index.records.forEach(function (record) { records[record.id] = record; });
      points = index.capacity_points.map(function (point) {
        const record = records[point.record] || {};
        const missing = index.not_recorded;
        const series = [point.model || missing, point.engine || missing].join(" · ");
        return Object.assign({}, point, {
          hardware: point.hardware || missing,
          engine: point.engine || missing,
          title: record.title || point.record,
          series: series,
          // One line per configuration and context inside one finding, ordered by concurrency.
          sweep: [point.record, series, point.context_tokens].join("|"),
          completion: (point.completed == null ? "?" : point.completed) + " / " + (point.requests == null ? "?" : point.requests),
          href: record.links && record.links.finding ? "../../findings/" + record.links.finding.replace(/\.md$/, "/") : null,
        });
      });
      const withPoints = index.records.filter(function (record) { return record.artifacts.capacity > 0; });
      addOptions(controls.record, withPoints.map(function (r) { return r.id; }), function (id) {
        return records[id].date + " — " + records[id].title;
      });
      const unique = function (field) {
        return Array.from(new Set(points.map(function (p) { return p[field]; }).filter(function (v) { return v != null; })));
      };
      addOptions(controls.hardware, unique("hardware").sort());
      addOptions(controls.context, unique("context_tokens").sort(function (a, b) { return a - b; }));
      // Open on the newest finding with a concurrency sweep; "All findings" stays one click away.
      const sweep = withPoints.find(function (record) { return record.workload.concurrency.length > 2; });
      if (sweep) controls.record.value = sweep.id;
      Object.keys(controls).forEach(function (name) { controls[name].addEventListener("change", render); });
      render();
      // Re-read theme colors when the reader switches between light and dark.
      new MutationObserver(render).observe(document.body, { attributes: true, attributeFilter: ["data-md-color-scheme"] });
    })
    .catch(function (error) {
      status.textContent = "The benchmark index could not be loaded (" + error.message + "). The table below still lists every record.";
    });
})();
