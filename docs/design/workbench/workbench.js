/* Design-only application. Every record is synthetic; no owner or model calls. */
"use strict";
const $ = (s) => document.querySelector(s);
const escape = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const state = {
  page: "bench",
  stale: false,
  experimentTab: "overview",
  model: "Atlas 32B",
  connector: "Anvil router",
  preset: "Precise",
  prompt:
    "Explain why a model can reserve most of its GPU memory while GPU utilization stays near zero.",
  temperature: "0.2",
  maxTokens: "512",
  messages: [],
  attached: false,
  preview: null,
  sandbox: false,
  demoRun: false,
  sessionTab: "activity",
  logQuery: "",
};
const runs = [
  {
    id: "EXP-042",
    name: "Context window sweep",
    model: "Atlas 32B",
    detail: "FP8 · 32k context · concurrency 1",
    status: "In progress",
    tone: "",
    throughput: "84.6",
    gate: "32 / 48 checks",
  },
  {
    id: "EXP-041",
    name: "Tool round-trip validation",
    model: "Atlas 32B",
    detail: "FP8 · 32k context · concurrency 1",
    status: "Passed",
    tone: "",
    throughput: "81.2",
    gate: "24 / 24 checks",
  },
  {
    id: "EXP-040",
    name: "Long-context candidate",
    model: "Orion 70B",
    detail: "INT4 · 64k context · concurrency 2",
    status: "Failed",
    tone: "bad",
    throughput: "—",
    gate: "Invalid tool arguments",
  },
];
const pill = (text, tone = "") =>
  `<span class="pill ${tone}">${escape(text)}</span>`;
const btn = (text, action, style = "quiet") =>
  `<button class="${style}" data-action="${action}">${text}</button>`;
const intro = (label, title, description, actions = "") =>
  `<div class="intro"><div><div class="eyebrow">${label}</div><h1>${title}</h1><p>${description}</p></div><div class="intro-actions">${actions}</div></div>`;
const kv = (pairs) =>
  pairs
    .map(
      ([k, v]) =>
        `<div class="kv-row"><span>${escape(k)}</span><span>${escape(v)}</span></div>`,
    )
    .join("");
const panel = (title, body, subtitle = "") =>
  `<section class="panel"><div class="panel-head"><div><h2>${title}</h2>${subtitle ? `<p>${subtitle}</p>` : ""}</div></div><div class="panel-body">${body}</div></section>`;
