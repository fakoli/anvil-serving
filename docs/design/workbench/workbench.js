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
  compute: "compute",
  flowStep: -1,
  flowPaused: false,
  doc: "overview",
  editedRecipe: null,
  recipeReview: null,
  accessReview: null,
  users: [
    {
      id: "operator",
      name: "Operator",
      admin: true,
      enabled: true,
      resources: ["workbench", "gateway"],
    },
    {
      id: "researcher",
      name: "Researcher",
      admin: false,
      enabled: true,
      resources: ["workbench", "gateway"],
    },
  ],
  connectSessions: [
    {
      id: "browser-017",
      user: "researcher",
      type: "Browser",
      resource: "workbench",
      expiry: "18:00 UTC",
      revoked: false,
    },
    {
      id: "terminal-021",
      user: "researcher",
      type: "Terminal",
      resource: "gateway",
      parent: "browser-017",
      expiry: "18:30 UTC",
      revoked: false,
    },
  ],
};

const computeLocations = {
  compute: {
    name: "Compute host",
    kind: "NVIDIA · two discrete GPUs",
    cpu: "18%",
    memory: "28 / 96 GiB RAM",
    utilization: "72% GPU A",
    ready: true,
  },
  macbook: {
    name: "MacBook",
    kind: "Apple Silicon · unified memory",
    cpu: "12%",
    memory: "14 / 64 GiB unified",
    utilization: "4% GPU",
    ready: true,
  },
  harness: {
    name: "Harness host",
    kind: "Agent / client · owner unavailable",
    cpu: "Unknown",
    memory: "Unknown",
    utilization: "Unknown",
    ready: false,
  },
};
const recipes = [
  {
    model: "Atlas 32B",
    id: "atlas-fp8-r3",
    location: "compute",
    engine: "Engine A · demo-3",
    precision: "FP8",
    context: 32768,
    concurrency: 1,
    revision: 3,
    memory: "31.2 GiB VRAM",
    gate: "Prior sample gate passed",
  },
  {
    model: "Orion 70B",
    id: "orion-int4-r2",
    location: "compute",
    engine: "Engine A · demo-3",
    precision: "INT4",
    context: 65536,
    concurrency: 2,
    revision: 2,
    memory: "Two GPUs required",
    gate: "Last sample gate failed",
  },
  {
    model: "Finch 8B",
    id: "finch-mlx-r1",
    location: "macbook",
    engine: "MLX adapter · demo-1",
    precision: "4-bit",
    context: 8192,
    concurrency: 1,
    revision: 1,
    memory: "6 GiB unified memory",
    gate: "Unqualified candidate",
  },
];
const deployments = { "Atlas 32B": { ...recipes[0] } };
const options = (items, selected) =>
  items
    .map(
      ([value, label]) =>
        `<option value="${escape(value)}" ${value === selected ? "selected" : ""}>${escape(label)}</option>`,
    )
    .join("");
const flowSteps = [
  "Resolve target",
  "Preflight",
  "Measure",
  "Collect evidence",
  "Review",
];
const flowBusy = () =>
  state.demoRun &&
  state.flowStep >= 0 &&
  state.flowStep < 4 &&
  !state.flowPaused;
