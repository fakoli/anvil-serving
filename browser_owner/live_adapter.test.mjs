import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { chromium } from "playwright";
import { createLiveObservationAdapter, LiveAdapterError } from "./live_adapter.mjs";
import { createLiveBufferedTransport } from "./live_transport.mjs";

const watchdog = setTimeout(() => { process.stderr.write("live adapter test watchdog expired\n"); process.exit(1); }, 60_000);
test.after(() => clearTimeout(watchdog));
const executablePath = () => process.env.CHROMIUM_EXECUTABLE || "/usr/bin/google-chrome";
const origin = "https://public.example";
const pages = Object.freeze([{ id: "home", url: `${origin}/` }, { id: "projects", url: `${origin}/projects` }]);
const error = (code) => (value) => value instanceof LiveAdapterError && value.code === code;
const bytes = (value) => Buffer.byteLength(value, "utf8");
const refused = (code) => ({ schema: "browser-owner-adapter/v1", status: "refused", code });
const cards = `<!doctype html>${Array.from({ length: 23 }, (_, index) => `<button aria-label="card-${index}">card-${index}</button>`).join("")}`;
const home = '<!doctype html><main><button disabled aria-label="Disabled">Disabled</button><button aria-label="Enabled">Enabled</button></main>';
const publicLookup = async () => [{ address: "93.184.216.34", family: 4 }];

function htmlFor(url) { return url.pathname === "/projects" ? cards : home; }
function response(body) {
  const value = new EventEmitter(); value.statusCode = 200; value.headers = { "content-type": "text/html" }; value.rawHeaders = ["content-type", "text/html"];
  value.destroyed = false; value.destroy = () => { value.destroyed = true; };
  queueMicrotask(() => { if (!value.destroyed) { value.emit("data", Buffer.from(body)); value.emit("end"); } });
  return value;
}
function transportFactory(calls, hooks = {}) {
  return async ({ documentUrls }) => createLiveBufferedTransport({
    documentUrls, lookup: publicLookup,
    request: (url, options, callback) => {
      const outgoing = new EventEmitter(); outgoing.destroyed = false; outgoing.destroy = () => { outgoing.destroyed = true; };
      outgoing.end = () => {
        calls.push({ url: url.href, options }); hooks.requested?.(url);
        const deliver = () => { if (!outgoing.destroyed) callback(response(htmlFor(url))); };
        if (hooks.waitForResponse) void hooks.waitForResponse.then(deliver);
        else deliver();
      };
      return outgoing;
    },
  });
}
function launch(...hooks) {
  return async () => {
    const browser = await chromium.launch({ executablePath: executablePath(), headless: true, args: ["--disable-gpu"] });
    for (const hook of hooks) if (hook) await hook(browser);
    return browser;
  };
}
function capture(request_id, changes = {}) {
  return {
    schema: "widget-resolution/v1", request_id,
    target: { description: "the requested public control", qualifiers: [] }, predicates: ["exists", "in_viewport", "occluded", "enabled"], scope: { kind: "document", root: "document" },
    ...changes,
  };
}
function canonical(value) { return value === null || typeof value === "boolean" ? String(value) : typeof value === "string" || typeof value === "number" ? JSON.stringify(value) : Array.isArray(value) ? `[${value.map(canonical).join(",")}]` : `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`; }
function fakeJev(origin, runner) {
  return { enabled: true, origin, fields: ["schema", "request_id", "observation_id", "source", "target", "scope", "coverage", "entities"], runner };
}
function answer(projection, choice) {
  const choices = [...projection.entities.map((entity) => entity.id), "NO_MATCH_IN_CANDIDATES", "AMBIGUOUS", "NEEDS_VISUAL_EVIDENCE"];
  return JSON.stringify({ ok: true, command: "jev evaluate", data: { schema: "anvil.jev.annotation.v1", provider: "fixture", model: "fixture", capability: "browser_element_resolution", status: "completed", reason: "validated", requested: true, used: true, request_started: true, elapsed_ms: 1, rubric_digest: "fixture", input_digest: createHash("sha256").update(canonical(projection), "utf8").digest("hex"), answers: { selection: { type: "choice", probabilities: Object.fromEntries(choices.map((item) => [item, item === choice ? 1 : 0])), confidence: 1, choice } }, usage: { input_tokens: 1, output_tokens: 1 } } });
}
function pageHook() {
  let resolve; const page = new Promise((done) => { resolve = done; });
  return { page, prepare: async (browser) => {
    const nextContext = browser.newContext.bind(browser);
    browser.newContext = async (...args) => { const context = await nextContext(...args); const nextPage = context.newPage.bind(context); context.newPage = async (...pageArgs) => { const value = await nextPage(...pageArgs); resolve(value); return value; }; return context; };
  } };
}
async function adapter({ jev = { enabled: false }, page = undefined, browser = undefined, transport = undefined } = {}) {
  const calls = [];
  const config = { launch: launch(page?.prepare, browser), piSessionId: "pi-session", pages, jev };
  const value = await createLiveObservationAdapter(config, { transportFactory: transportFactory(calls, transport) });
  return { value, calls, config };
}

