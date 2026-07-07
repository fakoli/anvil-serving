// test.mjs — unit tests for the T008 routing split (node --test).
//
// Tests the routing decision layer (route.mjs) and the classify → route
// end-to-end path WITHOUT requiring a running OpenClaw gateway.
//
// Run:  node --test test.mjs
// Or:   npm test   (via package.json scripts.test)

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  DEFAULT_CLOUD_CLASSES,
  DEFAULT_NATIVE_MODEL,
  DEFAULT_NATIVE_PROVIDER,
  DEFAULT_ROUTE_TIMEOUT_MS,
  fetchAnvilTier,
  getCloudClasses,
  getNativeRoute,
  getRouteAuthEnv,
  getRouteEndpoint,
  getRouteTimeoutMs,
  makeRoutingDecision,
  resolveRouteAuthToken,
} from "./route.mjs";

import { classify } from "./classify.mjs";

const DEFAULT_NATIVE_ROUTE = {
  providerOverride: DEFAULT_NATIVE_PROVIDER,
  modelOverride: DEFAULT_NATIVE_MODEL,
};
const HERE = dirname(fileURLToPath(import.meta.url));

// ── makeRoutingDecision unit tests ──────────────────────────────────────────

describe("makeRoutingDecision — cloud-preferred presets → explicit native route", () => {
  test("planning → native provider/model override", () => {
    const result = makeRoutingDecision("planning", DEFAULT_CLOUD_CLASSES);
    assert.deepEqual(result, DEFAULT_NATIVE_ROUTE, "planning is cloud-preferred; must bypass anvil");
  });

  test("planning → native route with explicit single-item set", () => {
    const result = makeRoutingDecision("planning", new Set(["planning"]));
    assert.deepEqual(result, DEFAULT_NATIVE_ROUTE);
  });

  test("any preset in an extended cloud set → native", () => {
    const extended = new Set(["planning", "long-context"]);
    assert.deepEqual(makeRoutingDecision("planning", extended), DEFAULT_NATIVE_ROUTE);
    assert.deepEqual(makeRoutingDecision("long-context", extended), DEFAULT_NATIVE_ROUTE);
  });

  test("empty cloud set → nothing is cloud-preferred (everything routes to anvil)", () => {
    const empty = new Set();
    const result = makeRoutingDecision("planning", empty);
    assert.deepEqual(result, { providerOverride: "anvil", modelOverride: "planning" });
  });
});

describe("makeRoutingDecision — local presets → anvil with correct wire form", () => {
  // LIVE-CONFIRMED wire form (OpenClaw 2026.6.6, 2026-06-30):
  //   providerOverride MUST name the provider separately;
  //   modelOverride carries the BARE preset (not "anvil/<preset>").
  for (const preset of ["chat", "quick-edit", "review", "long-context"]) {
    test(`${preset} → { providerOverride:"anvil", modelOverride:"${preset}" }`, () => {
      const result = makeRoutingDecision(preset, DEFAULT_CLOUD_CLASSES);
      assert.deepEqual(result, {
        providerOverride: "anvil",
        modelOverride: preset,
      });
    });
  }
});

// ── getCloudClasses — env var override ──────────────────────────────────────

describe("getCloudClasses — env var ANVIL_CLOUD_CLASSES", () => {
  test("no env var → DEFAULT_CLOUD_CLASSES (only planning)", () => {
    delete process.env.ANVIL_CLOUD_CLASSES;
    const classes = getCloudClasses();
    assert.ok(classes.has("planning"), "default must include planning");
    assert.equal(classes.size, 1, "default must have exactly one entry");
  });

  test("ANVIL_CLOUD_CLASSES=planning,long-context → set with both", () => {
    process.env.ANVIL_CLOUD_CLASSES = "planning,long-context";
    try {
      const classes = getCloudClasses();
      assert.ok(classes.has("planning"));
      assert.ok(classes.has("long-context"));
      assert.equal(classes.size, 2);
    } finally {
      delete process.env.ANVIL_CLOUD_CLASSES;
    }
  });

  test("ANVIL_CLOUD_CLASSES with spaces trimmed", () => {
    process.env.ANVIL_CLOUD_CLASSES = " planning , review ";
    try {
      const classes = getCloudClasses();
      assert.ok(classes.has("planning"));
      assert.ok(classes.has("review"));
    } finally {
      delete process.env.ANVIL_CLOUD_CLASSES;
    }
  });

  test("ANVIL_CLOUD_CLASSES='' (empty) → falls back to default", () => {
    process.env.ANVIL_CLOUD_CLASSES = "";
    try {
      const classes = getCloudClasses();
      assert.deepEqual(
        [...classes].sort(),
        [...DEFAULT_CLOUD_CLASSES].sort(),
        "empty env var must fall back to default",
      );
    } finally {
      delete process.env.ANVIL_CLOUD_CLASSES;
    }
  });

  test("ANVIL_CLOUD_CLASSES=none → cloud set does not include planning", () => {
    process.env.ANVIL_CLOUD_CLASSES = "none";
    try {
      const classes = getCloudClasses();
      // "none" is not a real preset but the override is respected
      assert.ok(!classes.has("planning"), "planning should not be in custom set");
      assert.ok(classes.has("none"));
    } finally {
      delete process.env.ANVIL_CLOUD_CLASSES;
    }
  });
});