function contextSelect() {
  return options(
    Object.entries(computeLocations).map(([id, c]) => [id, c.name]),
    state.compute,
  );
}

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
  `<button type="button" class="${style}" data-action="${action}">${text}</button>`;
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
  return `<section class="panel"><div class="panel-head"><div><h2>Performance over the run</h2><p>Decode throughput · tokens / second · synthetic samples</p></div><div class="legend"><span><i></i>Candidate</span><span><i class="blue"></i>Baseline</span></div></div><div class="panel-body"><svg class="chart" viewBox="0 0 650 185" preserveAspectRatio="none" role="img" aria-label="Synthetic throughput increases from 60 to 85 tokens per second. Baseline remains near 73. An annotation marks the 32k context phase."><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#28c7d7" stop-opacity=".16"/><stop offset="100%" stop-color="#28c7d7" stop-opacity="0"/></linearGradient></defs><path class="grid" d="M36 20H638M36 64H638M36 108H638M36 152H638"/><text x="5" y="24">100</text><text x="13" y="68">80</text><text x="13" y="112">60</text><text x="13" y="156">40</text><path d="M36 108L70 94L105 102L138 83L172 90L205 68L239 75L272 57L305 65L339 53L372 62L405 48L439 55L472 42L506 51L539 46L573 58L606 50L638 54L638 152H36Z" fill="url(#chart-fill)"/><path class="baseline" d="M36 88L70 84L105 87L138 78L172 85L205 80L239 82L272 76L305 83L339 75L372 80L405 73L439 80L472 77L506 74L539 79L573 72L606 80L638 77"/><path class="trace" d="M36 108L70 94L105 102L138 83L172 90L205 68L239 75L272 57L305 65L339 53L372 62L405 48L439 55L472 42L506 51L539 46L573 58L606 50L638 54"/><path d="M372 12V152" stroke="#f5ad35" stroke-dasharray="3 5" opacity=".7"/><text x="380" y="23">32k context phase</text><text x="36" y="177">14:00</text><text x="177" y="177">14:04</text><text x="322" y="177">14:08</text><text x="466" y="177">14:12</text><text x="608" y="177">14:16</text></svg></div><div class="chart-foot"><span>Historical run window · 16 minutes</span><button class="small quiet" data-action="chart-data">View sample data</button></div></section>`;
}
function runTable() {
  const items = state.demoRun
    ? [
        {
          id: "EXP-043",
          name: escape(state.preview?.name || "New test preview"),
          model: escape(state.preview?.target.model || "Unknown"),
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
      "WORKBENCH",
      "A place for the whole experiment.",
      "Plan the test, follow the run, and keep its evidence together.",
      btn("Open playground", "playground") +
        btn("+ New experiment", "new-experiment", "primary"),
    ) +
    (state.experimentTab === "flow"
      ? `<div class="run-focus"><div><div class="eyebrow">${state.demoRun ? "DEMO PLAN / EXP-043" : "NEW WORK / DETERMINISTIC RUNNER"}</div><h2>${state.demoRun ? escape(state.preview.name) : "From a fixed plan to evidence"}</h2><p class="section-copy">${state.demoRun ? escape(state.preview.target.model + " · " + state.preview.target.locationName) : "Select the target above, then review the test plan."}</p></div>${pill("Simulation only", "warn")}</div>`
      : state.experimentTab === "runs"
        ? ""
        : `<div class="run-focus"><div><div class="eyebrow">PINNED RUN / EXP-042</div><h2>Context window sweep</h2><p class="section-copy">Atlas 32B · Compute host · 8k → 32k context</p><small>Historical sample · 14:00–14:16 UTC · 32 of 48 checks complete</small></div>${pill("Qualification pending", "warn")}</div><p class="run-context">This run retains its original target. The compute selector scopes new work and current instruments.</p>`) +
    experiments()
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
      );
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
  if (state.experimentTab === "runs") body = runTable();
  if (state.experimentTab === "flow") body = deterministicFlow();
  return (
    `<div class="tabs" role="tablist" aria-label="Workbench run sections">${["overview", "flow", "compare", "evidence", "events", "runs"].map((x) => `<button role="tab" id="run-tab-${x}" data-tab="${x}" aria-controls="run-content" aria-selected="${state.experimentTab === x}" tabindex="${state.experimentTab === x ? 0 : -1}">${{ flow: "Run flow", runs: "All runs" }[x] || x[0].toUpperCase() + x.slice(1)}</button>`).join("")}</div>` +
    `<div id="run-content" role="tabpanel" aria-labelledby="run-tab-${state.experimentTab}" tabindex="0">${body}</div>`
  );
}
function deterministicFlow() {
  const active = state.demoRun;
  return panel(
    "Deterministic evaluation",
    `
    <p class="section-copy">A fixed test plan drives each phase. The model under test supplies responses; it does not choose the next operation or judge its own result.</p>
    ${kv([
      ["Plan", active ? state.preview.name : "No plan submitted"],
      [
        "Target",
        active
          ? `${state.preview.target.model} / ${state.preview.target.recipe} / ${state.preview.target.locationName}`
          : "Choose a target above, then create an experiment",
      ],
      ["Driver", "Anvil Serving evaluation runner · fixed commands"],
      [
        "Agent assistance",
        "Optional cloud operator in Pi; separate from the target",
      ],
      [
        "Workload",
        active
          ? `${state.preview.suite} · concurrency ${state.preview.concurrency} · limit ${state.preview.limit}`
          : "Reviewed before submission",
      ],
    ])}
    <ol class="flow-steps">${flowSteps.map((name, i) => `<li class="${active && i < state.flowStep ? "done" : active && i === state.flowStep ? "current" : ""}"><span class="mono">0${i + 1}</span><b>${name}</b><small>${active ? (i < state.flowStep ? "Sample complete" : i === state.flowStep ? (state.flowPaused ? "Paused" : i === 4 ? "Independent review pending" : "Simulating this phase") : "Waiting") : "Not started"}</small></li>`).join("")}</ol>
    <div class="action-row">${!active ? btn("Create experiment", "new-experiment", "primary") : state.flowStep < 4 ? `<button class="primary" data-action="flow-advance" ${state.flowPaused ? "disabled" : ""}>Advance demo phase</button>` + btn(state.flowPaused ? "Resume demo" : "Pause demo", "flow-pause") : pill("Evidence retained · review pending", "warn")}</div>
    <p class="muted" style="font-size:12px;margin-top:15px">Manual demo progression. No requests run and no result is qualified. A real failure stops dependent phases and retains partial evidence.</p>`,
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
      `<div class="form-grid"><label>Connector<select id="connector"><option>Anvil router</option><option>Evaluation gateway · not connected</option></select></label><label>Recipe / model<select id="model">${options(
        recipes.map((r) => [r.model, r.model]),
        state.model,
      )}</select></label><label>Request preset<select id="preset"><option>Precise</option><option>Creative</option><option>Tool validation</option></select></label><label>Temperature<input id="temperature" type="number" min="0" max="2" step="0.1" value="${escape(state.temperature)}"></label></div><p class="eyebrow" style="margin-top:15px">EXACT SERVED TARGET</p><p id="served-target" class="mono"></p><div class="chips" id="target-chips"></div><details><summary class="text-link">Inspect effective request configuration</summary><pre id="effective-config"></pre></details>`,
    ) +
    panel(
      "Conversation",
      `<div id="messages">${messages()}</div><form id="prompt-form"><label for="prompt">Message</label><textarea id="prompt" required maxlength="8000">${escape(state.prompt)}</textarea><div class="form-grid" style="margin-top:12px"><label>Maximum output tokens<input id="max-tokens" type="number" min="1" max="4096" required value="${escape(state.maxTokens)}"></label><div class="intro-actions" style="align-items:end;justify-content:flex-end"><button class="primary" type="submit">Send demo request ↑</button></div></div><p class="muted" style="font-size:12px;margin-top:12px">This preview returns a canned response. Nothing is sent to a model.</p></form>`,
    ) +
    panel(
      "From a conversation to an experiment",
      `<p class="section-copy">Keep the exact request, selected recipe, and configuration together. Turn a useful prompt into a repeatable test case.</p><div class="chips">${btn("Create test from this session", "new-experiment")}${btn("Open task agent session", "work/task/T-04/agent")}</div>`,
    )
  );
}
function messages() {
  return state.messages.length
    ? state.messages
        .map(
          (m) =>
            `<div class="chat-message"><span class="eyebrow">${m.role === "user" ? "YOU" : escape(m.model || "SAMPLE MODEL") + " / CANNED DEMO RESPONSE"}</span><p>${escape(m.text)}</p>${m.role === "assistant" ? '<div class="response-meta"><span>Example TTFT 248 ms</span><span>Example speed 84.6 tok/s</span><span>Tool execution: off</span></div>' : ""}</div>`,
        )
        .join("")
    : `<div class="empty">A conversation with the instruments still in view.<br><small>Start with the sample question or enter your own.</small></div>`;
}
function models() {
  const draft = state.editedRecipe;
  return (
    intro(
      "MODEL CATALOG",
      "Recipes you can work with.",
      "Edit a saved configuration, review placement, then load it through Anvil Serving.",
      btn("Connectors & presets", "connectors"),
    ) +
    (draft
      ? panel(
          "Saved recipe candidate",
          `${kv([
            ["Recipe", draft.id],
            ["Model", draft.model],
            ["Placement", computeLocations[draft.location].name],
            [
              "Context / concurrency",
              `${draft.context} / ${draft.concurrency}`,
            ],
            [
              "Deployment",
              deployments[draft.model]?.id === draft.id
                ? "Loaded in demo · unqualified"
                : "Candidate saved · not loaded",
            ],
          ])}<div class="action-row">${btn("Edit candidate", "edit-draft")}${btn("Review candidate load", "load-draft", "primary")}</div>`,
        )
      : "") +
    `<div class="split">${recipes
      .map(
        (r, i) =>
          `<section class="panel model-card"><div class="panel-body"><div class="eyebrow">${computeLocations[r.location].name.toUpperCase()}</div><div class="model-mark">${escape(r.model.split(" ").at(-1))}</div><h2>${escape(r.model)}</h2><small>${escape(r.id)}</small><div class="chips">${pill(deployments[r.model]?.id === r.id ? "Loaded in demo" : "Not loaded", deployments[r.model]?.id === r.id ? "" : "neutral")}</div>${kv(
            [
              ["Memory requirement", r.memory],
              ["Configured context", r.context.toLocaleString() + " tokens"],
              ["Runtime", r.engine],
              ["Qualification", r.gate],
              ["Active revision", deployments[r.model]?.id || "None"],
            ],
          )}<div class="action-row">${btn("Select target", `select-recipe-${i}`, "primary")}${btn("Edit recipe", `edit-recipe-${i}`)}${btn("Review load", `load-recipe-${i}`)}</div></div></section>`,
      )
      .join("")}</div>` +
    panel(
      "One model, explicit placement",
      `<p class="section-copy">The MacBook is a compute location with unified memory and sample speech services. Placement and runtime requirements belong to the recipe. A load request goes to its declared resource owner.</p><p class="section-copy" style="margin-top:12px">Every host, capacity, deployment, and model on this page is synthetic.</p>`,
    )
  );
}
function editRecipe(recipe) {
  state.editingRecipe = { ...recipe };
  openDialog(
    "Edit recipe",
    `<p class="section-copy">Editable field projection for this design. Saving creates a candidate; the loaded deployment keeps its current configuration.</p><form id="recipe-form"><div class="stack" style="margin-top:15px">${kv(
      [
        ["Source recipe", recipe.id],
        ["Model / runtime", `${recipe.model} / ${recipe.engine}`],
      ],
    )}<div class="form-grid"><label>Context tokens<input id="recipe-context" type="number" min="1024" max="131072" step="1024" value="${recipe.context}" required></label><label>Concurrency<input id="recipe-concurrency" type="number" min="1" max="4" value="${recipe.concurrency}" required></label></div><label>Compute location<select id="recipe-location">${options(
      Object.entries(computeLocations)
        .filter(([id]) => id !== "harness")
        .map(([id, c]) => [id, c.name]),
      recipe.location,
    )}</select></label><details><summary class="text-link">Inspect source projection</summary><pre>${escape(JSON.stringify(recipe, null, 2))}</pre></details><p id="recipe-error" class="field-error" role="status"></p><button type="submit" class="primary">Review recipe changes</button></div></form>`,
  );
}
function reviewLoad(recipe) {
  state.recipeReview = { ...recipe };
  const identical = deployments[recipe.model]?.id === recipe.id;
  const blocked = recipe.location === "compute" || !!deployments["Finch 8B"];
  openDialog(
    "Review managed recipe load",
    `${kv([
      ["Recipe", recipe.id],
      ["Resource owner", computeLocations[recipe.location].name],
      ["Model / runtime", `${recipe.model} / ${recipe.engine}`],
      ["Context / concurrency", `${recipe.context} / ${recipe.concurrency}`],
      ["Operation", "Anvil Serving · models recipes load"],
      [
        "Current ownership",
        recipe.location === "compute"
          ? "GPU A: Atlas · GPU B: reserved deployment"
          : "Speech services · separate sample allocation",
      ],
      [
        "Resource check",
        identical
          ? "Already loaded · no change"
          : blocked
            ? "Existing reservation requires reconciliation"
            : "Sample capacity available; qualification remains pending",
      ],
    ])}<div class="notice">${identical ? "This exact recipe is already loaded in the demo." : blocked ? "A resource owner must resolve existing reservations before a replacement load. No implicit unload is included." : "Simulation only. The real owner must validate capacity, revision, policy, and preview expiry before loading."}</div>`,
    btn("Close", "close") +
      (!blocked && !identical
        ? btn("Simulate recipe load", "confirm-recipe-load", "primary")
        : ""),
  );
}
function work() {
  if (state.workTask) return taskWorkspace();
  return (
    intro(
      "ANVIL STATE / SELECTED PROJECT",
      "Work, with its context intact.",
      "Start from a PRD, open a ready task, and work with Pi inside that task.",
    ) +
    `<div class="work-summary"><span><b>02</b> Open PRDs</span><span><b>01</b> Ready task</span><span><b>02 / 06</b> Tasks accepted</span><span class="pill neutral">Sample project state</span></div>` +
    `<div class="split work-prds"><section class="panel"><div class="panel-head"><div><div class="eyebrow">PRD-017</div><h2>Qualify the research model</h2></div>${pill("Approved")}</div><div class="panel-body"><p class="section-copy">Correct tool behavior at the declared context and concurrency, backed by independent evidence.</p><div class="work-prd-progress"><div class="bar"><span style="width:33.3%"></span></div><small>2 of 6 tasks accepted · 1 ready to start</small></div></div></section><section class="panel"><div class="panel-head"><div><div class="eyebrow">PRD-018</div><h2>Repeatable model experiments</h2></div>${pill("Draft", "neutral")}</div><div class="panel-body"><p class="section-copy">A reusable evaluation plan with explicit targets, comparable results, and retained failure evidence.</p><p class="section-copy" style="margin-top:18px">Waiting for review before tasks become executable.</p></div></section></div>` +
    panel(
      "Ready to work",
      `<div class="task-row"><span class="index-number">T-04</span><div><h3>Validate long-context tool behavior</h3><p>PRD-017 · dependencies accepted · unclaimed in sample</p><small>Open the task to review its work packet, talk to Pi, and inspect the resulting evidence.</small></div>${btn("Open task ↗", "work/task/T-04/overview", "primary")}</div>`,
    ) +
    `<div class="settings-note"><span aria-hidden="true">↳</span><p>Pi works within the selected task. Anvil keeps the approval, claim, work packet and acceptance record; the agent supplies changes and independently captured execution evidence.</p><a href="#docs" class="text-link">Read about Anvil State ↗</a></div>`
  );
}
function taskWorkspace() {
  let content = "";
  if (state.workView === "agent")
    content = PiSurface.render({ embedded: true });
  if (state.workView === "overview")
    content = `<div class="split">${panel("Work packet", `<p class="section-copy">Validate the model’s tool-call behavior across the approved context windows. Use the fixed evaluation suite, preserve malformed responses, and retain the exact recipe and request parameters.</p><div class="task-acceptance"><h3>Acceptance criteria</h3><ul><li>Correct tool name, arguments and finish reason at each declared context.</li><li>Captured process output and raw artifacts, including failed and partial runs.</li><li>Independent review of the evidence before task acceptance.</li></ul></div><div class="action-row">${btn("Open agent session", "work/task/T-04/agent", "primary")}${btn("Review environment", "sandbox")}</div>`)}${panel(
      "Task record",
      kv([
        ["Project", "Selected sample workspace"],
        ["PRD", "PRD-017 · approved revision 3"],
        ["Task", "T-04"],
        ["Dependency", "T-03 · accepted"],
        ["Claim", "Unclaimed in sample"],
        ["Environment", "Isolated task checkout"],
        [
          "Evidence",
          state.attached ? "EXP-042 attached · incomplete" : "No run linked",
        ],
        ["Acceptance", "Independent review required"],
      ]),
    )}</div>`;
  if (state.workView === "evidence")
    content = panel(
      "Task evidence",
      `<p class="section-copy">Keep the agent’s conversation alongside independently captured results. An agent response alone does not satisfy the acceptance gate.</p>${kv(
        [
          [
            "Linked run",
            state.attached ? "EXP-042 · incomplete sample" : "Not attached",
          ],
          ["Changes", "No real files changed"],
          ["Test execution", "No real tests run by this prototype"],
          ["Review", "Pending independent evidence"],
        ],
      )}<div class="action-row">${btn("Attach run evidence", "attach", "primary")}${btn("Inspect sample artifact", "artifact")}${btn("Open Workbench", "bench")}</div>`,
    );
  return `<a class="task-back" href="#work">← All Anvil work</a><div class="task-workspace-heading"><div><div class="eyebrow">PRD-017 / TASK T-04</div><h1>Validate long-context tool behavior</h1><p>Qualify the research model · approved work packet · isolated task workspace</p></div><div class="task-heading-actions">${pill("Ready", "neutral")}${state.workView === "agent" ? btn("Review environment", "sandbox", "small quiet") : ""}</div></div><nav class="task-workspace-tabs" aria-label="Task views">${[
    ["overview", "Overview"],
    ["agent", "Agent session"],
    ["evidence", "Evidence"],
  ]
    .map(
      ([id, label]) =>
        `<a href="#work/task/T-04/${id}" ${state.workView === id ? 'aria-current="page"' : ""}>${id === "agent" ? '<span aria-hidden="true">π</span> ' : ""}${label}</a>`,
    )
    .join("")}</nav><div class="task-workspace-content">${content}</div>`;
}