function metrics() {
  return `<div class="metrics">${[
    ["Decode throughput", "84.6", "tok/s", "Per request · latest sample"],
    ["First token · p95", "248", "ms", "Client observed · n = 32"],
    ["Checks passed", "32/32", "", "16 still pending"],
    ["Queue depth", "0", "requests", "At run snapshot"],
  ]
    .map(
      ([k, v, u, n]) =>
        `<div class="metric"><div class="metric-label">${k}</div><div class="metric-value">${v} <span>${u}</span></div><div class="metric-note">${n}</div></div>`,
    )
    .join("")}</div>`;
}
function chart() {
  return `<section class="panel"><div class="panel-head"><div><h2>Performance over the run</h2><p>Decode throughput · tokens / second · synthetic samples</p></div><div class="legend"><span><i></i>Candidate</span><span><i class="blue"></i>Baseline</span></div></div><div class="panel-body"><svg class="chart" viewBox="0 0 650 185" preserveAspectRatio="none" role="img" aria-label="Synthetic throughput increases from 60 to 85 tokens per second. Baseline remains near 73. An annotation marks the 32k context phase."><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#c9ed89" stop-opacity=".16"/><stop offset="100%" stop-color="#c9ed89" stop-opacity="0"/></linearGradient></defs><path class="grid" d="M36 20H638M36 64H638M36 108H638M36 152H638"/><text x="5" y="24">100</text><text x="13" y="68">80</text><text x="13" y="112">60</text><text x="13" y="156">40</text><path d="M36 108L70 94L105 102L138 83L172 90L205 68L239 75L272 57L305 65L339 53L372 62L405 48L439 55L472 42L506 51L539 46L573 58L606 50L638 54L638 152H36Z" fill="url(#chart-fill)"/><path class="baseline" d="M36 88L70 84L105 87L138 78L172 85L205 80L239 82L272 76L305 83L339 75L372 80L405 73L439 80L472 77L506 74L539 79L573 72L606 80L638 77"/><path class="trace" d="M36 108L70 94L105 102L138 83L172 90L205 68L239 75L272 57L305 65L339 53L372 62L405 48L439 55L472 42L506 51L539 46L573 58L606 50L638 54"/><path d="M372 12V152" stroke="#edbb78" stroke-dasharray="3 5" opacity=".7"/><text x="380" y="23">32k context phase</text><text x="36" y="177">14:00</text><text x="177" y="177">14:04</text><text x="322" y="177">14:08</text><text x="466" y="177">14:12</text><text x="608" y="177">14:16</text></svg></div><div class="chart-foot"><span>Historical run window · 16 minutes</span><button class="small quiet" data-action="chart-data">View sample data</button></div></section>`;
}
function runTable() {
  const items = state.demoRun
    ? [
        {
          id: "EXP-043",
          name: escape(state.preview?.name || "New test preview"),
          model: state.model,
          detail: "Demo configuration only",
          status: "Simulated",
          tone: "warn",
          throughput: "—",
          gate: "No model called",
        },
        ...runs,
      ]
    : runs;
  return `<div class="panel table-scroll"><table><thead><tr><th scope="col">EXPERIMENT</th><th scope="col">STATE</th><th scope="col">CORRECTNESS</th><th scope="col">DETAILS</th></tr></thead><tbody>${items.map((r) => `<tr><td><span class="run-name">${r.name}</span><small>${r.id} · ${r.model}</small></td><td>${pill(r.status, r.tone)}</td><td>${r.gate}</td><td><button class="small quiet" data-action="inspect-${r.id}">Inspect ↗</button></td></tr>`).join("")}</tbody></table></div>`;
}
function bench() {
  return (
    intro(
      "YOUR RESEARCH DESK",
      "Make the next run count.",
      "Test a model. Understand its behavior. Keep the evidence.",
      btn("Open playground", "playground") +
        btn("+ New experiment", "new-experiment", "primary"),
    ) +
    `<section class="focus-card"><div class="eyebrow">IN FOCUS / EXP-042</div><div class="focus-top"><div><h2>Context window sweep</h2><p>Atlas 32B · FP8 · Compute A · 8k → 32k context</p></div>${pill("Sample run · in progress")}</div><div class="focus-foot"><span class="mono">32 / 48</span><div class="bar"><span style="width:67%"></span></div><a class="text-link" href="#experiments">Open run ↗</a></div></section>` +
    metrics() +
    chart() +
    `<div class="section-title"><h2>Recent experiments</h2><a class="text-link" href="#experiments">View all runs ↗</a></div>` +
    runTable() +
    `<div class="panel"><div class="task-row"><span class="index-number">↳</span><div><h3>Give this experiment a place in the project.</h3><p>Connect results to a PRD, task, and acceptance criterion.</p></div><a class="text-link" href="#work">Open Anvil work ↗</a></div></div>`
  );
}
function experiments() {
  let body = "";
  if (state.experimentTab === "overview")
    body =
      metrics() +
      chart() +
      panel(
        "Run configuration",
        kv([
          ["Model / recipe", "Atlas 32B / atlas-fp8-r3"],
          ["Runtime", "Engine A · revision demo-3"],
          ["Hardware", "1 × synthetic 48 GiB GPU"],
          ["Context sweep", "8k, 16k, 32k"],
          ["Concurrency / output limit", "1 / 512 tokens"],
          ["Warm state / seed", "Warm / 42"],
          ["Gate", "32 passed · 16 pending"],
          ["Outcome", "Incomplete; qualification pending"],
        ]),
      ) +
      runTable();
  if (state.experimentTab === "compare")
    body = `<div class="notice">EXP-040 is excluded: model, context, and concurrency differ. A failed run cannot be ranked as a speed improvement.</div><div class="panel table-scroll"><table><caption>EXP-041 versus EXP-042 · illustrative comparison</caption><thead><tr><th>DIMENSION</th><th>BASELINE · 041</th><th>CANDIDATE · 042</th></tr></thead><tbody>${[
      ["Model / recipe", "Atlas 32B / r3", "Atlas 32B / r3"],
      [
        "Hardware / runtime",
        "Same sample GPU / demo-3",
        "Same sample GPU / demo-3",
      ],
      ["Context", "32k", "8k → 32k sweep"],
      ["Concurrency", "1", "1"],
      ["Output constraint", "512 tokens", "512 tokens"],
      ["Warm state", "Warm", "Warm"],
      ["Correctness", "24 / 24 passed", "32 / 48 completed"],
      ["Mean decode speed", "81.2 tok/s", "84.6 tok/s · partial"],
      ["Decision", "Retain baseline", "No recommendation yet"],
    ]
      .map((row) => `<tr>${row.map((x) => `<td>${x}</td>`).join("")}</tr>`)
      .join(
        "",
      )}</tbody></table></div><p class="section-copy">Different workload distributions and an incomplete candidate prevent a like-for-like conclusion. Select a matched phase after completion.</p>`;
  if (state.experimentTab === "evidence")
    body = panel(
      "Evidence packet",
      `<div class="chips">${pill("Incomplete", "warn")}${pill("Synthetic data", "neutral")}</div>${kv(
        [
          ["Run identity", "EXP-042"],
          ["Recipe fingerprint", "demo:atlas-fp8-r3"],
          ["Protocol trace", "32 bounded request records"],
          ["Metrics window", "14:00–14:16 UTC"],
          ["Correctness", "32 passed / 16 pending"],
          [
            "Project attachment",
            state.attached ? "PRD-017 / T-04 (local demo)" : "Not attached",
          ],
        ],
      )}<div class="chips">${btn(state.attached ? "Attached in demo" : "Attach to Anvil task", "attach", "primary")}${btn("Inspect sample artifact", "artifact")}</div>`,
    );
  if (state.experimentTab === "events")
    body = panel(
      "Run timeline",
      `<ol class="timeline"><li>14:00 · Exact recipe and model recorded<small>Runtime fingerprint captured.</small></li><li>14:01 · Preflight passed<small>Independent protocol checks completed.</small></li><li>14:02 · 8k phase started<small>Client observations correlated with host metrics.</small></li><li>14:08 · 32k phase started<small>Warm context; concurrency 1.</small></li><li>14:16 · 32 of 48 checks complete<small>All events are illustrative.</small></li></ol>`,
    );
  return (
    intro(
      "EXPERIMENTS / EXP-042",
      "Context window sweep",
      "One run, with its configuration, telemetry, checks, and artifacts.",
      btn("+ New experiment", "new-experiment", "primary"),
    ) +
    `<div class="tabs" aria-label="Run sections">${["overview", "compare", "evidence", "events"].map((x) => `<button data-tab="${x}" aria-pressed="${state.experimentTab === x}">${x[0].toUpperCase() + x.slice(1)}</button>`).join("")}</div>` +
    body
  );
}
function playground() {
  return (
    intro(
      "PLAYGROUND",
      "Ask. Probe. Inspect.",
      "Choose the connection, recipe, and request preset explicitly.",
      btn("Save preset", "save-preset"),
    ) +
    panel(
      "Session configuration",
      `<div class="form-grid"><label>Connector<select id="connector"><option>Anvil router</option><option>Evaluation gateway · not connected</option></select></label><label>Recipe / model<select id="model"><option>Atlas 32B</option><option>Orion 70B · offline</option></select></label><label>Request preset<select id="preset"><option>Precise</option><option>Creative</option><option>Tool validation</option></select></label><label>Temperature<input id="temperature" type="number" min="0" max="2" step="0.1" value="${escape(state.temperature)}"></label></div><p class="eyebrow" style="margin-top:15px">EXACT SERVED TARGET</p><p id="served-target" class="mono"></p><div class="chips" id="target-chips"></div><details><summary class="text-link">Inspect effective request configuration</summary><pre id="effective-config"></pre></details>`,
    ) +
    panel(
      "Conversation",
      `<div id="messages">${messages()}</div><form id="prompt-form"><label for="prompt">Message</label><textarea id="prompt" required maxlength="8000">${escape(state.prompt)}</textarea><div class="form-grid" style="margin-top:12px"><label>Maximum output tokens<input id="max-tokens" type="number" min="1" max="4096" required value="${escape(state.maxTokens)}"></label><div class="intro-actions" style="align-items:end;justify-content:flex-end"><button class="primary" type="submit">Send demo request ↑</button></div></div><p class="muted" style="font-size:12px;margin-top:12px">This preview returns a canned response. Nothing is sent to a model.</p></form>`,
    ) +
    panel(
      "From a conversation to an experiment",
      `<p class="section-copy">Keep the exact request, selected recipe, and configuration together. Turn a useful prompt into a repeatable test case.</p><div class="chips">${btn("Create test from this session", "new-experiment")}${btn("Review Pi session", "sandbox")}</div>`,
    )
  );
}
function messages() {
  return state.messages.length
    ? state.messages
        .map(
          (m) =>
            `<div class="chat-message"><span class="eyebrow">${m.role === "user" ? "YOU" : "ATLAS 32B / CANNED DEMO RESPONSE"}</span><p>${escape(m.text)}</p>${m.role === "assistant" ? '<div class="response-meta"><span>Example TTFT 248 ms</span><span>Example speed 84.6 tok/s</span><span>Tool execution: off</span></div>' : ""}</div>`,
        )
        .join("")
    : `<div class="empty">A conversation with the instruments still in view.<br><small>Start with the sample question or enter your own.</small></div>`;
}
function models() {
  return (
    intro(
      "MODEL CATALOG",
      "The right setup, explicitly.",
      "Recipes define how a model runs. Presets define how you ask it.",
      btn("Connectors & presets", "connectors"),
    ) +
    `<div class="split">${[
      {
        name: "Atlas 32B",
        mark: "32B",
        recipe: "atlas-fp8-r3",
        status: "Ready",
        tone: "",
        memory: "31.2 / 48 GiB",
        context: "32,768 tokens",
        action: btn("Use in playground", "playground", "primary"),
      },
      {
        name: "Orion 70B",
        mark: "70B",
        recipe: "orion-int4-r2",
        status: "Offline",
        tone: "neutral",
        memory: "Not allocated",
        context: "65,536 tokens",
        action: btn("Review recipe load", "load-recipe"),
      },
    ]
      .map(
        (m) =>
          `<section class="panel model-card"><div class="panel-body"><div class="model-mark">${m.mark}</div><h2>${m.name}</h2><small>${m.recipe}</small><div class="chips">${pill(m.status, m.tone)}</div>${kv(
            [
              ["Memory", m.memory],
              ["Configured context", m.context],
              ["Owner", "Sample compute host"],
              [
                "Qualification",
                m.name === "Atlas 32B"
                  ? "Prior sample gate passed"
                  : "Last sample gate failed",
              ],
            ],
          )}<div class="chips">${m.action}${btn("Inspect configuration", "recipe-config")}</div></div></section>`,
      )
      .join("")}</div>` +
    panel(
      "Connections and request presets",
      `<div class="task-row"><div><h3>Anvil router</h3><p>Declared aliases · server-held credential reference · available in this demo</p></div>${pill("Sample connection")}</div><div class="task-row"><div><h3>Precise / Creative / Tool validation</h3><p>Named request settings. Their resolved values are visible before sending.</p></div>${btn("Manage presets", "connectors", "small quiet")}</div>`,
    )
  );
}
function work() {
  return (
    intro(
      "ANVIL STATE / SELECTED PROJECT",
      "Work with a reason.",
      "PRDs, executable tasks, and the evidence that connects them.",
      btn("Open Pi sessions", "sessions", "primary"),
    ) +
    `<div class="notice">Sample project records. Live integration must resolve the selected checkout through Anvil State.</div>` +
    panel(
      "Open PRDs",
      `<div class="task-row"><span class="index-number">017</span><div><h3>Qualify the research model</h3><p>Approved · 6 tasks · 2 complete · 1 ready</p><small>Acceptance: correct tools at the declared context and concurrency.</small></div>${btn("Open ready task", "task-detail", "small quiet")}</div><div class="task-row"><span class="index-number">018</span><div><h3>Repeatable model experiments</h3><p>Draft · waiting for review</p><small>Draft PRDs become executable only after the Anvil approval gate.</small></div>${pill("Draft", "neutral")}</div>`,
    ) +
    panel(
      "Ready to work · T-04",
      `<h2>Validate long-context tool behavior</h2><p class="section-copy" style="margin:10px 0">Run the approved bounded suite, retain failures, and attach the artifact packet to the task.</p>${kv(
        [
          ["PRD", "PRD-017 · approved"],
          ["Dependencies", "T-03 accepted"],
          ["Claim", "Unclaimed in sample"],
          [
            "Evidence",
            state.attached
              ? "EXP-042 attached in local demo"
              : "No linked run yet",
          ],
          ["Acceptance", "Independent review required"],
        ],
      )}<div class="chips">${btn("Review execution plan", "sandbox", "primary")}${btn("Attach run evidence", "attach")}</div>`,
    ) +
    panel(
      "Agent environment",
      `<p class="section-copy">An isolated checkout and a bounded runner session, with progress, changes, tests, and artifacts in one view. The runner acquires an Anvil claim before execution and submits evidence for independent review.</p><div class="chips">${pill(state.sandbox ? "Previewed locally" : "No session running", "neutral")}${btn("Inspect sandbox proposal", "sandbox", "small quiet")}</div>`,
    )
  );
}
function system() {
  return (
    intro(
      "FLEET / SYSTEM",
      "Know what the machine is doing.",
      "Current owner state and historical telemetry keep their own timestamps.",
      btn(
        state.stale ? "Restore sample freshness" : "Show stale telemetry",
        "stale",
      ),
    ) +
    `<div class="split">${panel(
      "Compute host",
      `<div class="chips">${pill("Owner ready")}${pill(state.stale ? "Telemetry stale" : "Sample telemetry", state.stale ? "warn" : "neutral")}</div>${kv(
        [
          [
            "CPU utilization",
            state.stale ? "Unknown · last sample 18%" : "18%",
          ],
          [
            "RAM",
            state.stale ? "Unknown · last sample 28 / 96 GiB" : "28 / 96 GiB",
          ],
          [
            "Compute A",
            state.stale
              ? "Atlas 32B · utilization unknown"
              : "Atlas 32B · 72% utilization",
          ],
          [
            "Compute B",
            state.stale
              ? "Voice sample · utilization unknown"
              : "Voice sample · 4% utilization",
          ],
          ["Admission", "1 active / 2 allowed"],
        ],
      )}`,
    )}${panel(
      "Harness host",
      `<div class="chips">${pill("Owner unavailable", "warn")}</div>${kv([
        ["Role", "Agent / client"],
        ["Last seen", "Sample: 7 minutes ago"],
        ["CPU / RAM", "Unavailable"],
        ["Model placement", "None declared"],
        ["Readiness", "Unknown"],
      ])}<p class="section-copy" style="margin-top:12px">Missing telemetry stays visible. It does not become zero or healthy.</p>`,
    )}</div>` +
    panel(
      "Logs · selected run",
      `<label>Filter sample logs<input id="log-filter" placeholder="Search message text"></label><div id="log-lines" style="margin-top:18px"></div>`,
    ) +
    panel(
      "Explore deeper",
      `<p class="section-copy">The production integration should open Grafana with the same host, serve, and run time window. Grafana remains the place for free-form queries and deeper historical analysis.</p><div class="chips">${btn("Preview Grafana context", "grafana")}</div>`,
    )
  );
}
function sessions() {
  const panes = {
    activity: `<ol class="timeline"><li>Work packet loaded<small>PRD-017 / T-04 · exact approved revision</small></li><li>Claim acquired by runner<small>Illustrative lease; no real task claimed</small></li><li>Pi inspected the test contract<small>read · tests/tool_contract.py</small></li><li>Pi proposed a fixture correction<small>edit · tests/tool_contract.py · +6 / −2</small></li><li>Independent test command completed<small>Example result: 12 passed · not live verification</small></li><li>Ready for evidence review<small>Agent completion does not accept the Anvil task.</small></li></ol>`,
    changes: `<pre>Sample diff / illustrative only

--- tests/tool_contract.py
++ tests/tool_contract.py
@@ response validation @@
- assert result
 assert result.tool_name == expected_name
 assert result.arguments == expected_arguments
 assert result.finish_reason == "tool_calls"</pre>`,
    tests: `${kv([
      ["Command", "pytest tests/tool_contract.py"],
      ["Example exit status", "0"],
      ["Example result", "12 passed"],
      ["Evidence source", "Runner process exit and test output"],
      ["Acceptance", "Independent review pending"],
    ])}<div class="notice">These are sample results, not tests executed by Pi in this session.</div>`,
  };
  return (
    intro(
      "PI / AGENT SESSIONS",
      "A coding agent, with a workspace.",
      "Follow the work packet, tool activity, changes, and evidence.",
      btn("Review new Pi session", "sandbox", "primary"),
    ) +
    `<section class="focus-card"><div class="eyebrow">SAMPLE SESSION / PI-008</div><div class="focus-top"><div><h2>Validate long-context tool behavior</h2><p>PRD-017 / T-04 · isolated checkout · explicit model connection</p></div>${pill(state.sandbox ? "Local plan retained" : "Example session", "warn")}</div></section>` +
    panel(
      "Environment",
      kv([
        ["Agent", "Pi coding agent"],
        ["Model connection", "Anvil router / llm.research"],
        ["Workspace", "Ephemeral task checkout"],
        ["Resources", "2 CPUs / 4 GiB / 30 minutes"],
        ["Tokens / context", "Provider usage and compaction checkpoints"],
        ["State", "No real session running"],
      ]),
    ) +
    `<div class="tabs" aria-label="Pi session sections">${["activity", "changes", "tests"].map((x) => `<button data-session-tab="${x}" aria-pressed="${state.sessionTab === x}">${x[0].toUpperCase() + x.slice(1)}</button>`).join("")}</div>` +
    panel("Session evidence", panes[state.sessionTab]) +
    panel(
      "Next step",
      `<p class="section-copy">Review changes and independently captured test output before submitting task evidence. Actual sessions will expose steering, bounded cancellation, reconnect, and claim recovery.</p><div class="chips">${btn("Inspect linked task", "task-detail")}${btn("Attach experiment evidence", "attach")}</div>`,
    )
  );
}
function architecture() {
  return (
    intro(
      "DESIGN RECORD / PROPOSAL",
      "One desk. Clear ownership.",
      "The workbench composes the tools that already own the work.",
    ) +
    panel(
      "Recommended direction",
      `<div class="section-copy"><h3>An Anvil application with focused integrations</h3><p>Start with a distinct application boundary in this repository. Serving owns recipes and lifecycle. Anvil State owns PRDs, claims, and acceptance. Prometheus and Loki own telemetry. A separate runner owns sandbox sessions.</p><p>Open WebUI is a credible companion for rich chat and presets. Test an integration before taking on a permanent fork. A fork would still need the custom experiment, fleet, evidence, and PRD workspace you see here.</p><p><a href="DESIGN.md">Read the full design and fork comparison ↗</a></p></div>`,
    ) +
    panel(
      "Delivery sequence",
      `<ol class="timeline"><li>01 · Coherent read experience<small>Models, run inspection, fleet HUD, PRDs, and existing auth.</small></li><li>02 · Playground and repeatable experiments<small>Explicit connector, recipe and preset; bounded jobs and retained evidence.</small></li><li>03 · Agent sessions<small>Claims, isolated worktrees, resource budgets, event stream, and review.</small></li><li>04 · Packaging decision<small>Separate release only once integration contracts pass end-to-end.</small></li></ol>`,
    )
  );
}
function renderHUD() {
  const s = state.stale;
  $("#hud").innerHTML =
    `<div class="hud-head"><span class="eyebrow">SYSTEM HUD</span>${pill(s ? "Metrics stale" : "Sample", s ? "warn" : "neutral")}</div><section><div class="hud-title"><b>Compute host</b><span class="dot"></span></div><p>Owner ready · split allocation<br>Controller snapshot · 14:16 UTC</p>${[
      ["A", "72", "31.2", "48", "Atlas 32B"],
      ["B", "4", "6.8", "48", "Voice sample"],
    ]
      .map(
        ([id, use, used, total, name]) =>
          `<div class="gpu"><div class="gpu-head"><span>COMPUTE ${id}</span><b>${s ? "—" : use + "%"}</b></div><div class="bar"><span style="width:${s ? "0" : use}%"></span></div><div class="hud-kv"><span>VRAM reserved</span><b>${s ? "—" : used + " / " + total}</b></div><div class="hud-kv"><span>${name}</span><span>GiB</span></div>${s ? `<p>Last sample: ${use}% · ${used} GiB<br>45 seconds old</p>` : ""}</div>`,
      )
      .join(
        "",
      )}<p>Utilization and reserved memory are separate signals.</p></section><section><div class="hud-title"><b>Request pressure</b>${pill(s ? "Metrics stale" : "Sample", s ? "warn" : "neutral")}</div><p>Router / engine sample · ${s ? "45s old" : "14:16 UTC"}</p><div class="hud-kv"><span>Active requests</span><b>${s ? "—" : "1 / 2"}</b></div><div class="hud-kv"><span>Queued</span><b>${s ? "—" : "0"}</b></div><div class="hud-kv"><span>KV cache used</span><b>${s ? "—" : "42%"}</b></div><div class="hud-kv"><span>Temperature · GPU A</span><b>${s ? "—" : "61 °C"}</b></div><div class="hud-kv"><span>Power · GPU A</span><b>${s ? "—" : "218 W"}</b></div><a class="hud-link" href="#system">Inspect system & logs ↗</a></section><section><div class="hud-title"><b>Fleet coverage</b><span class="pill warn">Partial</span></div><div class="hud-kv"><span>Compute host</span><b>Owner ready</b></div><div class="hud-kv"><span>Harness host</span><b class="warn">Unavailable</b></div><p>1 of 2 declared owners reporting. Missing activity is unknown.</p></section><section><div class="eyebrow">LINKED WORK</div><p>PRD-017<br><span style="color:var(--text)">Qualify the research model</span></p><p>Anvil snapshot · 14:15 UTC</p><a class="hud-link" href="#work">1 ready task ↗</a></section><p class="mono">${s ? "Last sample: 45s ago" : "Synthetic snapshot · 14:16 UTC"}<br>HUD follows current state.<br>Run charts follow run time.</p>`;
  $("#scenario-toggle").textContent = s
    ? "Restore sample freshness"
    : "Show stale telemetry";
}
function navigate(page) {
  if (location.hash === `#${page}`) render();
  else location.hash = page;
}
function render() {
  const page = location.hash.slice(1) || "bench";
  state.page = [
    "bench",
    "experiments",
    "playground",
    "models",
    "work",
    "system",
    "sessions",
    "architecture",
  ].includes(page)
    ? page
    : "bench";
  const titles = {
    bench: "Workbench",
    experiments: "Experiments",
    playground: "Playground",
    models: "Models & recipes",
    work: "Anvil work",
    system: "System & logs",
    sessions: "Pi sessions",
    architecture: "Design & architecture",
  };
  $("#crumb").textContent = titles[state.page];
  document.title = `${titles[state.page]} · Anvil Workbench concept`;
  document.querySelectorAll("nav a").forEach((a) => {
    if (a.dataset.page === state.page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const views = {
    bench,
    experiments,
    playground,
    models,
    work,
    system,
    sessions,
    architecture,
  };
  $("#main").innerHTML = `<div class="appear">${views[state.page]()}</div>`;
  renderHUD();
  if (state.page === "playground") {
    for (const [id, key] of [
      ["model", "model"],
      ["connector", "connector"],
      ["preset", "preset"],
    ])
      $("#" + id).value = state[key];
    effectiveConfig();
  }
  if (state.page === "system") {
    $("#log-filter").value = state.logQuery;
    filterLogs(state.logQuery);
  }
}
function selectedTarget() {
  const catalog = [
    {
      connector: "Anvil router",
      model: "Atlas 32B",
      recipe: "atlas-fp8-r3",
      id: "atlas-r3",
      alias: "llm.research",
    },
  ];
  return (
    catalog.find(
      (target) =>
        target.connector === state.connector && target.model === state.model,
    ) || null
  );
}
function effectiveConfig() {
  const resolved = selectedTarget();
  const ready = resolved !== null;
  const target = $("#served-target");
  if (target) {
    target.textContent = ready
      ? "llm.research → atlas-r3 / sample compute owner"
      : "No available deployment for this selection";
  }
  const send = $("#prompt-form button[type=submit]");
  if (send) send.disabled = !ready;
  const chips = $("#target-chips");
  if (chips)
    chips.innerHTML = ready
      ? pill("Alias: llm.research", "neutral") +
        pill("atlas-fp8-r3", "neutral") +
        pill("No tools execute", "warn")
      : pill("Selected connector or recipe unavailable", "warn") +
        pill("No ready route", "warn");
  const el = $("#effective-config");
  if (el)
    el.textContent = JSON.stringify(
      {
        connector: state.connector,
        recipe: state.model === "Atlas 32B" ? "atlas-fp8-r3" : "orion-int4-r2",
        alias: resolved?.alias || null,
        served_target: resolved?.id || null,
        preset: state.preset,
        temperature: Number(state.temperature),
        max_tokens: Number(state.maxTokens),
        tools: "inspect only",
        mode: "synthetic",
      },
      null,
      2,
    );
}
function requestSnapshot(provenance = "unsent draft") {
  const resolved = selectedTarget();
  if (!resolved) return null;
  return {
    model: state.model,
    connector: state.connector,
    recipe: resolved.recipe,
    target: resolved.id,
    alias: resolved.alias,
    provenance,
    preset: state.preset,
    temperature: Number(state.temperature),
    max_tokens: Number(state.maxTokens),
    tools: "inspect only",
    schema: null,
    prompt: state.prompt,
    history: state.messages.map((message) => ({ ...message })),
    synthetic: true,
  };
}
function announce(text) {
  $("#announcer").textContent = text;
}
function openDialog(title, body, foot = "") {
  const d = $("#dialog");
  d.innerHTML = `<div class="dialog-head"><h2 id="dialog-title">${title}</h2><button class="small quiet" data-action="close" aria-label="Close dialog">✕</button></div><div class="dialog-body">${body}</div>${foot ? `<div class="dialog-foot">${foot}</div>` : ""}`;
  if (!d.open) d.showModal();
}
function closeDialog() {
  $("#dialog").close();
}
function filterLogs(query) {
  const lines = [
    "14:00:02 INFO  run EXP-042: recipe fingerprint captured",
    "14:01:08 INFO  protocol preflight passed",
    "14:08:00 INFO  phase context=32768 concurrency=1",
    "14:08:03 INFO  request admitted: alias=llm.research",
    "14:16:00 INFO  checks passed=32 pending=16",
    "14:16:01 WARN  run incomplete; qualification pending",
  ];
  $("#log-lines").innerHTML =
    lines
      .filter((l) => l.toLowerCase().includes(query.toLowerCase()))
      .map((l) => `<pre>${escape(l)}</pre>`)
      .join("") || '<p class="muted">No matching sample logs.</p>';
}
function commandPalette() {
  openDialog(
    "Find or do something",
    `<label>Search workspace<input id="command-search" placeholder="Try playground, experiment, PRD…" autofocus></label><div class="command-results" id="command-results"></div>`,
  );
  filterCommands("");
  $("#command-search").focus();
}
function filterCommands(q) {
  const commands = [
    ["Open playground", "playground"],
    ["Create experiment", "new-experiment"],
    ["Browse recipes and models", "models"],
    ["View PRDs and ready tasks", "work"],
    ["Inspect fleet health and logs", "system"],
    ["Open Pi coding sessions", "sessions"],
    ["Compare architecture options", "architecture"],
  ];
  $("#command-results").innerHTML =
    commands
      .filter(([label]) => label.toLowerCase().includes(q.toLowerCase()))
      .map(([label, a]) => btn(label + " ↗", a))
      .join("") || '<p class="muted">No matching action.</p>';
}
function action(name) {
  if (name.startsWith("inspect-")) {
    const id = name.slice(8);
    if (id === "EXP-042") {
      state.experimentTab = "overview";
      navigate("experiments");
      return;
    }
    const run = runs.find((r) => r.id === id);
    if (run) {
      openDialog(
        `${run.id} · ${run.name}`,
        `${kv([
          ["Model", run.model],
          ["Configuration", run.detail],
          ["State", run.status],
          ["Correctness", run.gate],
          [
            "Throughput",
            run.throughput === "—"
              ? "Not qualified"
              : run.throughput + " tok/s",
          ],
        ])}<p class="section-copy" style="margin-top:15px">Retained sample result. No live owner or benchmark was queried.</p>`,
        btn("Close", "close"),
      );
    } else {
      openDialog(
        "EXP-043 · Simulated submission",
        kv([
          ["Name", state.preview?.name || "Demo experiment"],
          ["Suite", state.preview?.suite || "Unknown"],
          ["Concurrency", state.preview?.concurrency || "Unknown"],
          ["Request limit", state.preview?.limit || "Unknown"],
          ["Result", "No model called; not benchmark evidence"],
        ]),
        btn("Close", "close"),
      );
    }
    return;
  }

  if (
    [
      "bench",
      "experiments",
      "playground",
      "models",
      "work",
      "system",
      "sessions",
      "architecture",
    ].includes(name)
  ) {
    closeDialog();
    navigate(name);
    return;
  }
  if (name === "close") {
    closeDialog();
    return;
  }
  if (name === "stale") {
    state.stale = !state.stale;
    renderHUD();
    if (state.page === "system") render();
    announce(
      state.stale
        ? "Telemetry is stale. Current measurements are unknown."
        : "Synthetic telemetry restored.",
    );
    return;
  }
  if (name === "new-experiment") {
    if (
      state.page === "playground" &&
      (state.model !== "Atlas 32B" || state.connector !== "Anvil router")
    ) {
      openDialog(
        "Selected model unavailable",
        '<p class="section-copy">Choose an available connector and recipe explicitly before creating a test.</p>',
        btn("Close", "close"),
      );
      return;
    }
    openDialog(
      "Create an experiment",
      `<p class="section-copy">A reusable test, pinned to an explicit recipe and workload.</p><form id="experiment-form"><div class="stack" style="margin-top:16px"><label>Experiment name<input id="experiment-name" required maxlength="100" value="Tool behavior check"></label><label>Target recipe<select id="experiment-model"><option>Atlas 32B / atlas-fp8-r3</option><option disabled>Orion 70B / offline — load separately</option></select></label><label>Test suite<select id="experiment-suite">${state.page === "playground" ? "<option>Saved playground request</option>" : ""}<option>Protocol and tool checks</option><option>Context window sweep</option><option>Throughput and latency</option></select></label><div class="form-grid"><label>Concurrency<input id="experiment-concurrency" type="number" min="1" max="2" value="1" required></label><label>Request limit<input id="experiment-limit" type="number" min="1" max="48" value="24" required></label></div><label class="inline-check"><input type="checkbox" checked disabled> Retain failures and incomplete results</label><div class="notice">Preview only. This design does not submit benchmark jobs.</div><button class="primary" type="submit">Review experiment</button></div></form>`,
    );
    return;
  }
  if (name === "simulate-run") {
    state.demoRun = true;
    closeDialog();
    navigate("experiments");
    announce("Demo run added locally. No benchmark was submitted.");
    return;
  }
  if (name === "attach") {
    openDialog(
      "Attach evidence to work",
      `<p class="section-copy">EXP-042 · incomplete sample run. Linking evidence does not satisfy the acceptance gate.</p><label style="margin-top:15px">Anvil task<select><option>PRD-017 / T-04 · Validate long-context tool behavior</option></select></label>`,
      btn("Cancel", "close") +
        btn("Attach in demo", "confirm-attach", "primary"),
    );
    return;
  }
  if (name === "confirm-attach") {
    state.attached = true;
    closeDialog();
    render();
    announce("Sample evidence linked locally. Anvil State was not changed.");
    return;
  }
  if (name === "sandbox") {
    openDialog(
      "Review agent environment",
      `<div class="chips">${pill("Proposal only", "warn")}${pill("Pi coding agent", "neutral")}</div><p class="section-copy">An agent session for PRD-017 / T-04. The backend must recheck approval, dependencies, and acquire an exclusive claim before launching.</p>${kv(
        [
          ["Workspace", "Isolated checkout for selected project"],
          ["Execution", "Pi coding agent / ephemeral container"],
          ["Budget", "2 CPUs · 4 GiB RAM · 30 minutes"],
          ["Model access", "Explicit selected connector and model"],
          ["Network", "Declared services only"],
          ["Host access", "No Docker socket; no GPU devices"],
          ["Context recovery", "Durable checkpoint plus artifact references"],
          ["Token budget", "Explicit session budget before launch"],
          ["Output", "Diff, tests, trace, artifacts"],
          ["Completion", "Submit evidence; independent review"],
        ],
      )}<div class="notice">No container will start in this prototype. Pi runs behind a dedicated runner service; the browser follows its structured events.</div>`,
      btn("Close", "close") +
        btn("Keep local session plan", "sandbox-plan", "primary"),
    );
    return;
  }
  if (name === "sandbox-plan") {
    state.sandbox = true;
    closeDialog();
    navigate("sessions");
    announce("Local sandbox plan retained. No environment started.");
    return;
  }
  if (name === "task-detail") {
    openDialog(
      "T-04 · Long-context tool behavior",
      `${kv([
        ["Parent PRD", "PRD-017 · approved"],
        ["State", "Ready / unclaimed"],
        ["Dependency", "T-03 accepted"],
        ["Acceptance", "Protocol correctness; retained raw evidence"],
      ])}<p class="section-copy" style="margin-top:14px">Execution selects a ready task or bounded bundle, acquires a lease through Anvil State, and follows its work packet. A whole PRD is not launched as an unchecked shell command.</p>`,
      btn("Review execution plan", "sandbox", "primary"),
    );
    return;
  }
  if (name === "load-recipe") {
    openDialog(
      "Review recipe load",
      `${kv([
        ["Candidate", "Orion 70B / orion-int4-r2"],
        ["Requested resources", "Compute A + Compute B"],
        ["Existing owners", "Atlas 32B; Voice sample"],
        ["Last quality result", "Failed"],
        ["Eligibility", "Blocked by existing reservations"],
      ])}<div class="notice">Loading would conflict with existing serves. Resolve ownership and qualification explicitly before any managed lifecycle action.</div>`,
      btn("Close", "close"),
    );
    return;
  }
  if (name === "recipe-config") {
    openDialog(
      "Request settings ≠ runtime settings",
      `<div class="section-copy"><h3>Request preset</h3><p>Temperature, output budget, system instructions, and allowed tools. Changes apply to the next explicit request.</p><h3 style="margin-top:20px">Recipe configuration</h3><p>Model revision, runtime image, quantization, context, concurrency, and hardware requirements. Validate changes against current ownership and review any restart before applying.</p></div>`,
      btn("Close", "close"),
    );
    return;
  }
  if (name === "connectors") {
    openDialog(
      "Connectors & presets",
      `<div class="section-copy"><h3>Connection</h3><p>Anvil router resolves declared aliases. Credentials stay with the backend. Additional providers are explicit connectors with independent grants.</p><h3 style="margin-top:18px">Presets</h3><p>Precise: temperature 0.2.<br>Creative: temperature 0.8.<br>Tool validation: temperature 0, tool definitions inspectable.</p><p style="margin-top:14px">Presets never load a recipe or silently select another model.</p></div>`,
      btn("Configure in playground", "playground", "primary"),
    );
    return;
  }
  if (name === "save-preset") {
    openDialog(
      "Request preset snapshot",
      `<pre>${escape(JSON.stringify({ name: state.preset, model: state.model, connector: state.connector, temperature: Number(state.temperature), max_tokens: Number(state.maxTokens) }, null, 2))}</pre><p class="section-copy" style="margin-top:14px">Settings are retained in this page session only. Persistent preset management is a proposed backend capability.</p>`,
      btn("Done", "close"),
    );
    return;
  }
  if (name === "artifact") {
    openDialog(
      "Inspect sample artifact",
      `<pre>${escape(JSON.stringify({ schema: "design-sample/v1", run: "EXP-042", synthetic: true, complete: false, correctness: { passed: 32, pending: 16 }, promotion: "not_requested", recipe: "atlas-fp8-r3" }, null, 2))}</pre>`,
      btn("Close", "close"),
    );
    return;
  }
  if (name === "chart-data") {
    openDialog(
      "Throughput · sample data",
      `<div class="table-scroll"><table><caption>Selected illustrative samples; not benchmark evidence</caption><thead><tr><th>TIME</th><th>CANDIDATE</th><th>BASELINE</th></tr></thead><tbody><tr><td>14:00</td><td>60 tok/s</td><td>69 tok/s</td></tr><tr><td>14:08</td><td>83 tok/s</td><td>75 tok/s</td></tr><tr><td>14:16</td><td>84.6 tok/s</td><td>74 tok/s</td></tr></tbody></table></div>`,
      btn("Close", "close"),
    );
    return;
  }
  if (name === "grafana") {
    openDialog(
      "Grafana context",
      `${kv([
        ["Host", "Selected sample compute host"],
        ["Serve", "atlas-fp8-r3"],
        ["Time window", "EXP-042 start → end"],
        ["Authorization", "Grafana checks its own session"],
      ])}<p class="section-copy" style="margin-top:15px">The live adapter should generate an allowlisted deep link with these filters. This preview has no configured Grafana destination.</p>`,
      btn("Close", "close"),
    );
  }
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-action]");
  if (a) action(a.dataset.action);
  const st = e.target.closest("[data-session-tab]");
  if (st) {
    state.sessionTab = st.dataset.sessionTab;
    render();
    document.querySelector(`[data-session-tab="${state.sessionTab}"]`).focus();
  }
  const t = e.target.closest("[data-tab]");
  if (t) {
    state.experimentTab = t.dataset.tab;
    render();
    document.querySelector(`[data-tab="${state.experimentTab}"]`).focus();
  }
});
document.addEventListener("input", (e) => {
  if (e.target.id === "command-search") filterCommands(e.target.value);
  if (e.target.id === "log-filter") {
    state.logQuery = e.target.value;
    filterLogs(state.logQuery);
  }
  const keys = {
    prompt: "prompt",
    temperature: "temperature",
    "max-tokens": "maxTokens",
  };
  if (keys[e.target.id]) {
    state[keys[e.target.id]] = e.target.value;
    effectiveConfig();
  }
});
document.addEventListener("change", (e) => {
  const keys = { model: "model", connector: "connector", preset: "preset" };
  if (keys[e.target.id]) {
    state[keys[e.target.id]] = e.target.value;
    if (e.target.id === "preset") {
      state.temperature = {
        Precise: "0.2",
        Creative: "0.8",
        "Tool validation": "0",
      }[state.preset];
      $("#temperature").value = state.temperature;
    }
    effectiveConfig();
  }
});
document.addEventListener("submit", (e) => {
  if (e.target.id === "prompt-form") {
    e.preventDefault();
    if (!$("#temperature").reportValidity()) return;
    if (state.model !== "Atlas 32B" || state.connector !== "Anvil router") {
      openDialog(
        "Selected model unavailable",
        '<p class="section-copy">This sample connector or recipe is offline. Choose an available target explicitly. The request is not sent to a substitute model.</p>',
        btn("Close", "close"),
      );
      return;
    }
    const prompt = $("#prompt").value.trim();
    if (!prompt) return;
    state.lastRequest = requestSnapshot("submitted demo request");
    state.messages.push(
      { role: "user", text: prompt },
      {
        role: "assistant",
        text: "This is a fixed demonstration response, not an answer generated for your message.\n\nReserved GPU memory can hold model weights and a preallocated KV cache even while no kernels are executing. Utilization measures current compute activity. Read both signals alongside the request queue to distinguish a loaded model from a busy one.",
      },
    );
    state.prompt = "";
    render();
    $("#prompt").focus();
    announce("Canned demonstration response received. No model was called.");
  }
  if (e.target.id === "experiment-form") {
    e.preventDefault();
    state.preview = {
      name: $("#experiment-name").value,
      suite: $("#experiment-suite").value,
      concurrency: $("#experiment-concurrency").value,
      limit: $("#experiment-limit").value,
      session:
        state.page === "playground"
          ? state.prompt.trim()
            ? requestSnapshot()
            : state.lastRequest || requestSnapshot()
          : null,
    };
    openDialog(
      "Review experiment",
      `${kv([
        ["Name", state.preview.name],
        ["Recipe", "atlas-fp8-r3"],
        ["Suite", state.preview.suite],
        ["Concurrency", state.preview.concurrency],
        ["Request limit", state.preview.limit],
        [
          "Source",
          state.preview.session
            ? "Playground session / request settings captured"
            : "Declared test suite",
        ],
        ["Effect", "Request-only; no runtime changes"],
        ["Evidence", "Retain failures and incomplete results"],
      ])}${state.preview.session ? `<details open style="margin-top:15px"><summary class="text-link">Captured request snapshot</summary><pre>${escape(JSON.stringify(state.preview.session, null, 2))}</pre></details>` : ""}<div class="notice">Synthetic preview. A production preview must also bind current owner state, policy, and configuration digests with an expiry.</div>`,
      btn("Cancel", "close") +
        btn("Simulate submission", "simulate-run", "primary"),
    );
  }
});
$(".skip").addEventListener("click", (e) => {
  e.preventDefault();
  $("#main").focus();
});
$("#command-open").addEventListener("click", commandPalette);
$("#scenario-toggle").addEventListener("click", () => action("stale"));
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    commandPalette();
  }
});
window.addEventListener("hashchange", () => {
  render();
  $("#main").focus();
});
render();