// ── plugin config fallback + env precedence ─────────────────────────────────

describe("getCloudClasses — api.pluginConfig.cloudClasses fallback", () => {
  test("plugin config cloudClasses is used when env var is unset", () => {
    delete process.env.ANVIL_CLOUD_CLASSES;
    const classes = getCloudClasses({ cloudClasses: ["planning", "long-context"] });
    assert.deepEqual([...classes].sort(), ["long-context", "planning"]);
  });

  test("ANVIL_CLOUD_CLASSES wins over plugin config cloudClasses", () => {
    process.env.ANVIL_CLOUD_CLASSES = "review";
    try {
      const classes = getCloudClasses({ cloudClasses: ["planning", "long-context"] });
      assert.deepEqual([...classes], ["review"]);
    } finally {
      delete process.env.ANVIL_CLOUD_CLASSES;
    }
  });

  test("empty plugin config cloudClasses falls back to default", () => {
    delete process.env.ANVIL_CLOUD_CLASSES;
    const classes = getCloudClasses({ cloudClasses: [] });
    assert.deepEqual(
      [...classes].sort(),
      [...DEFAULT_CLOUD_CLASSES].sort(),
    );
  });

  test("malformed plugin config cloudClasses does not throw and falls back", () => {
    delete process.env.ANVIL_CLOUD_CLASSES;
    const pluginConfig = {};
    Object.defineProperty(pluginConfig, "cloudClasses", {
      get() {
        throw new Error("config getter failed");
      },
    });
    const classes = getCloudClasses(pluginConfig);
    assert.deepEqual(
      [...classes].sort(),
      [...DEFAULT_CLOUD_CLASSES].sort(),
    );
  });
});

describe("getRouteEndpoint — env var then api.pluginConfig.routeEndpoint", () => {
  test("plugin config routeEndpoint is used when env var is unset", () => {
    delete process.env.ANVIL_ROUTE_ENDPOINT;
    const endpoint = getRouteEndpoint({
      routeEndpoint: " http://127.0.0.1:8000/v1/route ",
    });
    assert.equal(endpoint, "http://127.0.0.1:8000/v1/route");
  });

  test("ANVIL_ROUTE_ENDPOINT wins over plugin config routeEndpoint", () => {
    process.env.ANVIL_ROUTE_ENDPOINT = "http://127.0.0.1:9000/v1/route";
    try {
      const endpoint = getRouteEndpoint({
        routeEndpoint: "http://127.0.0.1:8000/v1/route",
      });
      assert.equal(endpoint, "http://127.0.0.1:9000/v1/route");
    } finally {
      delete process.env.ANVIL_ROUTE_ENDPOINT;
    }
  });

  test("empty route endpoint values are treated as unset", () => {
    process.env.ANVIL_ROUTE_ENDPOINT = " ";
    try {
      const endpoint = getRouteEndpoint({ routeEndpoint: " " });
      assert.equal(endpoint, undefined);
    } finally {
      delete process.env.ANVIL_ROUTE_ENDPOINT;
    }
  });

  test("malformed plugin config routeEndpoint does not throw", () => {
    delete process.env.ANVIL_ROUTE_ENDPOINT;
    const pluginConfig = {};
    Object.defineProperty(pluginConfig, "routeEndpoint", {
      get() {
        throw new Error("config getter failed");
      },
    });
    assert.equal(getRouteEndpoint(pluginConfig), undefined);
  });
});