function access(embedded = false) {
  return (
    (embedded
      ? ""
      : intro(
          "ANVIL CONNECT / ADMINISTRATION",
          "Access, with a clear owner.",
          "Manage resource grants and issued sessions from the same workspace.",
        )) +
    `<div class="notice">Sample administrator view. Live controls are available only to a configured Connect administrator. These actions update local demo state.</div>` +
    panel(
      "People & resource grants",
      `<div class="table-scroll"><table><thead><tr><th>PERSON</th><th>ACCESS</th><th>RESOURCES</th><th>MANAGE</th></tr></thead><tbody>${state.users.map((u) => `<tr><td><b>${u.name}</b><small>${u.admin ? "Configured administrator" : "Existing identity"}</small></td><td>${pill(u.enabled ? "Enabled" : "Disabled", u.enabled ? "" : "warn")}</td><td>${u.resources.join(", ")}</td><td>${btn("Edit grants", `grants-${u.id}`, "small quiet")}</td></tr>`).join("")}</tbody></table></div><p class="section-copy" style="margin-top:15px">Connect keeps the identity when access is disabled. Administrator membership and new identities are configured through enrollment.</p>`,
    ) +
    panel(
      "Issued sessions",
      `<p class="section-copy">These are authorizations, not an online-device inventory. Browser and terminal sessions keep their own expiry and resource scope.</p><div class="table-scroll"><table><thead><tr><th>SESSION</th><th>RESOURCE / EXPIRY</th><th>STATE</th><th>ACTION</th></tr></thead><tbody>${state.connectSessions.map((x) => `<tr><td><b>${x.id}</b><small>${x.type} · Researcher${x.parent ? " · approved by " + x.parent : ""}</small></td><td>${x.resource}<small>${x.expiry} · sample</small></td><td>${pill(x.revoked ? "Revoked" : "Issued", x.revoked ? "warn" : "neutral")}</td><td>${x.revoked ? "—" : btn("Disconnect", `revoke-${x.id}`, "small danger")}</td></tr>`).join("")}</tbody></table></div>`,
    ) +
    panel(
      "What revocation changes",
      `<p class="section-copy">Disconnect blocks new admissions and closes authorized streams. Revoking a browser session also revokes the terminal sessions it approved. Work already executing upstream may continue. An enabled user can sign in again through the identity provider.</p><div class="action-row">${btn("Read Connect access documentation", "doc-connect")}</div>`,
    )
  );
}
const docPages = {
  overview: {
    label: "Anvil Serving",
    title: "The local AI workspace",
    kicker: "PRODUCT OVERVIEW",
    body: `<p>Anvil Serving covers Model Serving, Capability Gateway, Evaluation & Evidence, Anvil Voice, Anvil Media, and Control Plane & Fleet. The router is one part of that product.</p><h3>Start from the work</h3><p>Choose a declared model and compute location. Use a managed recipe to describe its runtime. Explore behavior in Playground, retain repeatable tests in Workbench, and connect evidence to project work.</p><h3>Each tool keeps its authority</h3><p>Serving owns model operations. Anvil State owns PRDs, claims, and acceptance. Connect owns browser access. The Workbench brings those surfaces together.</p>`,
    source: "https://github.com/fakoli/anvil-serving/blob/main/README.md",
    sourceLabel: "Anvil Serving README",
  },
  recipes: {
    label: "Models & recipes",
    title: "A recipe is a reproducible runtime",
    kicker: "MODEL SERVING",
    body: `<p>A recipe captures a particular model and runtime configuration. A request preset captures how you ask the loaded model: temperature, output budget, instructions, and tool definitions.</p><h3>Edit, then load</h3><p>Save changes as a candidate recipe. Review its resource owner and conflicts before a managed load. Editing a recipe does not implicitly restart a running deployment.</p><h3>Use the managed lifecycle</h3><p>The CLI provides <code>models recipes load</code>, <code>status</code>, <code>logs</code>, and <code>unload</code>. The proposed editor must round-trip the canonical recipe schema through the Serving owner. This concept edits a small field projection only.</p>`,
    source: "https://github.com/fakoli/anvil-serving/blob/main/README.md",
    sourceLabel: "Anvil Serving README · model operations",
  },
  evaluation: {
    label: "Evaluation & evidence",
    title: "Make each result reproducible",
    kicker: "WORKBENCH",
    body: `<p>Capture the exact model, recipe revision, runtime, hardware, context, concurrency, and request constraints with every run. Keep incomplete and failed results.</p><h3>A deterministic path</h3><p>Resolve the target, run independent preflight, measure a fixed workload, collect evidence, then review. An optional cloud agent can help prepare a plan, while the evaluation runner executes the fixed steps against the explicit target.</p><h3>Compare like with like</h3><p>Different workload distributions and failed correctness gates cannot establish a speed improvement. Promotion requires independent evidence and a recorded human decision.</p>`,
    source:
      "https://github.com/fakoli/anvil-serving/blob/main/docs/BENCHMARKS.md",
    sourceLabel: "Benchmark documentation",
  },
  state: {
    label: "Anvil State",
    title: "Give work a durable record",
    kicker: "PROJECT WORK",
    body: `<p>Anvil is the system of record for agent software work: PRDs, tasks, dependencies and conflicts, leased claims, work packets, review gates, and acceptance proofs.</p><h3>Resolve the project</h3><p>The workspace must identify the exact checkout before it lists or executes project work. A PRD in draft is not yet executable. A runner acquires a valid claim on approved work before beginning it.</p><h3>Completion and acceptance</h3><p>Pi may produce changes and test output. Anvil retains the evidence and applies its independent review and acceptance gates. The UI uses the supported State CLI or MCP; it does not edit a state database.</p>`,
    source: "https://github.com/fakoli/anvil/blob/main/README.md",
    sourceLabel: "Anvil README",
  },
  connect: {
    label: "Connect access",
    title: "Manage grants and issued sessions",
    kicker: "ADMINISTRATION",
    body: `<p>Configured Connect administrators can change an existing user's resource grants, enable or disable access, and revoke issued browser or terminal sessions.</p><h3>Understand the effect</h3><p>Revoking a browser session invalidates terminal sessions it approved. It blocks admissions and closes streams, while an already executing upstream request may continue. Disabling access keeps the identity record.</p><h3>Review against current state</h3><p>Changes use a target generation and request identity. Stale state requires a fresh review; an uncertain result is reconciled before another attempt. The final enabled administrator with the admin resource cannot remove their own access.</p>`,
    source:
      "https://github.com/fakoli/anvil-serving/blob/main/docs/ANVIL-CONNECT-ACCESS.md",
    sourceLabel: "Connect access administration",
  },
  pi: {
    label: "Pi integration",
    title: "Reuse the agent workspace",
    kicker: "DESIGN PROPOSAL",
    body: `<p>Pi provides supported RPC and SDK integration boundaries for conversational sessions, model selection, thinking controls, tool events, and extension UI.</p><h3>Pi Web as the first integration spike</h3><p>The third-party Pi Web project already supplies a browser chat surface and many of those session controls. The proposal is to reuse it behind a dedicated session service, adding project context and navigation around it.</p><h3>Keep the ownership boundary</h3><p>A Pi UI is not an isolation mechanism. The session runner owns the environment and policy; Anvil owns work claims. Authentication, cancellation, reconnect, and extension compatibility still need an integration spike.</p>`,
    source: "https://github.com/agegr/pi-web",
    sourceLabel: "Pi Web source · third-party reuse candidate",
  },
};
function docs() {
  const d = docPages[state.doc];
  return (
    intro(
      "DOCUMENTATION",
      "The handbook, within reach.",
      "Read the product concepts alongside the work. These are curated summaries linked to their source docs.",
    ) +
    `<div class="docs-layout"><div class="docs-menu" role="group" aria-label="Documentation topics">${Object.entries(
      docPages,
    )
      .map(
        ([id, d]) =>
          `<button id="doc-selector-${id}" aria-controls="doc-content" data-action="doc-${id}" aria-pressed="${state.doc === id}">${d.label}</button>`,
      )
      .join(
        "",
      )}</div><article class="doc-article" id="doc-content" aria-labelledby="doc-selector-${state.doc}" aria-live="polite"><div class="eyebrow">${d.kicker}</div><h2>${d.title}</h2>${d.body}<p class="source-line">Source: <a href="${d.source}" target="_blank" rel="noreferrer">${d.sourceLabel} ↗</a><br>Design summary · September 10, 2026. Full versioned document rendering is proposed.</p></article></div>`
  );
}
function extraAction(name) {
  if (name.startsWith("compute-")) {
    state.compute = name.slice(8);
    render();
    announce("Selected " + computeLocations[state.compute].name);
    return true;
  }
  if (name === "open-flow") {
    state.experimentTab = "flow";
    navigate("bench");
    return true;
  }
  if (name === "flow-advance") {
    if (state.demoRun && !state.flowPaused && state.flowStep < 4)
      state.flowStep++;
    render();
    announce("Demo phase: " + flowSteps[state.flowStep]);
    return true;
  }
  if (name === "flow-pause") {
    state.flowPaused = !state.flowPaused;
    render();
    announce(state.flowPaused ? "Demo runner paused" : "Demo runner resumed");
    return true;
  }
  if (name.startsWith("doc-")) {
    state.doc = name.slice(4);
    if (!docPages[state.doc]) state.doc = "overview";
    if (state.page === "docs") {
      render();
      document.querySelector(`[data-action="doc-${state.doc}"]`).focus();
    } else navigate("docs");
    return true;
  }
  if (name.startsWith("select-recipe-")) {
    const r = recipes[Number(name.slice(14))];
    state.compute = r.location;
    state.model = r.model;
    state.connector = "Anvil router";
    navigate("playground");
    return true;
  }
  if (name.startsWith("edit-recipe-")) {
    editRecipe(recipes[Number(name.slice(12))]);
    return true;
  }
  if (name === "edit-draft") {
    editRecipe(state.editedRecipe);
    return true;
  }
  if (name.startsWith("load-recipe-")) {
    reviewLoad(recipes[Number(name.slice(12))]);
    return true;
  }
  if (name === "load-draft") {
    reviewLoad(state.editedRecipe);
    return true;
  }
  if (name === "save-recipe") {
    state.editedRecipe = { ...state.pendingRecipe };
    closeDialog();
    render();
    announce(
      "Recipe candidate saved locally. The running deployment was not changed.",
    );
    return true;
  }
  if (name === "confirm-recipe-load") {
    const r = state.recipeReview;
    deployments[r.model] = { ...r };
    state.model = r.model;
    state.compute = r.location;
    state.connector = "Anvil router";
    closeDialog();
    render();
    announce(
      "Recipe loaded in the local simulation. No real owner was called.",
    );
    return true;
  }
  if (name.startsWith("grants-")) {
    const u = state.users.find((x) => x.id === name.slice(7));
    state.editingUser = u.id;
    openDialog(
      "Edit resource grants",
      `<p class="section-copy">${u.name} · ${u.admin ? "last configured administrator" : "existing identity"}</p><form id="grants-form"><div class="grant-controls"><label><input type="checkbox" id="grant-enabled" ${u.enabled ? "checked" : ""}>Enable access</label></div><fieldset><legend>Allowed resources · at least one</legend><div class="grant-controls">${["workbench", "gateway"].map((x) => `<label><input type="checkbox" name="resource" value="${x}" ${u.resources.includes(x) ? "checked" : ""}>${x}</label>`).join("")}</div></fieldset><p id="grant-error" class="field-error" role="status"></p><div class="notice">${u.admin ? "The last administrator must remain enabled with access to the Workbench administration resource." : "Changes affect admissions. Disabling access retains the identity."}</div><button class="primary" type="submit">Review access change</button></form>`,
    );
    return true;
  }
  if (name === "confirm-grants") {
    const r = state.accessReview;
    const u = state.users.find((x) => x.id === r.id);
    u.enabled = r.enabled;
    u.resources = [...r.resources];
    closeDialog();
    render();
    announce(
      "Access grants updated in the demo. No Connect service was called.",
    );
    return true;
  }
  if (name.startsWith("revoke-")) {
    const id = name.slice(7);
    state.revokeIds = state.connectSessions
      .filter((x) => !x.revoked && (x.id === id || x.parent === id))
      .map((x) => x.id);
    openDialog(
      "Review session revocation",
      `${kv([
        ["Selected session", id],
        [
          "Also affected",
          state.revokeIds.filter((x) => x !== id).join(", ") ||
            "No linked sessions",
        ],
        ["Effect", "Block new admissions and close streams"],
        ["Already executing work", "May continue upstream"],
        ["Future sign-in", "Allowed if the user remains enabled"],
      ])}<div class="notice">This changes only the sample records. A production action binds the current session generation and reconciles uncertain outcomes.</div>`,
      btn("Cancel", "close") +
        btn("Revoke in demo", "confirm-revoke", "danger"),
    );
    return true;
  }
  if (name === "confirm-revoke") {
    state.connectSessions.forEach((x) => {
      if (state.revokeIds.includes(x.id)) x.revoked = true;
    });
    closeDialog();
    render();
    announce("Selected and linked session authorizations revoked in the demo.");
    return true;
  }
  return false;
}
function extraSubmit(e) {
  if (e.target.id === "recipe-form") {
    e.preventDefault();
    const from = state.editingRecipe;
    const target = $("#recipe-location").value;
    if (
      (from.engine.startsWith("MLX") && target !== "macbook") ||
      (!from.engine.startsWith("MLX") && target !== "compute")
    ) {
      $("#recipe-error").textContent =
        "The pinned runtime is incompatible with that compute location. Choose a compatible recipe.";
      return true;
    }
    const after = {
      ...from,
      gate: "Unqualified candidate",
      context: Number($("#recipe-context").value),
      concurrency: Number($("#recipe-concurrency").value),
      location: target,
      revision: from.revision + 1,
      id: from.id.replace(/-r\d+$/, `-r${from.revision + 1}`),
    };
    state.pendingRecipe = after;
    openDialog(
      "Review recipe changes",
      `${kv([
        ["Candidate", after.id],
        ["Context", `${from.context} → ${after.context}`],
        ["Concurrency", `${from.concurrency} → ${after.concurrency}`],
        ["Compute", computeLocations[target].name],
        ["Runtime effect", "None · save only"],
        ["Qualification", "New candidate requires evaluation"],
      ])}<div class="notice">This is a small editable schema projection. Production editing must validate the complete canonical recipe and preserve its secret references.</div>`,
      btn("Cancel", "close") +
        btn("Save candidate in demo", "save-recipe", "primary"),
    );
    return true;
  }
  if (e.target.id === "grants-form") {
    e.preventDefault();
    const u = state.users.find((x) => x.id === state.editingUser);
    const resources = Array.from(
      e.target.querySelectorAll('[name="resource"]:checked'),
    ).map((x) => x.value);
    const enabled = $("#grant-enabled").checked;
    if (
      !resources.length ||
      (u.admin && (!enabled || !resources.includes("workbench")))
    ) {
      $("#grant-error").textContent = u.admin
        ? "Keep the last administrator enabled with the Workbench resource."
        : "Choose at least one resource.";
      return true;
    }
    state.accessReview = { id: u.id, resources, enabled };
    openDialog(
      "Review access change",
      `${kv([
        ["Person", u.name],
        [
          "Access",
          `${u.enabled ? "Enabled" : "Disabled"} → ${enabled ? "Enabled" : "Disabled"}`,
        ],
        ["Resources before", u.resources.join(", ")],
        ["Resources after", resources.join(", ")],
        ["Identity", "Retained"],
      ])}`,
      btn("Cancel", "close") +
        btn("Apply in demo", "confirm-grants", "primary"),
    );
    return true;
  }
  return false;
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
      `<div class="section-copy"><h3>An Anvil application with focused integrations</h3><p>Start with a distinct application boundary in this repository. Serving owns recipes and lifecycle. Anvil State owns PRDs, claims, and acceptance. Prometheus and Loki own telemetry. A separate runner owns sandbox sessions.</p><p>Pi Web is the first reuse candidate for the conversational agent workspace. It already provides the requested Pi session and extension UI features. Run a bounded integration spike before taking on a fork. Open WebUI remains an optional chat companion; the experiments, fleet, Connect, and Anvil State workspace remains Anvil-owned.</p><p><a href="DESIGN.md">Read the full design and fork comparison ↗</a></p></div>`,
    ) +
    panel(
      "Delivery sequence",
      `<ol class="timeline"><li>01 · Coherent read experience<small>Models, run inspection, fleet HUD, PRDs, and existing auth.</small></li><li>02 · Playground and repeatable experiments<small>Explicit connector, recipe and preset; bounded jobs and retained evidence.</small></li><li>03 · Agent sessions<small>Claims, isolated worktrees, resource budgets, event stream, and review.</small></li><li>04 · Packaging decision<small>Separate release only once integration contracts pass end-to-end.</small></li></ol>`,
    )
  );
}
function renderHUD() {
  const c = computeLocations[state.compute];
  const unknown = state.stale || !c.ready;
  const executing =
    flowBusy() && state.preview?.target.location === state.compute;
  const pi = PiSurface.status();
  $("#hud").innerHTML =
    `<label>Compute<select id="compute-location">${contextSelect()}</select></label><label>Target<select id="scope-model">${options(
      recipes.map((r) => [r.model, r.model]),
      state.model,
    )}</select></label>
  <div class="instrument"><span>OWNER · 14:16 UTC</span><strong><i class="led ${c.ready ? "ready" : "unknown"}"></i>${c.ready ? "Ready" : "Unavailable"}</strong></div>
  <div class="instrument"><span>${unknown ? "TELEMETRY · STALE" : "SAMPLE · 14:16 UTC"}</span><strong>${unknown ? "Utilization unknown" : c.utilization}</strong><span>${unknown ? "Memory unknown" : c.memory}</span></div>
  <div class="instrument"><span>RUNNER · DEMO</span><strong><i class="led ${executing ? "active" : ""}"></i>${executing ? flowSteps[state.flowStep] : state.demoRun && state.preview?.target.location === state.compute && state.flowPaused ? "Paused" : "Idle"}</strong></div>
  <div class="instrument"><span>PI · CLOUD OPERATOR</span><strong><i class="led ${pi.busy ? "active" : ""}"></i>${escape(pi.label)}</strong></div>
  <a class="text-link small" href="#compute">Compute ↗</a>`;
  $("#scenario-toggle").textContent = state.stale
    ? "Restore sample freshness"
    : "Show stale telemetry";
}
function navigate(page) {
  if (location.hash === `#${page}`) render();
  else location.hash = page;
}
function render() {
  const prefs = SettingsSurface.preferences();
  let hash = location.hash.slice(1) || prefs.start;
  if (hash === "system") hash = "observability/fleet";
  if (hash === "access") hash = "settings/access";
  if (hash === "sessions") hash = "work/task/T-04/agent";
  if (["access", "sessions", "system"].includes(location.hash.slice(1)))
    history.replaceState(null, "", "#" + hash);
  const page = hash === "experiments" ? "bench" : hash.split("/")[0];
  state.settingsSection = hash.split("/")[1] || "general";
  state.observabilityDashboard = hash.split("/")[1] || "fleet";
  state.workTask =
    hash.split("/")[0] === "work" &&
    hash.split("/")[1] === "task" &&
    hash.split("/")[2] === "T-04";
  state.workView = ["overview", "agent", "evidence"].includes(
    hash.split("/")[3],
  )
    ? hash.split("/")[3]
    : "overview";
  state.page = [
    "bench",
    "experiments",
    "playground",
    "models",
    "work",
    "observability",
    "compute",
    "architecture",
    "settings",
    "docs",
  ].includes(page)
    ? page
    : "bench";
  const titles = {
    bench: "Workbench",
    experiments: "Experiments",
    playground: "Playground",
    models: "Models & recipes",
    work: "Anvil work",
    observability: "Observability",
    compute: "Compute",
    settings: "Settings",
    docs: "Documentation",
    architecture: "Design & architecture",
  };
  $("#crumb").textContent = titles[state.page];
  document.title = `${titles[state.page]} · Anvil Workbench concept`;
  document.querySelectorAll("[data-page]").forEach((a) => {
    if (a.dataset.page === state.page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const views = {
    bench,
    experiments,
    playground,
    models,
    work,
    observability: () => ObservatorySurface.render(observabilityApi()),
    compute: () => ComputeSurface.render(computeApi()),
    architecture,
    settings: () =>
      SettingsSurface.render(state.settingsSection, { access, kv, btn }),
    docs,
  };
  $("#main").innerHTML = `<div class="appear">${views[state.page]()}</div>`;
  document.body.dataset.page = state.page;
  document.body.dataset.taskView = state.workTask ? state.workView : "";
  document.body.dataset.density = prefs.density;
  $("#workspace-name").textContent = prefs.name;
  $("#breadcrumb-workspace").textContent = prefs.name;
  $(".brand").href = "#" + prefs.start;
  $("#hud").hidden = state.page === "settings" || !prefs.showHud;
  $("#scenario-toggle").hidden = state.page === "settings" || !prefs.showHud;
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
}
function selectedTarget() {
  const deployment = deployments[state.model];
  if (
    state.connector !== "Anvil router" ||
    !deployment ||
    deployment.location !== state.compute
  )
    return null;
  return {
    connector: state.connector,
    model: state.model,
    recipe: deployment.id,
    id: deployment.id + "-serve",
    alias: state.model === "Atlas 32B" ? "llm.research" : "llm.laptop",
    location: state.compute,
    locationName: computeLocations[state.compute].name,
  };
}
function effectiveConfig() {
  const resolved = selectedTarget();
  const ready = resolved !== null;
  const target = $("#served-target");
  if (target) {
    target.textContent = ready
      ? `${resolved.alias} → ${resolved.id} / ${resolved.locationName}`
      : "No available deployment for this selection";
  }
  const send = $("#prompt-form button[type=submit]");
  if (send) send.disabled = !ready;
  const chips = $("#target-chips");
  if (chips)
    chips.innerHTML = ready
      ? pill("Alias: " + resolved.alias, "neutral") +
        pill(resolved.recipe, "neutral") +
        pill("No tools execute", "warn")
      : pill("Selected connector or recipe unavailable", "warn") +
        pill("No ready route", "warn");
  const el = $("#effective-config");
  if (el)
    el.textContent = JSON.stringify(
      {
        connector: state.connector,
        recipe: resolved?.recipe || null,
        location: state.compute,
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
    location: resolved.location,
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
let dialogOpener = null;
let dialogOpenerSelector = "";
function openDialog(title, body, foot = "") {
  const d = $("#dialog");
  if (!d.open) {
    dialogOpener = document.activeElement;
    dialogOpenerSelector = dialogOpener?.id
      ? "#" + CSS.escape(dialogOpener.id)
      : dialogOpener?.dataset.action
        ? `[data-action="${CSS.escape(dialogOpener.dataset.action)}"]`
        : "";
  }
  d.innerHTML = `<div class="dialog-head"><h2 id="dialog-title" tabindex="-1">${escape(title)}</h2><button type="button" class="small quiet" data-action="close" aria-label="Close dialog">✕</button></div><div class="dialog-body">${body}</div>${foot ? `<div class="dialog-foot">${foot}</div>` : ""}`;
  if (!d.open) d.showModal();
  $("#dialog-title").focus();
}
function closeDialog() {
  $("#dialog").close();
}
$("#dialog").addEventListener("close", () => {
  const dialog = $("#dialog");
  if (dialog.open) return;
  const active = document.activeElement;
  // A completed action may already have focused its new page or thread.
  if (
    active &&
    active !== document.body &&
    active !== $("#main") &&
    !dialog.contains(active)
  )
    return;
  const opener = dialogOpener?.isConnected
    ? dialogOpener
    : dialogOpenerSelector
      ? document.querySelector(dialogOpenerSelector)
      : null;
  (opener || $("#main"))?.focus({ preventScroll: true });
});
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
    ["Inspect observability dashboards", "observability/fleet"],
    ["Manage hosts, workloads and logs", "compute"],
    ["Open Anvil task agent session", "work/task/T-04/agent"],
    ["Open workspace settings", "settings/general"],
    ["Manage Connect grants and sessions", "settings/access"],
    ["Read Anvil documentation", "docs"],
    ["Open deterministic run flow", "open-flow"],
    ["Compare architecture options", "architecture"],
  ];
  $("#command-results").innerHTML =
    commands
      .filter(([label]) => label.toLowerCase().includes(q.toLowerCase()))
      .map(([label, a]) => btn(label + " ↗", a))
      .join("") || '<p class="muted">No matching action.</p>';
}
function settingsApi() {
  return { render, announce, openDialog, closeDialog, btn, kv };
}
function observabilityApi() {
  return {
    dashboard: state.observabilityDashboard,
    location: state.compute,
    stale: state.stale,
    readyModels: () => Object.keys(deployments).length,
    render,
    openDialog,
    kv,
    btn,
  };
}
function computeApi() {
  return {
    location: state.compute,
    setLocation: (id) => {
      state.compute = id;
    },
    getDeployment: (model) => deployments[model] || null,
    getRecipe: (model) =>
      state.editedRecipe?.model === model
        ? state.editedRecipe
        : recipes.find((r) => r.model === model),
    startModel: (model, reviewedRecipe) => {
      deployments[model] = { ...reviewedRecipe };
    },
    stopModel: (model) => {
      delete deployments[model];
    },
    render,
    renderHUD,
    openDialog,
    closeDialog,
    announce,
    btn,
    kv,
    pill,
  };
}
function action(name) {
  if (ComputeSurface.action(name, computeApi())) return;
  if (ObservatorySurface.action(name, observabilityApi())) return;
  if (SettingsSurface.action(name, settingsApi())) return;
  if (
    name.startsWith("settings/") ||
    name.startsWith("work/") ||
    name.startsWith("observability/")
  ) {
    closeDialog();
    navigate(name);
    return;
  }
  if (name === "settings") {
    closeDialog();
    navigate("settings/general");
    return;
  }
  const api = {
    render,
    renderHUD,
    openDialog,
    closeDialog,
    announce,
    btn,
    kv,
    pill,
  };
  if (PiSurface.action(name, api)) return;
  if (extraAction(name)) return;
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
      "observability",
      "compute",
      "architecture",
      "docs",
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
    if (state.page === "observability") render();
    announce(
      state.stale
        ? "Telemetry is stale. Current measurements are unknown."
        : "Synthetic telemetry restored.",
    );
    return;
  }
  if (name === "new-experiment") {
    if (!selectedTarget()) {
      openDialog(
        "Selected model unavailable",
        '<p class="section-copy">Choose an available connector and recipe explicitly before creating a test.</p>',
        btn("Close", "close"),
      );
      return;
    }
    openDialog(
      "Create an experiment",
      `<p class="section-copy">A reusable test, pinned to an explicit recipe and workload.</p><form id="experiment-form"><div class="stack" style="margin-top:16px"><label>Experiment name<input id="experiment-name" required maxlength="100" value="Tool behavior check"></label><label>Target recipe<input id="experiment-model" readonly value="${escape(selectedTarget().recipe)} / ${escape(selectedTarget().locationName)}"></label><label>Test suite<select id="experiment-suite">${state.page === "playground" ? "<option>Saved playground request</option>" : ""}<option>Protocol and tool checks</option><option>Context window sweep</option><option>Throughput and latency</option></select></label><div class="form-grid"><label>Concurrency<input id="experiment-concurrency" type="number" min="1" max="2" value="1" required></label><label>Request limit<input id="experiment-limit" type="number" min="1" max="48" value="24" required></label></div><label class="inline-check"><input type="checkbox" checked disabled> Retain failures and incomplete results</label><div class="notice">Preview only. This design does not submit benchmark jobs.</div><button class="primary" type="submit">Review experiment</button></div></form>`,
    );
    return;
  }
  if (name === "simulate-run") {
    state.preview = structuredClone(state.pendingExperiment);
    state.demoRun = true;
    state.flowStep = 0;
    state.flowPaused = false;
    state.experimentTab = "flow";
    closeDialog();
    navigate("bench");
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
          [
            "Budget",
            `${SettingsSurface.environment().cpus} CPUs · ${SettingsSurface.environment().memory} GiB RAM · ${SettingsSurface.environment().minutes} minutes`,
          ],
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
    navigate("work/task/T-04/agent");
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
        ["Host", computeLocations[state.compute].name],
        ["Serve", selectedTarget()?.recipe || "None selected at this location"],
        ["Time window", "Current instrument snapshot · 14:16 UTC"],
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
  if (ComputeSurface.input(e, computeApi())) return;
  if (ObservatorySurface.input(e, observabilityApi())) return;
  if (SettingsSurface.input(e)) return;
  if (PiSurface.input(e)) return;
  if (e.target.id === "command-search") filterCommands(e.target.value);
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
  if (ObservatorySurface.change(e, observabilityApi())) return;
  if (SettingsSurface.input(e)) return;
  if (
    PiSurface.change(e, {
      render,
      renderHUD,
      openDialog,
      closeDialog,
      announce,
      btn,
      kv,
      pill,
    })
  )
    return;
  if (e.target.id === "compute-location" || e.target.id === "scope-model") {
    const id = e.target.id;
    state[id === "compute-location" ? "compute" : "model"] = e.target.value;
    render();
    $("#" + id).focus();
    return;
  }
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
    renderHUD();
  }
});
document.addEventListener("submit", (e) => {
  if (ComputeSurface.submit(e, computeApi())) return;
  if (SettingsSurface.submit(e, settingsApi())) return;
  if (
    PiSurface.submit(e, {
      render,
      renderHUD,
      openDialog,
      closeDialog,
      announce,
      btn,
      kv,
      pill,
    })
  )
    return;
  if (extraSubmit(e)) return;
  if (e.target.id === "prompt-form") {
    e.preventDefault();
    if (!$("#temperature").reportValidity()) return;
    if (!selectedTarget()) {
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
        model: state.model,
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
    const sourceRequest =
      state.page === "playground"
        ? state.prompt.trim()
          ? requestSnapshot()
          : state.lastRequest || requestSnapshot()
        : null;
    if (
      sourceRequest &&
      (sourceRequest.target !== selectedTarget()?.id ||
        sourceRequest.location !== state.compute)
    ) {
      openDialog(
        "The saved request has a different target",
        '<p class="section-copy">The last submitted sample request belongs to a different deployment. Select that original target or enter a new draft for the current target before creating this test.</p>',
        btn("Close", "close"),
      );
      return;
    }
    state.pendingExperiment = {
      name: $("#experiment-name").value,
      target: { ...selectedTarget() },
      suite: $("#experiment-suite").value,
      concurrency: $("#experiment-concurrency").value,
      limit: $("#experiment-limit").value,
      session: sourceRequest,
    };
    openDialog(
      "Review experiment",
      `${kv([
        ["Name", state.pendingExperiment.name],
        ["Recipe", state.pendingExperiment.target.recipe],
        ["Compute", state.pendingExperiment.target.locationName],
        ["Driver", "Deterministic Anvil Serving runner"],
        ["Suite", state.pendingExperiment.suite],
        ["Concurrency", state.pendingExperiment.concurrency],
        ["Request limit", state.pendingExperiment.limit],
        [
          "Source",
          state.pendingExperiment.session
            ? "Playground session / request settings captured"
            : "Declared test suite",
        ],
        ["Effect", "Request-only; no runtime changes"],
        ["Evidence", "Retain failures and incomplete results"],
      ])}${state.pendingExperiment.session ? `<details open style="margin-top:15px"><summary class="text-link">Captured request snapshot</summary><pre>${escape(JSON.stringify(state.pendingExperiment.session, null, 2))}</pre></details>` : ""}<div class="notice">Synthetic preview. A production preview must also bind current owner state, policy, and configuration digests with an expiry.</div>`,
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
  if (
    e.target.closest(".compute-surface") &&
    e.target.matches('[role="tab"]') &&
    ComputeSurface.input(e, computeApi())
  )
    return;
  if (
    e.target.matches('[role="tab"]') &&
    ["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)
  ) {
    e.preventDefault();
    const all = Array.from(
      e.target.closest('[role="tablist"]').querySelectorAll('[role="tab"]'),
    );
    const i = all.indexOf(e.target);
    const next =
      e.key === "Home"
        ? 0
        : e.key === "End"
          ? all.length - 1
          : (i + (e.key === "ArrowRight" ? 1 : -1) + all.length) % all.length;
    all[next].click();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    commandPalette();
  }
});
window.addEventListener("hashchange", () => {
  render();
  $("#main").focus({ preventScroll: true });
  window.scrollTo({ top: 0 });
});
render();
