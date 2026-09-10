/* Dashboard composition study. Every series is a synthetic fixture. */
"use strict";
const ObservatorySurface = (() => {
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
  const dashboards = {
    fleet: ["Fleet overview", "Readiness, demand and fleet coverage"],
    models: ["Model performance", "Throughput, latency and cache behavior"],
    gpus: ["Physical GPUs", "Activity, memory, temperature and power"],
    hosts: ["Host resources", "CPU, RAM, disks and network"],
    benchmarks: ["Benchmark history", "Retained runs and measured evidence"],
    logs: ["Container logs", "Correlated records from selected workloads"],
    health: ["Monitoring health", "Collection gaps, freshness and alerts"],
  };
  const state = { board: "fleet", window: "15m", logQuery: "" };
  const hosts = {
    compute: "Compute host",
    macbook: "MacBook",
    harness: "Harness host",
  };
  const kpis = (items) =>
    `<div class="obs-kpis">${items.map(([label, value, unit, note]) => `<div><span>${label}</span><strong>${value}<small>${unit}</small></strong><p>${note}</p></div>`).join("")}</div>`;
  const panel = (title, desc, content) =>
    `<section class="panel obs-panel"><div class="panel-head"><div><h2>${title}</h2><p>${desc}</p></div></div><div class="panel-body">${content}</div></section>`;
  const table = (head, rows) =>
    `<div class="table-scroll"><table><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((v) => `<td>${v}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  function series(title, desc, unit, values, max, amber = false) {
    if (state.window === "1h")
      values = values.map(
        (value, index) =>
          Math.round(value * (0.8 + (index * 0.2) / (values.length - 1)) * 10) /
          10,
      );
    const points = values
      .map(
        (v, i) =>
          `${42 + i * (528 / (values.length - 1))},${155 - (v / max) * 128}`,
      )
      .join(" ");
    return panel(
      title,
      desc,
      `<svg class="obs-chart" viewBox="0 0 600 188" role="img" aria-label="${esc(title + ": synthetic " + state.window + " samples, " + unit + ". Values " + values.join(", "))}"><path class="grid" d="M42 27H570M42 91H570M42 155H570"/><text x="4" y="30">${max}</text><text x="4" y="94">${max / 2}</text><text x="15" y="158">0</text><polygon points="42,155 ${points} 570,155" fill="${amber ? "#f5ad3510" : "#28c7d710"}"/><polyline points="${points}" fill="none" stroke="${amber ? "#f5ad35" : "#28c7d7"}" stroke-width="2.4" stroke-linejoin="round"/><text x="42" y="180">${state.window === "15m" ? "14:01" : "13:16"}</text><text x="274" y="180">${state.window === "15m" ? "14:08" : "13:46"}</text><text x="531" y="180">14:16</text></svg><div class="obs-chart-foot"><span>${unit} · ${state.window} window · UTC</span><details><summary>Sample values</summary><p>${values.join(" · ")}</p></details></div>`,
    );
  }
  const metrics = (items, unknown) =>
    kpis(
      items.map(([label, value, unit, note]) => [
        label,
        unknown ? "—" : value,
        unknown ? "" : unit,
        unknown ? "Current measurement unavailable" : note,
      ]),
    );
  function fleet(api) {
    return (
      kpis([
        ["Reporting owners", "2 / 3", "", "One owner unavailable"],
        [
          "Running LLM deployments",
          String(api.readyModels()),
          "",
          "Current demo owner state",
        ],
        [
          "Decode throughput",
          api.stale ? "—" : "84.6",
          api.stale ? "" : "tok/s",
          api.stale ? "Current telemetry unavailable" : "Sample at 14:16 UTC",
        ],
        [
          "Requests waiting",
          api.stale ? "—" : "0",
          "",
          api.stale ? "Current telemetry unavailable" : "Sample at 14:16 UTC",
        ],
      ]) +
      `<div class="obs-grid">${series("Output throughput", "Client sample · aggregate decode", "tokens / second", [59, 64, 61, 75, 70, 79, 82, 81, 86, 84, 88, 84.6], 100)}${series("Request pressure", "Running requests · fixture observations", "requests", [1, 1, 2, 2, 1, 2, 1, 1, 2, 1, 1, 1], 4, true)}</div>` +
      panel(
        "Fleet coverage",
        "Last sampled inventory · current operations are in Compute.",
        table(
          ["HOST", "OWNER", "COMPUTE", "COLLECTION"],
          [
            [
              "Compute host",
              "Ready",
              "Atlas LLM · discrete GPUs",
              api.stale ? "Stale · 45s" : "Sample 14:16 UTC",
            ],
            [
              "MacBook",
              "Ready",
              "Speech · Apple Silicon",
              api.stale ? "Stale · 45s" : "Sample 14:16 UTC",
            ],
            [
              "Harness host",
              "Unavailable",
              "Agent / client",
              "Last sample 7 minutes ago",
            ],
          ],
        ),
      ) +
      `<div class="obs-crosslink"><span>Find the workload behind a measurement.</span><a href="#compute" class="text-link">Open Compute →</a></div>`
    );
  }
  function modelView(api) {
    const missing = api.location !== "compute";
    if (missing)
      return panel(
        "No LLM telemetry sample at this location",
        hosts[api.location],
        '<p class="section-copy">This design includes the retained Atlas model series on Compute host. Missing series stay unavailable; selecting another host does not reuse Atlas measurements.</p>',
      );
    return (
      metrics(
        [
          ["Decode throughput", "84.6", "tok/s", "Atlas · per request"],
          ["Streaming TTFT · p95", "248", "ms", "32 observations"],
          ["KV cache active", "42", "%", "Resident memory is separate"],
          ["Prefix cache hit ratio", "71", "%", "Last sample"],
        ],
        api.stale,
      ) +
      `<div class="obs-grid">${series("Token throughput", "Atlas · engine sample", "tokens / second", [61, 65, 75, 71, 78, 82, 85, 80, 84, 82, 88, 84.6], 100)}${series("Streaming first token", "p95 · n ≥ 20 per bucket", "milliseconds", [270, 290, 280, 260, 251, 262, 253, 246, 264, 248, 250, 248], 400, true)}${series("KV cache occupancy", "Active token occupancy · fixture", "percent", [23, 25, 29, 33, 31, 39, 42, 38, 43, 40, 44, 42], 100)}${series("Completed requests", "Request completion rate", "requests / second", [1, 2, 1, 3, 2, 3, 2, 2, 3, 2, 3, 2], 4, true)}</div>` +
      panel(
        "Metric provenance",
        "Interpret missing metrics before comparing engines.",
        table(
          ["METRIC", "SOURCE", "COVERAGE"],
          [
            [
              "Client TTFT",
              "Streaming client observations",
              "32 samples · sufficient for displayed p95",
            ],
            [
              "Engine latency",
              "Engine adapter",
              "Mean and p95 are distinct fields",
            ],
            [
              "Cache counters",
              "Bounded engine adapter",
              "Unsupported values stay unknown",
            ],
            [
              "Recipe revision",
              "Retained sample run",
              "atlas-fp8-r3 · historical context",
            ],
          ],
        ),
      )
    );
  }
  function gpuView(api) {
    if (api.location === "harness")
      return panel(
        "GPU telemetry unavailable",
        "Owner has not reported",
        '<p class="section-copy">No compute device is declared for this sample harness host.</p>',
      );
    const apple = api.location === "macbook";
    return (
      metrics(
        apple
          ? [
              ["GPU utilization", "4", "%", "Apple Silicon sample"],
              ["Unified memory", "14 / 64", "GiB", "Shared system pool"],
              ["Temperature", "—", "", "Counter unavailable"],
              ["Board power", "—", "", "Counter unavailable"],
            ]
          : [
              ["GPU A utilization", "72", "%", "Physical device · sample"],
              ["GPU A memory", "31.2 / 48", "GiB", "Reserved memory"],
              ["GPU A temperature", "61", "°C", "Device sample"],
              ["GPU A board power", "218", "W", "Device sample"],
            ],
        api.stale,
      ) +
      `<div class="obs-grid">${series("GPU activity", apple ? "Apple Silicon sample" : "GPU A · physical device", "percent", apple ? [2, 3, 7, 3, 2, 4, 3, 2, 4, 6, 3, 4] : [52, 64, 71, 59, 67, 74, 72, 69, 80, 68, 75, 72], 100)}${series(apple ? "Unified memory used" : "GPU memory reserved", "Allocation does not prove active compute", "GiB", apple ? [12, 12, 13, 13, 14, 14, 14, 14, 14, 14, 14, 14] : [30, 31, 31, 31.2, 31.2, 31.2, 31.2, 31.2, 31.2, 31.2, 31.2, 31.2], apple ? 64 : 48, true)}</div>` +
      panel(
        "Physical inventory & assignments",
        "Synthetic device identities; not live hardware inventory.",
        table(
          ["DEVICE", "ASSIGNMENT", "MEMORY MODEL", "COLLECTION"],
          apple
            ? [
                [
                  "Apple Silicon GPU",
                  "Speech services / optional LLM",
                  "Unified system memory",
                  api.stale ? "Stale" : "Sample",
                ],
              ]
            : [
                [
                  "GPU A",
                  "Atlas serving deployment",
                  "Discrete VRAM",
                  api.stale ? "Stale" : "Sample",
                ],
                [
                  "GPU B",
                  "Media workload reservation",
                  "Discrete VRAM",
                  api.stale ? "Stale" : "Sample",
                ],
              ],
        ),
      )
    );
  }
  function hostView(api) {
    const apple = api.location === "macbook",
      unknown = api.location === "harness" || api.stale;
    return (
      metrics(
        [
          ["CPU busy", apple ? "12" : "18", "%", "Host exporter sample"],
          [
            "Memory in use",
            apple ? "14 / 64" : "28 / 96",
            "GiB",
            apple ? "Unified system memory" : "System RAM",
          ],
          [
            "Filesystem free",
            apple ? "730" : "2,400",
            "GiB",
            "Sample data volume",
          ],
          ["Host uptime", "9", "days", "Retained host sample"],
        ],
        unknown,
      ) +
      (api.location === "harness"
        ? panel(
            "Collection unavailable",
            "Harness host",
            '<p class="section-copy">No current host measurements are available. The last owner sample was seven minutes ago.</p>',
          )
        : `<div class="obs-grid">${series("CPU utilization", hosts[api.location], "percent", apple ? [8, 9, 12, 10, 9, 15, 12, 13, 11, 10, 13, 12] : [13, 15, 18, 21, 17, 19, 18, 20, 17, 18, 19, 18], 100)}${series("Network receive", hosts[api.location], "Mbit / second", [4, 6, 5, 9, 7, 12, 8, 10, 13, 9, 10, 12], 20, true)}</div>`) +
      panel(
        "Host counters",
        "Dashboard coverage includes OS-specific collectors.",
        table(
          ["COUNTER", "COMPUTE HOST", "MACBOOK", "HARNESS"],
          [
            ["RAM sample", "28 / 96 GiB", "14 / 64 GiB unified", "Unknown"],
            ["Disk I/O sample", "46 MB/s", "18 MB/s", "Unknown"],
            ["1m load sample", "1.6", "1.2", "Unknown"],
            [
              "Collector scope",
              "Linux host / GPU",
              "Host / native compute",
              "Owner unavailable",
            ],
          ],
        ),
      )
    );
  }
  function benchmarkView() {
    return (
      kpis([
        ["Retained runs", "3", "", "Synthetic evidence catalog"],
        ["Models with history", "2", "", "Atlas and Orion fixtures"],
        ["Completed passing run", "1", "", "Other outcomes retained"],
        ["Candidate decision", "Pending", "", "No automatic promotion"],
      ]) +
      panel(
        "Benchmark history",
        "Historical evidence has its own model, hardware and run window.",
        table(
          ["RUN", "MODEL / RECIPE", "CORRECTNESS", "DECODE", "OUTCOME"],
          [
            [
              "EXP-042",
              "Atlas 32B / r3",
              "32 passed · 16 pending",
              "84.6 tok/s · partial",
              "Incomplete",
            ],
            [
              "EXP-041",
              "Atlas 32B / r3",
              "24 / 24 passed",
              "81.2 tok/s",
              "Passed",
            ],
            [
              "EXP-040",
              "Orion 70B / r2",
              "Invalid tool arguments",
              "Not qualified",
              "Failed",
            ],
          ],
        ),
      ) +
      `<div class="obs-crosslink"><span>Compare matched workloads or prepare the next deterministic run.</span><a href="#bench" class="text-link">Open Workbench →</a></div>`
    );
  }
  function logRows(api) {
    if (api.location === "harness") return [];
    return api.location === "macbook"
      ? [
          ["14:16:00", "INFO", "speech-service", "Sample speech owner ready"],
          [
            "14:16:01",
            "INFO",
            "speech-service",
            "Native process metrics received",
          ],
        ]
      : [
          [
            "14:00:02",
            "INFO",
            "sample-atlas",
            "EXP-042 recipe fingerprint captured",
          ],
          [
            "14:01:08",
            "INFO",
            "sample-atlas",
            "Independent protocol preflight passed",
          ],
          [
            "14:08:00",
            "INFO",
            "sample-atlas",
            "Context phase 32768 · concurrency 1",
          ],
          ["14:16:00", "INFO", "sample-atlas", "32 checks passed · 16 pending"],
          [
            "14:16:01",
            "WARN",
            "sample-atlas",
            "Run incomplete; qualification pending",
          ],
        ];
  }
  function logBody(api) {
    const rows = logRows(api)
      .filter((r) => state.window === "1h" || r[0] >= "14:01:00")
      .filter((r) =>
        r.join(" ").toLowerCase().includes(state.logQuery.toLowerCase()),
      );
    return rows.length
      ? table(
          ["TIME · UTC", "LEVEL", "WORKLOAD", "MESSAGE"],
          rows.map((r) => r.map(esc)),
        )
      : '<p class="section-copy">No matching sample log records. Missing source data is not proof of an idle service.</p>';
  }
  function logs(api) {
    return (
      panel(
        "Container & process logs",
        hosts[api.location] + " · scoped fixture records",
        `<label>Filter messages<input id="obs-log-filter" value="${esc(state.logQuery)}" placeholder="Level, workload or message"></label><div id="obs-log-results" style="margin-top:18px">${logBody(api)}</div>`,
      ) +
      `<div class="obs-crosslink"><span>Open a workload’s logs or inspect its managed runtime.</span><a href="#compute" class="text-link">Open Compute →</a></div>`
    );
  }
  function health(api) {
    return (
      kpis([
        [
          "Reporting targets",
          api.stale ? "Unknown" : "2 / 3",
          "",
          "One expected owner missing",
        ],
        [
          "Prometheus memory",
          api.stale ? "—" : "186",
          api.stale ? "" : "MiB",
          "Monitoring process sample",
        ],
        ["Rule failures", api.stale ? "—" : "0", "", "Last observed window"],
        [
          "Collection age",
          api.stale ? "45" : "5",
          "seconds",
          "Synthetic scrape source",
        ],
      ]) +
      `<div class="notice">Harness host is missing from collection. Its current activity remains unknown.</div>` +
      panel(
        "Collection & readiness",
        "Reachability, readiness and data freshness are independent.",
        table(
          ["SOURCE", "STATE", "LAST SAMPLE", "COVERAGE"],
          [
            [
              "Compute metrics",
              api.stale ? "Stale" : "Sample available",
              api.stale ? "45 seconds ago" : "14:16 UTC",
              "Host + physical GPU",
            ],
            [
              "MacBook metrics",
              api.stale ? "Stale" : "Sample available",
              api.stale ? "45 seconds ago" : "14:16 UTC",
              "Host + unified memory",
            ],
            [
              "Harness owner",
              "Unavailable",
              "7 minutes ago",
              "Expected source missing",
            ],
            [
              "Router readiness",
              "Sample available",
              "14:16 UTC",
              "Model probe state",
            ],
            [
              "Log collection",
              "Sample available",
              "14:16 UTC",
              "Declared workloads only",
            ],
          ],
        ),
      ) +
      panel(
        "Alerts & monitoring headroom",
        "Selected panels from the existing monitoring-health dashboard.",
        table(
          ["SIGNAL", "SAMPLE STATE"],
          [
            ["Expected target missing", "Harness host · investigate collector"],
            ["Configuration loaded", "Yes · synthetic observation"],
            ["Rule evaluation errors", "None in fixture window"],
            ["Monitoring storage", "Headroom available in sample"],
          ],
        ),
      )
    );
  }
  function render(api) {
    state.board = dashboards[api.dashboard] ? api.dashboard : "fleet";
    const [title, desc] = dashboards[state.board];
    const fleetScope = ["fleet", "benchmarks", "health"].includes(state.board);
    const views = {
      fleet,
      models: modelView,
      gpus: gpuView,
      hosts: hostView,
      benchmarks: benchmarkView,
      logs,
      health,
    };
    return `<div class="intro"><div><div class="eyebrow">OBSERVABILITY</div><h1>${title}</h1><p>${desc}</p></div><div class="intro-actions">${state.board === "benchmarks" ? '<span class="obs-evidence-window">Evidence range<strong>All retained run windows</strong></span>' : `<label class="obs-time-label">Sample window<select id="obs-window"><option value="15m" ${state.window === "15m" ? "selected" : ""}>15 minutes · ends 14:16</option><option value="1h" ${state.window === "1h" ? "selected" : ""}>1 hour · ends 14:16</option></select></label>`}<button type="button" class="quiet" data-action="obs-grafana">Grafana context ↗</button></div></div>
      <nav class="obs-dashboard-nav" aria-label="Observability dashboards">${Object.entries(
        dashboards,
      )
        .map(
          ([id, [name]]) =>
            `<a href="#observability/${id}" ${id === state.board ? 'aria-current="page"' : ""}>${name}</a>`,
        )
        .join("")}</nav>
      <div class="obs-scope"><span>${fleetScope ? "SCOPE / <b>All fleet</b>" : "LOCATION / <b>" + hosts[api.location] + "</b>"}</span><span>${state.board === "benchmarks" ? "Retained runs · independent run windows" : "Dashboard sample ends 14:16 UTC"}</span><span class="pill ${api.stale ? "warn" : "neutral"}">${api.stale ? "Latest telemetry stale" : "Synthetic dashboard data"}</span></div>
      ${api.stale ? '<p class="obs-stale-note">Current telemetry is unavailable. Historical fixture series remain visible for inspection.</p>' : ""}${views[state.board](api)}`;
  }
  function action(name, api) {
    if (name !== "obs-grafana") return false;
    api.openDialog(
      "Grafana dashboard context",
      api.kv([
        ["Dashboard", dashboards[state.board][0]],
        [
          "Scope",
          ["fleet", "benchmarks", "health"].includes(state.board)
            ? "All fleet"
            : hosts[api.location],
        ],
        [
          "Window",
          state.board === "benchmarks"
            ? "Retained run windows"
            : state.window + " ending 14:16 UTC",
        ],
        ["Access", "Grafana retains its own authorization"],
      ]) +
        '<p class="section-copy" style="margin-top:15px">The live adapter will open the matching dashboard with these filters. No Grafana destination is configured in this design.</p>',
      api.btn("Close", "close"),
    );
    return true;
  }
  function input(e, api) {
    if (e.target.id !== "obs-log-filter") return false;
    state.logQuery = e.target.value;
    document.querySelector("#obs-log-results").innerHTML = logBody(api);
    return true;
  }
  function change(e, api) {
    if (e.target.id !== "obs-window") return false;
    state.window = e.target.value;
    api.render();
    document.querySelector("#obs-window").focus({ preventScroll: true });
    return true;
  }
  return { render, action, input, change };
})();