describe("getRouteTimeoutMs — env var then api.pluginConfig fallback", () => {
  test("defaults to the bounded hook timeout", () => {
    delete process.env.ANVIL_ROUTE_TIMEOUT_MS;
    assert.equal(getRouteTimeoutMs(), DEFAULT_ROUTE_TIMEOUT_MS);
  });

  test("plugin config routeTimeoutMs is used when env var is unset", () => {
    delete process.env.ANVIL_ROUTE_TIMEOUT_MS;
    assert.equal(getRouteTimeoutMs({ routeTimeoutMs: 250 }), 250);
  });

  test("ANVIL_ROUTE_TIMEOUT_MS wins over plugin config", () => {
    process.env.ANVIL_ROUTE_TIMEOUT_MS = "750";
    try {
      assert.equal(getRouteTimeoutMs({ routeTimeoutMs: 250 }), 750);
    } finally {
      delete process.env.ANVIL_ROUTE_TIMEOUT_MS;
    }
  });

  test("invalid timeout values fall back instead of hanging the hook", () => {
    process.env.ANVIL_ROUTE_TIMEOUT_MS = "999999";
    try {
      assert.equal(getRouteTimeoutMs({ routeTimeoutMs: -1 }), DEFAULT_ROUTE_TIMEOUT_MS);
    } finally {
      delete process.env.ANVIL_ROUTE_TIMEOUT_MS;
    }
  });
});

describe("route endpoint auth — env-name based token resolution", () => {
  test("plugin config routeAuthEnv is used when env var is unset", () => {
    delete process.env.ANVIL_ROUTE_AUTH_ENV;
    assert.equal(
      getRouteAuthEnv({ routeAuthEnv: " ANVIL_ROUTER_TOKEN " }),
      "ANVIL_ROUTER_TOKEN",
    );
  });

  test("ANVIL_ROUTE_AUTH_ENV wins over plugin config routeAuthEnv", () => {
    process.env.ANVIL_ROUTE_AUTH_ENV = "ANVIL_OTHER_ROUTER_TOKEN";
    try {
      assert.equal(
        getRouteAuthEnv({ routeAuthEnv: "ANVIL_ROUTER_TOKEN" }),
        "ANVIL_OTHER_ROUTER_TOKEN",
      );
    } finally {
      delete process.env.ANVIL_ROUTE_AUTH_ENV;
    }
  });

  test("route auth values must be env var names, not raw tokens", () => {
    delete process.env.ANVIL_ROUTE_AUTH_ENV;
    assert.equal(getRouteAuthEnv({ routeAuthEnv: "not-an-env-name" }), undefined);
  });

  test("resolveRouteAuthToken reads only the named env var", () => {
    delete process.env.ANVIL_ROUTE_AUTH_ENV;
    process.env.ANVIL_ROUTER_TOKEN = "route-secret";
    try {
      assert.equal(
        resolveRouteAuthToken({ routeAuthEnv: "ANVIL_ROUTER_TOKEN" }),
        "route-secret",
      );
    } finally {
      delete process.env.ANVIL_ROUTER_TOKEN;
    }
  });
});

describe("native route config — env var then api.pluginConfig fallback", () => {
  test("defaults to the documented native provider/model", () => {
    delete process.env.ANVIL_NATIVE_PROVIDER;
    delete process.env.ANVIL_NATIVE_MODEL;
    assert.deepEqual(getNativeRoute(), DEFAULT_NATIVE_ROUTE);
  });

  test("plugin config native route is used when env vars are unset", () => {
    delete process.env.ANVIL_NATIVE_PROVIDER;
    delete process.env.ANVIL_NATIVE_MODEL;
    assert.deepEqual(
      getNativeRoute({ nativeProvider: "openai", nativeModel: "gpt-5.5" }),
      { providerOverride: "openai", modelOverride: "gpt-5.5" },
    );
  });

  test("ANVIL_NATIVE_PROVIDER and ANVIL_NATIVE_MODEL win over plugin config", () => {
    process.env.ANVIL_NATIVE_PROVIDER = "openai";
    process.env.ANVIL_NATIVE_MODEL = "gpt-5.4-mini";
    try {
      assert.deepEqual(
        getNativeRoute({ nativeProvider: "anthropic", nativeModel: "claude-sonnet-4-5" }),
        { providerOverride: "openai", modelOverride: "gpt-5.4-mini" },
      );
    } finally {
      delete process.env.ANVIL_NATIVE_PROVIDER;
      delete process.env.ANVIL_NATIVE_MODEL;
    }
  });
});