test("adapter rejects invalid trusted configuration before transport or browser allocation", async () => {
  let launched = 0, transported = 0;
  const config = { launch: async () => { launched += 1; }, piSessionId: "pi-session", pages: [] };
  await assert.rejects(createLiveObservationAdapter(config, { transportFactory: async () => { transported += 1; } }), error("invalid_adapter_config"));
  await assert.rejects(createLiveObservationAdapter({ ...config, pages, piSessionId: "bad session" }, { transportFactory: async () => { transported += 1; } }), error("invalid_adapter_config"));
  await assert.rejects(createLiveObservationAdapter({ ...config, pages: [{ id: "home", url: `${origin}/` }, { id: "projects", url: "https://other.example/projects" }] }, { transportFactory: async () => { transported += 1; } }), error("invalid_adapter_config"));
  await assert.rejects(createLiveObservationAdapter({ ...config, pages: [{ id: "home", url: `${origin}/#fragment` }] }, { transportFactory: async () => { transported += 1; } }), error("invalid_adapter_config"));
  await assert.rejects(createLiveObservationAdapter({ ...config, pages, jev: null }, { transportFactory: async () => { transported += 1; } }), error("invalid_adapter_config"));
  assert.equal(launched, 0); assert.equal(transported, 0);
});

test("adapter rejects malformed, oversized, and null requests before navigation", { timeout: 15_000 }, async (t) => {
  const { value, calls } = await adapter(); t.after(() => value.close());
  const malformed = await value.execute({ operation: "capture", page_id: "home", request: capture("bad", { require_unique: null }) });
  const oversized = await value.execute({ operation: "capture", page_id: "home", request: capture("x".repeat(65)) });
  const giant = await value.execute({ operation: "capture", page_id: "home", request: { ...capture("giant"), padding: "x".repeat(17 * 1024) } });
  const nullSignal = await value.execute({ operation: "capture", page_id: "home", request: capture("signal") }, { signal: null });
  assert.deepEqual(malformed, refused("invalid_request"));
  assert.deepEqual(oversized, refused("invalid_request"));
  assert.deepEqual(giant, refused("invalid_request"));
  assert.deepEqual(nullSignal, refused("invalid_request"));
  assert.equal(calls.length, 0);
});

test("adapter captures bounded fresh pages without publishing URLs or image bytes", { timeout: 15_000 }, async (t) => {
  const { value, calls } = await adapter(); t.after(() => value.close());
  const first = await value.execute({ operation: "capture", page_id: "projects", request: capture("page-0") });
  const second = await value.execute({ operation: "capture", page_id: "projects", request: capture("page-8", { entity_offset: 8 }) });
  const third = await value.execute({ operation: "capture", page_id: "projects", request: capture("page-16", { entity_offset: 16 }) });
  for (const result of [first, second, third]) { assert.equal(result.status, "ok"); assert.equal(result.page_id, "projects"); assert.match(result.binding.owner_session_id, /^[0-9a-f-]{36}$/); assert.ok(bytes(JSON.stringify(result)) <= 15 * 1024); assert.doesNotMatch(JSON.stringify(result), /https:|iVBOR|data:image|file:/); }
  assert.equal(first.result.require_unique, false); assert.deepEqual(first.result.paging, { offset: 0, next_offset: 8 }); assert.deepEqual(second.result.paging, { offset: 8, next_offset: 16 }); assert.deepEqual(third.result.paging, { offset: 16, next_offset: null });
  assert.deepEqual([...first.result.entities, ...second.result.entities, ...third.result.entities].map((entity) => entity.text), Array.from({ length: 23 }, (_, index) => `card-${index}`));
  assert.equal(calls.filter((call) => call.url === `${origin}/projects`).length, 1, "same-page captures renavigated");
});

