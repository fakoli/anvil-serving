"use strict";
// Isolated browser fixture only. No owner endpoint or inference call exists here.
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const BASE = "/nested/observatory/";
const TIME = "2026-09-08T12:00:00.000001Z";
const staticRoot = path.resolve(
  __dirname,
  "../../../anvil_serving/observability/dashboard/static",
);
const metric = (value, unit, status = "fresh") => ({
  value: status === "fresh" ? value : null,
  last_known_value: status === "stale" ? value : null,
  status,
  unit,
  source_timestamp: TIME,
});
const gpu = (id, owner) => ({
  id,
  label: `Compute GPU ${id.endsWith("a") ? "A" : "B"}`,
  uuid: `GPU-fixture-${id}`,
  role: "compute",
  owners: [owner],
  memory_used: metric(24, "GiB"),
  memory_total: metric(48, "GiB"),
  utilization: metric(18, "%"),
});
const hosts = [
  {
    id: "host-fixture-a",
    display_name: "Atlas workstation",
    platform: "Linux",
    maintenance: false,
    controller: { status: "fresh", version: "fixture-v1", observed_at: TIME },
    telemetry: { status: "fresh", observed_at: TIME },
    gpus: [gpu("compute-a", "serve-a"), gpu("compute-b", "serve-b")],
    resources: {
      cpu: metric(12, "%"),
      memory: metric(32, "GiB"),
      filesystem: metric(240, "GiB"),
      network: metric(3, "MB/s"),
    },
    profiles: [
      {
        id: "split",
        label: "Independent GPU services",
        description: "One declared owner per physical GPU.",
      },
    ],
    mode: "split",
    ownership_status: "verified",
    diagnostics: [
      {
        id: "gpu-source",
        name: "GPU collection",
        source: "host",
        status: "fresh",
        records: 2,
      },
    ],
  },
  {
    id: "host-fixture-b",
    display_name: "Boreal workstation",
    platform: "Windows",
    maintenance: false,
    controller: {
      status: "unavailable",
      version: null,
      observed_at: null,
      reason: "Controller source unavailable.",
    },
    telemetry: { status: "stale", observed_at: TIME },
    gpus: [],
    resources: { memory: metric(8, "GiB", "stale") },
    profiles: [],
    mode: "single",
    ownership_status: "unknown",
  },
];
const serves = [
  {
    id: "serve-a",
    host_id: "host-fixture-a",
    display_name: "Primary reasoning",
    model: "fixture-model-a",
    observed_model: "fixture-model-a",
    engine: "engine-a",
    aliases: ["llm.primary"],
    runtime_state: "running",
    readiness: "ready",
    admission: "admitted",
    gpu_ids: ["compute-a"],
    observed_at: TIME,
    ownership_status: "verified",
    metrics: {
      queue: metric(0, "requests"),
      generation: metric(24, "tokens/s"),
      ttft: metric(null, "s", "unsupported"),
    },
    evidence: [{ id: "evidence-a", label: "Bounded probe · passed" }],
    logs: { lines: ["Owner fixture: source ready."], truncated: false },
  },
  {
    id: "serve-b",
    host_id: "host-fixture-a",
    display_name: "Secondary reasoning",
    model: "fixture-model-b",
    observed_model: "fixture-model-b",
    engine: "engine-b",
    aliases: ["llm.secondary"],
    runtime_state: "running",
    readiness: "ready",
    admission: "admitted",
    gpu_ids: ["compute-b"],
    observed_at: TIME,
    ownership_status: "verified",
    metrics: {
      queue: metric(1, "requests"),
      generation: metric(17, "tokens/s"),
    },
  },
];
const resources = [
  ...serves.map((s) => ({
    id: s.id,
    host_id: s.host_id,
    label: s.display_name,
    kind: "serve",
  })),
  {
    id: "gateway-policy",
    host_id: "host-fixture-a",
    label: "Primary gateway policy",
    kind: "configuration",
  },
  {
    id: "experiment-a",
    host_id: "host-fixture-a",
    label: "Primary managed probe",
    kind: "experiment",
  },
];
const dims = {
  host: "host-fixture-a",
  hardware: "fixture-gpu-a",
  model_revision: "fixture-revision-a",
  quantization: "fixture-q",
  engine: "fixture-engine-v1",
  context: 4096,
  concurrency: 1,
  output_constraints: 32,
  warm_state: "warm",
  correctness: "passed",
};
const evidence = [
  {
    id: "evidence-a",
    label: "Bounded probe · passed",
    resource_id: "serve-a",
    model: "fixture-model-a",
    host_id: "host-fixture-a",
    parameters: { max_output_tokens: 32 },
    metrics: { generation: 24 },
    correctness: "passed",
    limitations: ["One bounded fixture check only."],
    comparison_dimensions: dims,
  },
  {
    id: "evidence-b",
    label: "Candidate probe · incomplete",
    resource_id: "serve-b",
    model: "fixture-model-b",
    host_id: "host-fixture-a",
    parameters: { max_output_tokens: 32 },
    metrics: { generation: 26 },
    correctness: "incomplete",
    limitations: ["Different model revision."],
    comparison_dimensions: {
      ...dims,
      model_revision: "fixture-revision-b",
      correctness: null,
    },
  },
];
const setting = {
  setting_id: "max_output_tokens",
  label: "Maximum output tokens",
  value_type: "integer",
  unit: "tokens",
  configured: 32,
  observed: 24,
  observed_status: "fresh",
  constraints: { minimum: 1, maximum: 128, step: 1 },
  support: "supported",
  effect: "config reload",
  help: "Maximum output budget. Configuration validation never changes the active owner.",
};
function canonicalWorkloads(partial = false) {
  const record = {
    schema: "anvil-workloads/v1",
    id: "a".repeat(64),
    kind: "controller-operation",
    owner: "controller",
    host: "host-fixture-a",
    label: "Controller Operation",
    state: "running",
    phase: "running",
    created_at: TIME,
    updated_at: TIME,
    source_timestamp: TIME,
    source_authority: "controller-store",
    observation_quality: "recorded",
  };
  const source = {
    schema: "anvil-workloads/v1",
    owner: "controller",
    status: partial ? "partial" : "complete",
    collection_timestamp: TIME,
    records: [record],
    truncation: { returned: 1, omitted: partial ? null : 0 },
    error: null,
  };
  return {
    schema: "anvil-workloads/v1",
    status: partial ? "partial" : "complete",
    collection_timestamp: TIME,
    nodes: [
      {
        schema: "anvil-workloads/v1",
        host: "host-fixture-a",
        status: source.status,
        collection_timestamp: TIME,
        sources: [source],
      },
    ],
    truncation: source.truncation,
  };
}
async function createFixture(base = BASE) {
  const state = {
    authenticated: false,
    operate: true,
    posts: [],
    reads: [],
    operations: [],
    draft: null,
    preview: null,
    lost: false,
    expirePreview: false,
    partialWorkloads: false,
    malformedWorkloads: false,
    metricFailure: false,
    slowMetrics: 0,
    slowHost: null,
    serveProbeOnly: false,
  };
  const session = () => ({
    authenticated: state.authenticated,
    identity: state.authenticated ? "fixture-operator" : null,
    role: state.operate ? "operator" : "viewer",
    operate: state.authenticated && state.operate,
    csrf_token: state.authenticated ? "synthetic-csrf" : null,
    expires_at: Date.now() / 1000 + 3600,
    base_path: base,
    build: "fixture-v1",
    fixture: true,
  });
  const respond = (res, data, status = 200) => {
    res.writeHead(status, {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    });
    res.end(
      JSON.stringify(
        status < 400 ? { ok: true, data } : { ok: false, error: data },
      ),
    );
  };
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    const prefix = base + "api/observatory/v1/";
    if (!url.pathname.startsWith(prefix)) {
      let filename =
        url.pathname === base
          ? "observatory.html"
          : url.pathname.slice(base.length);
      if (
        !url.pathname.startsWith(base) ||
        filename.includes("..") ||
        !/^([a-z_]+\/)?[a-z_.]+$/.test(filename)
      ) {
        res.writeHead(404).end();
        return;
      }
      const full = path.join(staticRoot, filename);
      if (!fs.existsSync(full)) {
        res.writeHead(404).end();
        return;
      }
      res.writeHead(200, {
        "Content-Type": filename.endsWith(".js")
          ? "text/javascript"
          : filename.endsWith(".css")
            ? "text/css"
            : "text/html",
        "Content-Security-Policy":
          "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        "Cache-Control": "no-store",
      });
      res.end(fs.readFileSync(full));
      return;
    }
    const route = url.pathname.slice(prefix.length);
    let body = {};
    try {
      if (req.method !== "GET") {
        let raw = "";
        for await (const chunk of req) raw += chunk;
        body = raw ? JSON.parse(raw) : {};
        state.posts.push({
          route,
          method: req.method,
          body,
          csrf: req.headers["x-csrf-token"],
        });
      } else
        state.reads.push({
          route,
          query: Object.fromEntries(url.searchParams),
        });
    } catch {
      respond(
        res,
        { code: "invalid_json", message: "Invalid fixture request." },
        400,
      );
      return;
    }
    if (route === "session") {
      if (req.method === "POST") {
        state.authenticated = true;
        res.setHeader(
          "Set-Cookie",
          "fixture-session=opaque; HttpOnly; SameSite=Strict; Path=" + base,
        );
      }
      if (req.method === "DELETE") state.authenticated = false;
      respond(res, session());
      return;
    }
    if (!state.authenticated) {
      respond(
        res,
        { code: "unauthorized", message: "Sign in to continue." },
        401,
      );
      return;
    }
    if (
      req.method !== "GET" &&
      (!state.operate || req.headers["x-csrf-token"] !== "synthetic-csrf")
    ) {
      respond(
        res,
        { code: "forbidden", message: "Operate scope is required." },
        403,
      );
      return;
    }
    if (route === "fleet") {
      respond(res, {
        hosts,
        serves,
        coverage: { status: "partial", sources: ["controller", "host"] },
        control_resources: state.serveProbeOnly
          ? resources.filter((r) => r.kind !== "experiment")
          : resources,
        observed_at: TIME,
      });
      return;
    }
    if (route === "settings") {
      respond(res, {
        resources,
        integrations: [
          {
            id: "prometheus",
            label: "Prometheus",
            status: state.metricFailure ? "unavailable" : "fresh",
            observed_at: TIME,
          },
        ],
        schemas: { facade: "v1", journal: 1 },
        permitted_catalog: {
          configuration: ["configuration.apply"],
          experiment: ["experiment.start"],
        },
      });
      return;
    }
    if (route.startsWith("hosts/")) {
      respond(
        res,
        hosts.find((h) => h.id === route.split("/")[1]),
      );
      return;
    }
    if (route.startsWith("serves/")) {
      respond(
        res,
        serves.find((s) => s.id === route.split("/")[1]),
      );
      return;
    }
    if (route === "controls") {
      const resource = url.searchParams.get("resource");
      if (!resource) {
        respond(res, { resources });
        return;
      }
      const experiment = resource === "experiment-a";
      respond(res, {
        resource_id: resource,
        baseline_digest: "baseline-v1",
        settings: experiment ? [] : [setting],
        experiment_settings: experiment
          ? [
              {
                ...setting,
                configured: 16,
                help: "Fixed managed fixture probe; one request, concurrency one.",
              },
            ]
          : [],
        experiment_limit: 1,
        actions: experiment
          ? [
              {
                id: "experiment.start",
                label: "Run managed probe",
                supported: true,
                permitted: state.operate,
                effect: "Next test request",
              },
            ]
          : [
              ...(state.serveProbeOnly
                ? [
                    {
                      id: "serve.probe",
                      label: "Run bounded serve probe",
                      supported: true,
                      permitted: state.operate,
                      effect: "One fixed owner request",
                    },
                  ]
                : []),
              {
                id: "serve.stop",
                label: "Stop serve",
                supported: resource.startsWith("serve"),
                permitted: state.operate,
                reason: "Only declared serves support stop.",
                effect: "Stop the selected serve",
                stop_semantics: "Owner-defined draining",
              },
              {
                id: "configuration.apply",
                label: "Apply configuration",
                supported: true,
                permitted: state.operate,
                effect: "Config reload",
              },
            ],
      });
      return;
    }
    if (route === "metrics") {
      if (
        state.slowMetrics &&
        (!state.slowHost || url.searchParams.get("host") === state.slowHost)
      )
        await new Promise((resolve) => setTimeout(resolve, state.slowMetrics));
      if (state.metricFailure) {
        respond(
          res,
          {
            code: "source_unavailable",
            message: "Historical source unavailable.",
          },
          503,
        );
        return;
      }
      const id = url.searchParams.get("chart");
      respond(res, {
        id,
        title:
          {
            generation: "Generation throughput",
            ttft: "Time to first token",
            queue: "Queue pressure",
            gpu_memory: "GPU memory by physical device",
          }[id] || id,
        unit:
          id === "gpu_memory"
            ? "GiB"
            : id === "ttft"
              ? "seconds"
              : id === "queue"
                ? "requests"
                : "tokens/s",
        source: id === "gpu_memory" ? "Host telemetry" : "Engine telemetry",
        window: url.searchParams.get("range"),
        status: id === "ttft" ? "unsupported" : "fresh",
        reason:
          id === "ttft" ? "This engine does not expose TTFT samples." : null,
        series:
          id === "ttft"
            ? []
            : [
                {
                  id: "a",
                  label:
                    url.searchParams.get("serve") ||
                    url.searchParams.get("host") ||
                    "Primary reasoning",
                  points: [
                    [1788868800, 12],
                    [1788868860, 18],
                    [1788868920, null],
                    [1788868980, 24],
                    [1788869040, 23],
                  ],
                },
                {
                  id: "b",
                  label: "Secondary reasoning",
                  points: [
                    [1788868800, 8],
                    [1788868860, 10],
                    [1788868920, 11],
                    [1788868980, 16],
                    [1788869040, 17],
                  ],
                },
              ],
        grafana_url:
          "/grafana/d/fixture?var-host=" +
          encodeURIComponent(url.searchParams.get("host") || "all"),
      });
      return;
    }
    if (route === "workloads") {
      const data = canonicalWorkloads(state.partialWorkloads);
      if (state.malformedWorkloads)
        data.nodes[0].sources[0].records[0].source_authority = "router-memory";
      respond(res, data);
      return;
    }
    if (route === "drafts") {
      const errors =
        typeof body.values.max_output_tokens !== "number" ||
        body.values.max_output_tokens > 128
          ? [{ field: "max_output_tokens", message: "Use at most 128 tokens." }]
          : [];
      state.draft = {
        ...body,
        id: "draft-a",
        version: 1,
        baseline_digest: "baseline-v1",
        candidate_digest: "candidate-v1",
        errors,
      };
      respond(res, state.draft);
      return;
    }
    if (route === "previews") {
      state.preview = {
        id: "preview-a",
        host_id: "host-fixture-a",
        resource_id: body.resource_id,
        action_id: body.action_id,
        label:
          body.action_id === "experiment.start"
            ? "Run managed probe"
            : "Apply configuration",
        baseline_digest: "baseline-v1",
        candidate_digest: "candidate-v1",
        policy_digest: "policy-v1",
        expires_at_epoch_seconds:
          Date.now() / 1000 + (state.expirePreview ? -1 : 120),
        effect: "Config reload; request admission may change.",
        diff: [
          {
            field: "max_output_tokens",
            before: 32,
            after: state.draft?.values.max_output_tokens || 16,
          },
        ],
        affected_aliases: ["llm.primary"],
        workload_impact: "Existing work is drained by its owner.",
        gpu_ids: ["compute-a"],
        stop_semantics: "Drain before owner transition",
        recovery:
          "Restore prior configuration through a new reviewed operation.",
        planned_steps: [
          "Validate owner identity and candidate",
          "Apply exact candidate",
          "Verify independent observed result",
        ],
      };
      respond(res, state.preview);
      return;
    }
    if (route === "operations" && req.method === "POST") {
      const prior = state.operations.find(
        (op) => op.intent_key === body.intent_key,
      );
      if (prior) {
        if (state.lost) {
          req.socket.destroy();
          return;
        }
        respond(res, prior, 202);
        return;
      }
      const op = {
        id: `operation-${state.operations.length + 1}`,
        intent_key: body.intent_key,
        resource_id: state.preview.resource_id,
        host_id: "host-fixture-a",
        action_id: state.preview.action_id,
        label: state.preview.label,
        actor: "fixture-operator",
        service_identity: "fixture-web-control",
        submitted_at: TIME,
        updated_at: TIME,
        status: "running",
        native_state: "running",
        owner_operation_id: "owner-001",
        execution_outcome: "pending",
        verification: {
          status: "pending",
          message: "Waiting for independent observation.",
        },
        recovery: { status: "not_required", message: "No recovery attempted." },
        events: [
          {
            at: TIME,
            source: "owner",
            phase: "running",
            message: "Owner accepted the exact candidate.",
          },
        ],
        evidence_id: "evidence-a",
      };
      state.operations.push(op);
      if (state.lost) {
        req.socket.destroy();
        return;
      }
      respond(res, op, 202);
      return;
    }
    if (route === "operations") {
      respond(res, { items: state.operations, truncated: false });
      return;
    }
    if (route.startsWith("operations/")) {
      respond(
        res,
        state.operations.find((op) => op.id === route.split("/")[1]),
      );
      return;
    }
    if (route === "evidence") {
      respond(res, { items: evidence, truncated: false });
      return;
    }
    if (route.startsWith("evidence/")) {
      respond(
        res,
        evidence.find((e) => e.id === route.split("/")[1]),
      );
      return;
    }
    respond(
      res,
      { code: "not_found", message: "This fixture route is unavailable." },
      404,
    );
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return {
    state,
    url: `http://127.0.0.1:${server.address().port}${base}`,
    server,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}
module.exports = { createFixture, BASE };