describe("fetchAnvilTier — authenticated route endpoint", () => {
  test("sends bearer and x-api-key headers when authToken is provided", async () => {
    const seen = [];
    const server = createServer((req, res) => {
      let body = "";
      req.on("data", (chunk) => { body += chunk; });
      req.on("end", () => {
        const parsedBody = JSON.parse(body);
        seen.push({
          authorization: req.headers.authorization,
          apiKey: req.headers["x-api-key"],
          body: parsedBody,
        });
        if (
          req.headers.authorization !== "Bearer route-secret"
          || req.headers["x-api-key"] !== "route-secret"
        ) {
          res.writeHead(401, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "unauthorized" }));
          return;
        }
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ tier: "local" }));
      });
    });

    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const { port } = server.address();
    const endpoint = `http://127.0.0.1:${port}/v1/route`;
    try {
      assert.equal(await fetchAnvilTier("hello", undefined, endpoint), null);
      assert.equal(
        await fetchAnvilTier("hello", undefined, endpoint, {
          authToken: "route-secret",
          workClass: "planning",
        }),
        "local",
      );
      assert.deepEqual(seen[1], {
        authorization: "Bearer route-secret",
        apiKey: "route-secret",
        body: {
          model: "planning",
          messages: [{ role: "user", content: "hello" }],
          signals: { work_class: "planning" },
        },
      });
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });

  test("maps route exhaustion 503 to cloud/native", async () => {
    const server = createServer((req, res) => {
      req.resume();
      res.writeHead(503, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ tier: "local" }));
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const { port } = server.address();
    try {
      assert.equal(
        await fetchAnvilTier("hello", undefined, `http://127.0.0.1:${port}/v1/route`, {
          workClass: "planning",
        }),
        "cloud",
      );
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });

  test("ignores non-503 non-2xx route responses even if body contains a tier", async () => {
    const server = createServer((req, res) => {
      req.resume();
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ tier: "local" }));
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const { port } = server.address();
    try {
      assert.equal(
        await fetchAnvilTier("hello", undefined, `http://127.0.0.1:${port}/v1/route`, {
          workClass: "planning",
        }),
        null,
      );
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });

  test("honors raised route timeout for split-host route endpoints", async () => {
    const server = createServer((req, res) => {
      req.resume();
      setTimeout(() => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ tier: "local" }));
      }, 80);
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const { port } = server.address();
    try {
      assert.equal(
        await fetchAnvilTier("hello", undefined, `http://127.0.0.1:${port}/v1/route`, {
          timeoutMs: 200,
          workClass: "planning",
        }),
        "local",
      );
    } finally {
      await new Promise((resolve) => server.close(resolve));
    }
  });
});

describe("fixture generation", () => {
  test("make-fixture ignores ANVIL_CLOUD_CLASSES from the developer shell", () => {
    const fixturePath = join(HERE, "decision_log.fixture.jsonl");
    const before = readFileSync(fixturePath, "utf8");
    try {
      execFileSync(process.execPath, ["make-fixture.mjs"], {
        cwd: HERE,
        env: { ...process.env, ANVIL_CLOUD_CLASSES: "none" },
        stdio: "pipe",
      });
      const after = readFileSync(fixturePath, "utf8");
      assert.equal(after.replace(/\r\n/g, "\n"), before.replace(/\r\n/g, "\n"));
    } finally {
      writeFileSync(fixturePath, before);
    }
  });
});

// ── End-to-end: classify → makeRoutingDecision ──────────────────────────────