test("adapter snapshots a validated request before navigation and admits only one active operation", { timeout: 15_000 }, async (t) => {
  let requested, release; const requestedPromise = new Promise((resolve) => { requested = resolve; }); const responseGate = new Promise((resolve) => { release = resolve; });
  const { value, config } = await adapter({ transport: { waitForResponse: responseGate, requested } }); t.after(() => value.close());
  const raw = { operation: "capture", page_id: "projects", request: capture("cloned") };
  const pending = value.execute(raw); await requestedPromise;
  raw.request.target.description = "mutated after validation"; config.piSessionId = "mutated-session";
  assert.deepEqual(await value.execute({ operation: "capture", page_id: "home", request: capture("busy") }), refused("busy"));
  release(); const result = await pending;
  assert.equal(result.status, "ok"); assert.equal(result.binding.pi_session_id, "pi-session"); assert.equal(result.result.target.description, "the requested public control");
});

test("adapter binds offered ids, retains same-page subtree context, releases records, and invalidates on page changes", { timeout: 15_000 }, async (t) => {
  const { value, calls } = await adapter(); t.after(() => value.close());
  const captured = await value.execute({ operation: "capture", page_id: "home", request: capture("home") }); const disabled = captured.result.entities.find((entity) => entity.text === "Disabled");
  assert.equal((await value.execute({ operation: "resolve", observation_id: "00000000-0000-0000-0000-000000000000", entity_id: "e-1" })).code, "unknown_observation");
  const resolved = await value.execute({ operation: "resolve", observation_id: captured.result.observation_id, entity_id: disabled.id }); assert.equal(resolved.status, "ok"); assert.equal(resolved.result.enabled, false);
  const nested = await value.execute({ operation: "capture", page_id: "home", request: capture("subtree", { scope: { kind: "subtree", root: `${captured.result.observation_id}:${disabled.id}` } }) });
  assert.equal(nested.status, "ok"); assert.equal(calls.filter((call) => call.url === `${origin}/`).length, 1, "same-page subtree renavigated");
  const released = await value.execute({ operation: "release", observation_id: captured.result.observation_id }); assert.equal(released.status, "ok"); assert.equal(released.result.released, true);
  assert.equal((await value.execute({ operation: "resolve", observation_id: captured.result.observation_id, entity_id: disabled.id })).code, "unknown_observation");
  const second = await value.execute({ operation: "capture", page_id: "home", request: capture("home-again") });
  await value.execute({ operation: "capture", page_id: "projects", request: capture("projects") });
  assert.equal((await value.execute({ operation: "resolve", observation_id: second.result.observation_id, entity_id: second.result.entities[0].id })).code, "unknown_observation");
});

test("adapter forgets stale records after the owner rejects changed page facts", { timeout: 15_000 }, async (t) => {
  const hook = pageHook(); const { value } = await adapter({ page: hook }); t.after(() => value.close());
  const captured = await value.execute({ operation: "capture", page_id: "home", request: capture("stale") }); const page = await hook.page;
  await page.evaluate(() => document.querySelector("button").setAttribute("aria-label", "Changed"));
  assert.deepEqual(await value.execute({ operation: "resolve", observation_id: captured.result.observation_id, entity_id: captured.result.entities[0].id }), refused("stale_observation"));
  assert.deepEqual(await value.execute({ operation: "resolve", observation_id: captured.result.observation_id, entity_id: captured.result.entities[0].id }), refused("unknown_observation"));
});