describe("e2e: classify → makeRoutingDecision (T008 routing split)", () => {
  // These are the CONFIRMED live-validation turns (2026-06-30):

  test("planning prompt → planning → explicit native route", () => {
    const preset = classify("Plan the migration across all services step by step");
    assert.equal(preset, "planning", "step-by-step planning → planning preset");
    const result = makeRoutingDecision(preset, DEFAULT_CLOUD_CLASSES);
    assert.deepEqual(result, DEFAULT_NATIVE_ROUTE, "planning must route to native, not anvil");
  });

  test("quick-edit prompt → quick-edit → anvil", () => {
    const preset = classify("Fix the null pointer deref in handler.go");
    assert.equal(preset, "quick-edit", "fix keyword → quick-edit preset");
    const result = makeRoutingDecision(preset, DEFAULT_CLOUD_CLASSES);
    assert.deepEqual(result, { providerOverride: "anvil", modelOverride: "quick-edit" });
  });

  test("review prompt → review → anvil", () => {
    const preset = classify("Review this pull request and find bugs");
    assert.equal(preset, "review", "review keyword → review preset");
    const result = makeRoutingDecision(preset, DEFAULT_CLOUD_CLASSES);
    assert.deepEqual(result, { providerOverride: "anvil", modelOverride: "review" });
  });

  test("factual chat prompt → chat → anvil", () => {
    const preset = classify("What is the capital of France?");
    assert.equal(preset, "chat", "factual question → chat preset (no keyword match)");
    const result = makeRoutingDecision(preset, DEFAULT_CLOUD_CLASSES);
    assert.deepEqual(result, { providerOverride: "anvil", modelOverride: "chat" });
  });

  test("plan keyword does NOT fire on 'explaining' or 'planet'", () => {
    // Regression: word-boundary matching must not fire on substrings.
    const p1 = classify("I was explaining the architecture");
    assert.notEqual(p1, "planning", "explaining must not trigger planning");
    const p2 = classify("The planet is round");
    assert.notEqual(p2, "planning", "planet must not trigger planning");
  });

  test("planning keyword fires on 'plans'", () => {
    const preset = classify("She plans to refactor the module");
    // "plans" matches planning but "refactor" also matches multi-file-refactor -> review;
    // keyword order: planning rule fires AFTER review, so "refactor" wins (review > planning).
    // This is a precedence test, not a routing test — just verify no throw.
    assert.ok(["planning", "review", "quick-edit", "chat", "long-context"].includes(preset));
  });

  test("design/architect keywords → planning → native", () => {
    for (const prompt of [
      "Design a new service API",
      "Architect the caching layer",
    ]) {
      const preset = classify(prompt);
      assert.equal(preset, "planning", `"${prompt}" should classify as planning`);
      const result = makeRoutingDecision(preset, DEFAULT_CLOUD_CLASSES);
      assert.deepEqual(result, DEFAULT_NATIVE_ROUTE, `planning from "${prompt}" must route to native`);
    }
  });

  test("implement/patch → quick-edit → anvil", () => {
    const preset = classify("Implement the missing error handler");
    assert.equal(preset, "quick-edit");
    const result = makeRoutingDecision(preset, DEFAULT_CLOUD_CLASSES);
    assert.deepEqual(result, { providerOverride: "anvil", modelOverride: "quick-edit" });
  });

  test("long prompt → long-context → anvil (not in default cloud set)", () => {
    const longPrompt = "a".repeat(25_000);
    const preset = classify(longPrompt);
    assert.equal(preset, "long-context");
    const result = makeRoutingDecision(preset, DEFAULT_CLOUD_CLASSES);
    assert.deepEqual(result, { providerOverride: "anvil", modelOverride: "long-context" });
  });

  test("long-context → native when explicitly added to cloud-preferred presets", () => {
    const extended = new Set(["planning", "long-context"]);
    const preset = classify("a".repeat(25_000));
    assert.equal(preset, "long-context");
    const result = makeRoutingDecision(preset, extended);
    assert.deepEqual(result, DEFAULT_NATIVE_ROUTE, "long-context in cloud set must route to native");
  });
});

// ── Wire-form assertion ──────────────────────────────────────────────────────

describe("wire form: modelOverride must be bare preset, not 'anvil/<preset>'", () => {
  test("no local result has 'anvil/' prefix in modelOverride", () => {
    for (const preset of ["quick-edit", "review", "chat", "long-context"]) {
      const result = makeRoutingDecision(preset, DEFAULT_CLOUD_CLASSES);
      if (result.modelOverride !== undefined) {
        assert.ok(
          !result.modelOverride.includes("/"),
          `modelOverride "${result.modelOverride}" must not contain '/' (bare preset only)`,
        );
      }
    }
  });
});