test("adapter revokes script navigation instead of relabeling another document", { timeout: 15_000 }, async (t) => {
  for (const destination of ["/unlisted", "/projects", "/#changed"]) {
    await t.test(destination, async () => {
      const hook = pageHook(); const { value, calls } = await adapter({ page: hook });
      try {
        const captured = await value.execute({ operation: "capture", page_id: "home", request: capture("before-navigation") });
        assert.equal(captured.status, "ok");
        const page = await hook.page;
        const navigated = page.waitForEvent("framenavigated", { predicate: (frame) => frame === page.mainFrame(), timeout: 5_000 });
        await page.evaluate((path) => { location.assign(path); }, destination).catch(() => {});
        await navigated;
        assert.equal((await value.execute({ operation: "capture", page_id: "home", request: capture("after-navigation") })).status, "refused");
        assert.equal((await value.execute({ operation: "resolve", observation_id: captured.result.observation_id, entity_id: captured.result.entities[0].id })).status, "refused");
        if (destination === "/unlisted") assert.ok(!calls.some((call) => call.url.endsWith("/unlisted")), "denied request reached transport");
      } finally { await value.close(); }
    });
  }
});

test("adapter cancellation during navigation waits for owner teardown and rejects late navigation", { timeout: 15_000 }, async (t) => {
  let requested, release; const requestedPromise = new Promise((resolve) => { requested = resolve; }); const responseGate = new Promise((resolve) => { release = resolve; });
  let closing, allowClose; const closingPromise = new Promise((resolve) => { closing = resolve; }); const closeGate = new Promise((resolve) => { allowClose = resolve; });
  const browser = async (value) => { const close = value.close.bind(value); value.close = async (...args) => { closing(); await closeGate; return close(...args); }; };
  const { value } = await adapter({ browser, transport: { waitForResponse: responseGate, requested } }); t.after(async () => { allowClose(); await value.close(); });
  const controller = new AbortController(); const pending = value.execute({ operation: "capture", page_id: "projects", request: capture("cancel-navigation") }, { signal: controller.signal });
  await requestedPromise; controller.abort(); await closingPromise;
  let settled = false; void pending.finally(() => { settled = true; }); await new Promise((resolve) => setImmediate(resolve)); assert.equal(settled, false, "execute returned before owner close completed");
  allowClose(); release(); assert.deepEqual(await pending, refused("cancelled"));
});

test("adapter cancellation closes navigation or follow-up work and discards late capture results", { timeout: 15_000 }, async (t) => {
  const hook = pageHook(); const { value } = await adapter({ page: hook }); t.after(() => value.close());
  await value.execute({ operation: "capture", page_id: "home", request: capture("warm") }); const browserPage = await hook.page;
  const screenshot = browserPage.screenshot.bind(browserPage); let entered, release; const enteredPromise = new Promise((resolve) => { entered = resolve; }); const releasePromise = new Promise((resolve) => { release = resolve; });
  browserPage.screenshot = async (...args) => { entered(); await releasePromise; return screenshot(...args); };
  const controller = new AbortController(); const pending = value.execute({ operation: "capture", page_id: "home", request: capture("cancelled") }, { signal: controller.signal });
  await enteredPromise; controller.abort(); release(); const result = await pending; browserPage.screenshot = screenshot;
  assert.deepEqual(result, { schema: "browser-owner-adapter/v1", status: "refused", code: "cancelled" });
  assert.equal((await value.execute({ operation: "capture", page_id: "home", request: capture("after") })).code, "adapter_closed");
});

test("adapter exposes disabled semantic matches only through the existing Jev owner seam", { timeout: 15_000 }, async (t) => {
  let calls = 0; const jev = fakeJev(origin, async ({ args }) => { calls += 1; const projection = JSON.parse(await readFile(args[4], "utf8")); const disabled = projection.entities.find((entity) => entity.text === "Disabled"); return { code: 0, stdout: answer(projection, disabled.id) }; });
  const { value } = await adapter({ jev }); t.after(() => value.close());
  const captured = await value.execute({ operation: "capture", page_id: "home", request: capture("jev") }); const disabled = captured.result.entities.find((entity) => entity.text === "Disabled");
  const selected = await value.execute({ operation: "jev_resolve", observation_id: captured.result.observation_id });
  assert.equal(calls, 1); assert.equal(selected.status, "ok"); assert.equal(selected.result.outcome, "matched"); assert.equal(selected.result.advisory, true); assert.equal(selected.result.entity.id, disabled.id); assert.equal(selected.result.entity.enabled, false);
});
