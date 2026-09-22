import assert from "node:assert/strict";
import { createServer } from "node:http";
import { existsSync } from "node:fs";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import test from "node:test";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";
import { OwnerError, createBrowserOwner } from "./owner.mjs";

const watchdog = setTimeout(() => { process.stderr.write("browser owner test watchdog expired\n"); process.exit(1); }, 60_000);
test.after(() => clearTimeout(watchdog));
const executablePath = () => process.env.CHROMIUM_EXECUTABLE || "/usr/bin/google-chrome";
const page = (kind = "") => kind === "empty" ? "<!doctype html><p>none</p>" : `<!doctype html><main aria-label="Synthetic fixture"><h1>Read-only report</h1><button aria-label="Capture">Capture</button><button disabled>Disabled</button>${kind === "input" || kind === "input-long" ? `<input aria-label="Value" value="${kind === "input-long" ? "x".repeat(257) : "before"}">` : ""}${kind === "private" ? '<textarea>SYNTHETIC_PRIVATE_FORM_VALUE</textarea><script>const synthetic_private_script=123;</script>' : ""}<section role="status">Status</section><img alt="Chart">${kind === "many" ? Array.from({ length: 70 }, (_, index) => `<button aria-label="extra-${index}">extra</button>`).join("") : ""}${kind === "oversized" ? `<div role="${"r".repeat(129)}"></div><button aria-label="${"x".repeat(257)}"></button>` : ""}${kind === "tall" ? '<div style="height: 200vh"></div>' : ""}${kind === "canvas" ? "<canvas></canvas>" : ""}${kind === "shadow" ? "<x-private></x-private>" : ""}${kind === "network" ? '<img src="/asset"><script>window.open("/popup"); new WebSocket(location.origin.replace("http", "ws") + "/socket");</script>' : ""}</main>${kind === "frame" ? '<iframe src="/"></iframe>' : ""}<script>${kind === "shadow" ? 'customElements.define("x-private", class extends HTMLElement { constructor() { super(); this.attachShadow({mode:"open"}).innerHTML="<button>private</button>"; } })' : ""}${kind === "spoof" ? 'Element.prototype.matches=()=>true; Element.prototype.getBoundingClientRect=()=>({width:1,height:1,top:0,right:1,bottom:1,left:0}); document.elementFromPoint=()=>document.body' : ""}</script>`;
const error = (code) => (value) => value instanceof OwnerError && value.code === code;
const typed = (value) => value instanceof OwnerError;
const captureRequest = (changes = {}) => ({
  schema: "widget-resolution/v1",
  request_id: "request-001",
  target: { description: "the synthetic capture control", qualifiers: [] },
  predicates: ["exists", "in_viewport", "occluded", "enabled"],
  scope: { kind: "document", root: "document" },
  require_unique: true,
  ...changes,
});

async function fixture() {
  let assets = 0, sockets = 0, popups = 0;
  const server = createServer((request, response) => {
    if (request.url === "/redirect") { response.writeHead(302, { location: "http://example.test/" }); return response.end(); }
    if (request.url === "/asset") { assets += 1; response.writeHead(204); return response.end(); }
    if (request.url === "/popup") popups += 1;
    const kind = new URL(request.url, "http://fixture.test").searchParams.get("mode") || "";
    response.writeHead(200, { "content-type": "text/html" }); response.end(page(kind));
  });
  server.on("upgrade", (request, socket) => { if (request.url === "/socket") sockets += 1; socket.destroy(); });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address(); const origin = `http://127.0.0.1:${port}`;
  return { origin, counts: () => ({ assets, sockets, popups }), close: () => new Promise((resolve) => server.close(resolve)) };
}

async function owner(origin, limits = {}, clock, prepare, subresourceOrigins = [origin], jev = { enabled: false }, documentOrigins = [origin]) {
  if (!existsSync(executablePath())) throw new Error("missing_browser");
  return createBrowserOwner({ launch: async () => {
    const browser = await chromium.launch({ executablePath: executablePath(), headless: true, args: ["--disable-gpu"] });
    if (prepare) await prepare(browser);
    return browser;
  }, documentOrigins, subresourceOrigins, limits, clock, jev });
}

const jevFields = ["schema", "request_id", "observation_id", "source", "target", "scope", "coverage", "entities"];
const answer = (choice, offered) => {
  const choices = [...offered, "NO_MATCH_IN_CANDIDATES", "AMBIGUOUS", "NEEDS_VISUAL_EVIDENCE"];
  const probabilityChoice = choices.includes(choice) ? choice : choices[0];
  return JSON.stringify({ ok: true, command: "jev evaluate", data: { schema: "anvil.jev.annotation.v1", provider: "typesafe", model: "jev-1.13.0", capability: "browser_element_resolution", status: "completed", reason: "validated", requested: true, used: true, request_started: true, elapsed_ms: 1, rubric_digest: "a", input_digest: "b", answers: { selection: { type: "choice", probabilities: Object.fromEntries(choices.map((item) => [item, item === probabilityChoice ? 1 : 0])), confidence: 1, choice } }, usage: { input_tokens: 1, output_tokens: 1 } } });
};
const fakeJev = (origin, runner, changes = {}) => ({ enabled: true, origin, fields: jevFields, runner, ...changes });

async function screenshotGate(page) {
  const real = page.screenshot.bind(page);
  let enteredResolve, releaseResolve;
  const entered = new Promise((resolve) => { enteredResolve = resolve; });
  const release = new Promise((resolve) => { releaseResolve = resolve; });
  page.screenshot = async (...args) => { enteredResolve(); await release; return real(...args); };
  return { entered, release: releaseResolve, restore: () => { page.screenshot = real; } };
}

function trustedPageHook() {
  let resolvePage;
  const page = new Promise((resolve) => { resolvePage = resolve; });
  return {
    page,
    prepare: async (browser) => {
      const newContext = browser.newContext.bind(browser);
      browser.newContext = async (...args) => {
        const context = await newContext(...args);
        const newPage = context.newPage.bind(context);
        context.newPage = async (...pageArgs) => {
          const value = await newPage(...pageArgs);
          resolvePage(value);
          return value;
        };
        return context;
      };
    },
  };
}

test("owner creates one restricted session and captures fixture facts", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); const core = await owner(site.origin); t.after(async () => { await core.close(); await site.close(); });
  const first = core.session(); assert.throws(() => core.session(), error("session_already_open"));
  await assert.rejects(first.navigate("file:///tmp/nope"), error("navigation_not_permitted"));
  await first.navigate(`${site.origin}/`);
  const observation = await first.capture(captureRequest());
  assert.equal(observation.schema, "widget-resolution/v1");
  assert.equal(observation.request_id, "request-001");
  assert.deepEqual(observation.target, captureRequest().target);
  assert.deepEqual(observation.predicates, captureRequest().predicates);
  assert.equal(observation.require_unique, true);
  assert.equal(observation.coverage.complete, true);
  for (const entity of observation.entities) {
    assert.equal("interactive" in entity || "index" in entity || "scopeId" in entity || "scope_id" in entity, false);
    for (const key of ["exists", "in_viewport", "occluded", "enabled", "predicate_reasons"]) assert.ok(key in entity);
  }
  assert.ok(observation.entities.some((entity) => entity.role === "heading" && entity.enabled === null));
  assert.ok(observation.entities.some((entity) => entity.text === "Disabled" && entity.enabled === false));
  const button = observation.entities.find((entity) => entity.text === "Capture");
  observation.target.description = "caller mutation";
  button.text = "caller mutation";
  const resolved = await first.resolve(observation.observation_id, button.id);
  assert.equal(resolved.text, "Capture"); assert.equal(resolved.enabled, true);
  await assert.rejects(first.resolve("foreign", "e-1"), error("unknown_observation"));
  await assert.rejects(first.navigate(`${site.origin}/redirect`), error("navigation_not_permitted"));
});

test("capture rejects direct and mutate-restore races, then stale replacement and form changes", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); const trusted = trustedPageHook(); const core = await owner(site.origin, {}, undefined, trusted.prepare); const page = await trusted.page; const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/`);
  for (const mutate of [
    () => page.evaluate(() => document.querySelector("button").setAttribute("data-race", "direct")),
    () => page.evaluate(() => { const node = document.querySelector("button"); node.setAttribute("data-race", "restore"); node.removeAttribute("data-race"); }),
  ]) {
    await session.navigate(`${site.origin}/`);
    const gate = await screenshotGate(page); const pending = session.capture(captureRequest()); pending.catch(() => {});
    await gate.entered; await mutate(); gate.release();
    await assert.rejects(pending, error("incoherent_capture")); gate.restore();
  }
  await session.navigate(`${site.origin}/`);
  const fresh = await session.capture(captureRequest()); const button = fresh.entities.find((entity) => entity.text === "Capture"); assert.ok(button);
  await page.evaluate(() => { const node = document.querySelector("button"); node.replaceWith(node.cloneNode(true)); });
  await assert.rejects(session.resolve(fresh.observation_id, button.id), error("stale_observation"));
  await session.navigate(`${site.origin}/?mode=input`);
  const screenshot = page.screenshot.bind(page); let screenshotOptions;
  page.screenshot = async (options) => { screenshotOptions = options; return screenshot(options); };
  const input = await session.capture(captureRequest()); page.screenshot = screenshot;
  assert.equal(screenshotOptions.caret, "initial");
  assert.equal(await page.evaluate(() => document.querySelector("input").getAttribute("style")), null);
  await page.evaluate(() => { document.querySelector("input").value = "after"; });
  await assert.rejects(session.resolve(input.observation_id, input.entities.find((entity) => entity.role === "textbox").id), error("stale_observation"));
  await session.navigate(`${site.origin}/?mode=frame`); const partial = await session.capture(captureRequest());
  assert.equal(partial.coverage.complete, false); assert.ok(partial.coverage.unsupported_regions.includes("frame"));
});

test("owner refreshes successful TTL reads only and does not revive expired observations", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); let tick = 0; const core = await owner(site.origin, { ttl: 5 }, () => tick); const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/`);
  const first = await session.capture(captureRequest()); const button = first.entities.find((entity) => entity.text === "Capture");
  tick = 4; await assert.rejects(session.resolve(first.observation_id, "foreign"), error("unknown_entity"));
  tick = 6; await assert.rejects(session.resolve(first.observation_id, button.id), error("expired_observation"));
  await assert.rejects(session.resolve(first.observation_id, button.id), error("unknown_observation"));
  const second = await session.capture(captureRequest({ request_id: "ttl-refresh" })); const refreshed = second.entities.find((entity) => entity.text === "Capture");
  tick = 10; await session.resolve(second.observation_id, refreshed.id);
  tick = 14; await session.resolve(second.observation_id, refreshed.id);
  tick = 20; await assert.rejects(session.resolve(second.observation_id, refreshed.id), error("expired_observation"));
  await session.revoke(); await assert.rejects(session.capture(captureRequest()), error("owner_closed"));
});

test("owner evicts the oldest capture even after it is read", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); const core = await owner(site.origin, { maxObservations: 2 }); const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/`);
  const first = await session.capture(captureRequest({ request_id: "evict-a" }));
  const second = await session.capture(captureRequest({ request_id: "evict-b" }));
  const firstButton = first.entities.find((entity) => entity.text === "Capture");
  const secondButton = second.entities.find((entity) => entity.text === "Capture");
  await session.resolve(first.observation_id, firstButton.id);
  await session.capture(captureRequest({ request_id: "evict-c" }));
  await assert.rejects(session.resolve(first.observation_id, firstButton.id), error("unknown_observation"));
  await session.resolve(second.observation_id, secondButton.id);
});

test("capture accepts only the closed widget-resolution request contract", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); const core = await owner(site.origin); const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/`);
  const invalid = [
    {},
    captureRequest({ request_id: "" }),
    captureRequest({ schema: "other/v1" }),
    captureRequest({ request_id: "x".repeat(65) }),
    captureRequest({ request_id: "é".repeat(33) }),
    captureRequest({ target: { description: "x".repeat(513), qualifiers: [] } }),
    captureRequest({ target: { description: "é".repeat(257), qualifiers: [] } }),
    captureRequest({ target: { description: "target", qualifiers: [], nested: true } }),
    captureRequest({ target: { qualifiers: [] } }),
    captureRequest({ target: { description: "target", qualifiers: Array(9).fill("q") } }),
    captureRequest({ target: { description: "target", qualifiers: ["x".repeat(129)] } }),
    captureRequest({ predicates: ["exists", "invented"] }),
    captureRequest({ scope: { kind: "subtree", root: "document" } }),
    captureRequest({ scope: { kind: "document", root: "caller-selector" } }),
    captureRequest({ require_unique: "yes" }),
    { ...captureRequest(), extra: true },
  ];
  for (const request of invalid) await assert.rejects(session.capture(request), typed);
});

test("invalid owner limits reject before launch and lower bounds remain enforced", { timeout: 15_000 }, async (t) => {
  for (const limits of [{ maxEntities: 65 }, { maxPng: 0 }, { timeout: 10_001 }, { maxEntities: NaN }, { maxPixels: -1 }, { ttl: 1.5 }, { maxBytes: Infinity }, [], 42, null, { ttl: null }, { unknown: 1 }]) {
    let launched = false;
    await assert.rejects(
      createBrowserOwner({ launch: async () => { launched = true; throw new Error("must_not_launch"); }, documentOrigins: ["http://127.0.0.1:1"], limits }),
      error("invalid_limits"),
    );
    assert.equal(launched, false);
  }
  for (const malformed of [undefined, null, "http://127.0.0.1:1", 42, {}, new Map()]) {
    for (const field of ["documentOrigins", "subresourceOrigins"]) {
      if (field === "subresourceOrigins" && malformed === undefined) continue;
      let launched = false;
      await assert.rejects(
        createBrowserOwner({
          launch: async () => { launched = true; throw new Error("must_not_launch"); },
          documentOrigins: field === "documentOrigins" ? malformed : ["http://127.0.0.1:1"],
          subresourceOrigins: field === "subresourceOrigins" ? malformed : ["http://127.0.0.1:1"],
        }),
        error("invalid_origin_policy"),
      );
      assert.equal(launched, false);
    }
  }
  const site = await fixture(); const core = await owner(site.origin, { maxEntities: 1 }); const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/?mode=many`);
  const partial = await session.capture(captureRequest());
  assert.equal(partial.coverage.complete, false);
  assert.ok(partial.coverage.omitted_count > 0);
});

test("factory rejects invalid clocks and closes launched browser on setup failure", { timeout: 15_000 }, async () => {
  let launched = false;
  await assert.rejects(createBrowserOwner({ launch: async () => { launched = true; }, documentOrigins: ["http://127.0.0.1:1"], clock: null }), error("invalid_limits"));
  assert.equal(launched, false);
  for (const stage of ["context", "cdp"]) {
    let browserClosed = 0, contextClosed = 0;
    const context = { route: async () => {}, newPage: async () => ({}), newCDPSession: async () => { throw new Error("cdp"); }, close: async () => { contextClosed += 1; } };
    const browser = { newContext: async () => { if (stage === "context") throw new Error("context"); return context; }, close: async () => { browserClosed += 1; } };
    await assert.rejects(createBrowserOwner({ launch: async () => browser, documentOrigins: ["http://127.0.0.1:1"] }), error("owner_failed"));
    assert.equal(browserClosed, 1); assert.equal(contextClosed, stage === "cdp" ? 1 : 0);
  }
});

test("oversized form state is rejected before any PNG capture", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); const trusted = trustedPageHook(); const core = await owner(site.origin, {}, undefined, trusted.prepare); const trustedPage = await trusted.page; const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/?mode=input-long`);
  const screenshot = trustedPage.screenshot.bind(trustedPage); let screenshots = 0;
  trustedPage.screenshot = async (...args) => { screenshots += 1; return screenshot(...args); };
  try { await assert.rejects(session.capture(captureRequest()), typed); } finally { trustedPage.screenshot = screenshot; }
  assert.equal(screenshots, 0);
});

test("isolated DOM facts resist page getter spoofing and report unsupported regions", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); const core = await owner(site.origin); const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/?mode=spoof`);
  const spoofed = await session.capture(captureRequest());
  assert.equal(spoofed.entities.find((entity) => entity.text === "Disabled").enabled, false);
  await session.navigate(`${site.origin}/?mode=canvas`);
  assert.ok((await session.capture(captureRequest())).coverage.unsupported_regions.includes("canvas"));
  await session.navigate(`${site.origin}/?mode=shadow`);
  assert.ok((await session.capture(captureRequest())).coverage.unsupported_regions.includes("shadow"));
});

test("coverage reports oversized DOM fields and prior observations die on scroll, navigation, and page close", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); const trusted = trustedPageHook(); const core = await owner(site.origin, {}, undefined, trusted.prepare); const page = await trusted.page; const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/?mode=oversized`);
  const partial = await session.capture(captureRequest());
  assert.equal(partial.coverage.complete, false);
  assert.ok(partial.coverage.omitted_count > 0);
  assert.ok(partial.coverage.untraversed_regions.includes("role_too_large"));
  assert.ok(partial.coverage.untraversed_regions.includes("text_too_large"));
  await session.navigate(`${site.origin}/?mode=tall`);
  const scrolled = await session.capture(captureRequest({ request_id: "scroll-stale" })); const button = scrolled.entities.find((entity) => entity.text === "Capture");
  await page.evaluate(() => scrollTo(0, 100));
  await assert.rejects(session.resolve(scrolled.observation_id, button.id), error("stale_observation"));
  await session.navigate(`${site.origin}/?mode=input`);
  const navigated = await session.capture(captureRequest({ request_id: "navigation-stale" }));
  await session.navigate(`${site.origin}/`);
  await assert.rejects(session.resolve(navigated.observation_id, "e-1"), error("unknown_observation"));
});

test("closed owner page returns a typed failure", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); const trusted = trustedPageHook(); const core = await owner(site.origin, {}, undefined, trusted.prepare); const page = await trusted.page; const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/`);
  await page.close();
  await assert.rejects(session.capture(captureRequest({ request_id: "closed-page" })), typed);
});

test("subresource, popup, frame, and websocket policy stays inside the owner", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); const core = await owner(site.origin, {}, undefined, undefined, ["http://127.0.0.1:1"]); const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/?mode=network`); await session.capture(captureRequest());
  assert.deepEqual(site.counts(), { assets: 0, sockets: 0, popups: 0 });
});

test("screenshot, metadata, and aggregate retention bounds refuse without public image bytes", { timeout: 15_000 }, async (t) => {
  const site = await fixture();
  t.after(async () => { await site.close(); });
  for (const [limits, expected] of [[{ maxPixels: 1 }, "screenshot_too_large"], [{ maxPng: 1 }, "screenshot_too_large"], [{ maxMetadata: 1 }, "metadata_too_large"], [{ maxBytes: 1 }, "retention_exhausted"]]) {
    const core = await owner(site.origin, limits); const session = core.session();
    t.after(async () => { await core.close(); });
    await session.navigate(`${site.origin}/`);
    await assert.rejects(session.capture(captureRequest()), error(expected));
  }
});

test("owner exposes no raw browser handles and respects release and scoped references", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); const core = await owner(site.origin); const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/`);
  const documentObservation = await session.capture(captureRequest());
  assert.equal(JSON.stringify(documentObservation).includes("ElementHandle"), false);
  assert.equal("page" in session || "context" in session || "evaluate" in session || "s" in session, false);
  const captureEntity = documentObservation.entities.find((entity) => entity.text === "Capture");
  const root = `${documentObservation.observation_id}:${captureEntity.id}`;
  const scoped = await session.capture(captureRequest({ request_id: "request-002", scope: { kind: "subtree", root } }));
  assert.deepEqual(scoped.scope, { kind: "subtree", root });
  assert.equal(scoped.entities.length, 1);
  assert.equal(scoped.entities[0].text, "Capture");
  await session.release(documentObservation.observation_id);
  await assert.rejects(session.resolve(documentObservation.observation_id, "e-1"), typed);
  await assert.rejects(session.capture(captureRequest({ scope: { kind: "subtree", root } })), typed);
});

test("queue admission caps at one active plus eight pending", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); const trusted = trustedPageHook(); const core = await owner(site.origin, { timeout: 1_000 }, undefined, trusted.prepare); const page = await trusted.page; const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/`); const gate = await screenshotGate(page);
  const active = session.capture(captureRequest()); active.catch(() => {}); await gate.entered;
  const queued = Array.from({ length: 8 }, (_, index) => session.capture(captureRequest({ request_id: `queued-${index}` }))); for (const pending of queued) pending.catch(() => {});
  await assert.rejects(session.capture(captureRequest({ request_id: "overflow" })), error("queue_full"));
  gate.release(); gate.restore(); await active; await Promise.all(queued);
});

test("AbortSignal and explicit revocation discard work without late results", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); const trusted = trustedPageHook(); const core = await owner(site.origin, { timeout: 1_000 }, undefined, trusted.prepare); const page = await trusted.page; const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/`); const gate = await screenshotGate(page); const active = session.capture(captureRequest()); active.catch(() => {}); await gate.entered;
  const controller = new AbortController(); const queued = session.capture(captureRequest({ request_id: "queued-abort" }), { signal: controller.signal }); queued.catch(() => {});
  controller.abort(); await assert.rejects(queued, error("cancelled"));
  await session.revoke(); gate.release(); gate.restore(); await assert.rejects(active, typed);
  await assert.rejects(session.capture(captureRequest()), error("owner_closed"));
});

test("deadline includes queue wait and revokes an in-flight capture", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); const trusted = trustedPageHook(); const core = await owner(site.origin, { timeout: 1_000 }, undefined, trusted.prepare); const page = await trusted.page; const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/`); const gate = await screenshotGate(page); const active = session.capture(captureRequest()); active.catch(() => {}); await gate.entered;
  const queued = session.capture(captureRequest({ request_id: "queued-deadline" })); queued.catch(() => {});
  await assert.rejects(active, error("deadline_exceeded")); await assert.rejects(queued, typed);
  gate.release(); gate.restore(); await assert.rejects(session.capture(captureRequest()), error("owner_closed"));
});

test("missing browser fails nonzero by construction", { timeout: 15_000 }, async () => {
  const saved = process.env.CHROMIUM_EXECUTABLE; process.env.CHROMIUM_EXECUTABLE = "/missing-browser";
  try { await assert.rejects(owner("http://127.0.0.1:1"), /missing_browser/); } finally { if (saved === undefined) delete process.env.CHROMIUM_EXECUTABLE; else process.env.CHROMIUM_EXECUTABLE = saved; }
});

test("owner Jev consumer keeps export default-off and projects only fresh fixture facts", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); let calls = 0;
  const disabled = await owner(site.origin); const disabledSession = disabled.session(); t.after(async () => { await disabled.close(); await site.close(); });
  await disabledSession.navigate(`${site.origin}/`); const disabledObservation = await disabledSession.capture(captureRequest());
  assert.deepEqual(await disabledSession.jevResolve(disabledObservation.observation_id), { outcome: "disabled" }); assert.equal(calls, 0);
  const enabled = await owner(site.origin, {}, undefined, undefined, [site.origin], fakeJev(site.origin, async ({ args }) => { calls += 1; const projection = JSON.parse(await readFile(args[4], "utf8")); assert.deepEqual(Object.keys(projection), jevFields); assert.equal(JSON.stringify(projection).includes("navigation_epoch"), false); assert.equal(JSON.stringify(projection).includes("form"), false); return { code: 0, stdout: answer(projection.entities.find((entity) => entity.text === "Capture").id, projection.entities.map((entity) => entity.id)) }; }));
  const session = enabled.session(); t.after(() => enabled.close()); await session.navigate(`${site.origin}/`); const observation = await session.capture(captureRequest());
  const matched = await session.jevResolve(observation.observation_id);
  assert.equal(calls, 1); assert.equal(matched.outcome, "matched"); assert.equal(matched.advisory, true); assert.equal(matched.entity.text, "Capture"); assert.equal(matched.entity.enabled, true);
});

test("owner Jev projection excludes form and script bytes from every ancestor label", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); let exported = "";
  const core = await owner(site.origin, {}, undefined, undefined, [site.origin], fakeJev(site.origin, async ({ args }) => {
    const projection = JSON.parse(await readFile(args[4], "utf8")); exported = JSON.stringify(projection);
    return { code: 0, stdout: answer(projection.entities.find((entity) => entity.text === "Capture").id, projection.entities.map((entity) => entity.id)) };
  }));
  const session = core.session(); t.after(async () => { await core.close(); await site.close(); }); await session.navigate(`${site.origin}/?mode=private`);
  const observation = await session.capture(captureRequest({ request_id: "private", require_unique: false })); const result = await session.jevResolve(observation.observation_id);
  assert.equal(result.outcome, "matched"); assert.equal(observation.coverage.complete, false); assert.ok(observation.coverage.untraversed_regions.includes("restricted_text")); assert.equal(exported.includes("SYNTHETIC_PRIVATE_FORM_VALUE"), false); assert.equal(exported.includes("synthetic_private_script"), false);
});

test("owner Jev consumer reports bounded semantic outcomes without exporting denied or incomplete records", { timeout: 15_000 }, async (t) => {
  const first = await fixture(), second = await fixture(); let calls = 0;
  const denied = await owner(first.origin, {}, undefined, undefined, [first.origin, second.origin], fakeJev(second.origin, async () => { calls += 1; return { code: 0, stdout: answer("e-1") }; }), [first.origin, second.origin]);
  const session = denied.session(); t.after(async () => { await denied.close(); await first.close(); await second.close(); }); await session.navigate(`${first.origin}/`); const observation = await session.capture(captureRequest());
  assert.deepEqual(await session.jevResolve(observation.observation_id), { outcome: "export_denied" }); assert.equal(calls, 0);
  const partial = await owner(first.origin, {}, undefined, undefined, [first.origin], fakeJev(first.origin, async () => { calls += 1; return { code: 0, stdout: answer("NO_MATCH_IN_CANDIDATES") }; }));
  const partialSession = partial.session(); t.after(() => partial.close()); await partialSession.navigate(`${first.origin}/?mode=frame`); const partialObservation = await partialSession.capture(captureRequest());
  assert.deepEqual(await partialSession.jevResolve(partialObservation.observation_id), { outcome: "incomplete_coverage" }); assert.equal(calls, 0);
  await partialSession.navigate(`${first.origin}/?mode=empty`); const empty = await partialSession.capture(captureRequest({ request_id: "empty", require_unique: false }));
  assert.deepEqual(await partialSession.jevResolve(empty.observation_id), { outcome: "empty_inventory" }); assert.equal(calls, 0);
});

test("owner Jev consumer rejects malformed selections and keeps disabled and noninteractive facts advisory", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); const answers = ["unknown", "AMBIGUOUS", "NEEDS_VISUAL_EVIDENCE", "NO_MATCH_IN_CANDIDATES", "Disabled", "Read-only report", "duplicate"];
  let index = 0;
  const core = await owner(site.origin, {}, undefined, undefined, [site.origin], fakeJev(site.origin, async ({ args }) => {
    const projection = JSON.parse(await readFile(args[4], "utf8")); const choice = answers[index++];
    if (choice === "Disabled") return { code: 0, stdout: answer(projection.entities.find((entity) => entity.text === "Disabled").id, projection.entities.map((entity) => entity.id)) };
    if (choice === "Read-only report") return { code: 0, stdout: answer(projection.entities.find((entity) => entity.role === "heading").id, projection.entities.map((entity) => entity.id)) };
    if (choice === "duplicate") return { code: 0, stdout: '{"ok":true,"ok":true,"command":"jev evaluate","data":{}}' };
    return { code: 0, stdout: answer(choice, projection.entities.map((entity) => entity.id)) };
  }));
  const session = core.session(); t.after(async () => { await core.close(); await site.close(); }); await session.navigate(`${site.origin}/`);
  for (const expected of ["unknown_selection", "ambiguous", "needs_visual_evidence", "no_match_inconclusive"]) { const observation = await session.capture(captureRequest({ request_id: `semantic-${index}` })); assert.equal((await session.jevResolve(observation.observation_id)).outcome, expected); }
  const disabled = await session.capture(captureRequest({ request_id: "disabled" })); const disabledResult = await session.jevResolve(disabled.observation_id); assert.equal(disabledResult.entity.enabled, false);
  const noninteractive = await session.capture(captureRequest({ request_id: "noninteractive" })); const noninteractiveResult = await session.jevResolve(noninteractive.observation_id); assert.equal(noninteractiveResult.entity.enabled, null);
  const malformed = await session.capture(captureRequest({ request_id: "malformed" })); assert.equal((await session.jevResolve(malformed.observation_id)).outcome, "malformed_response");
});

test("owner Jev rechecks every abstention after mutation, navigation, and expiry", { timeout: 30_000 }, async (t) => {
  const site = await fixture(), abstentions = ["NO_MATCH_IN_CANDIDATES", "AMBIGUOUS", "NEEDS_VISUAL_EVIDENCE"];
  t.after(() => site.close());
  for (const change of ["mutation", "navigation"]) for (const choice of abstentions) {
    const trusted = trustedPageHook(); let release, entered; const wait = new Promise((resolve) => { release = resolve; }), started = new Promise((resolve) => { entered = resolve; });
    const core = await owner(site.origin, {}, undefined, trusted.prepare, [site.origin], fakeJev(site.origin, async ({ args }) => { const projection = JSON.parse(await readFile(args[4], "utf8")); entered(); await wait; return { code: 0, stdout: answer(choice, projection.entities.map((entity) => entity.id)) }; }));
    const session = core.session(), page = await trusted.page; t.after(() => core.close()); await session.navigate(`${site.origin}/`); const observation = await session.capture(captureRequest({ request_id: `${change}-${choice}` })); const pending = session.jevResolve(observation.observation_id); await started;
    if (change === "mutation") await page.evaluate(() => document.querySelector("button").setAttribute("data-after-reply", "yes")); else await page.goto(`${site.origin}/`);
    release(); assert.equal((await pending).outcome, change === "mutation" ? "stale_observation" : "unknown_observation");
  }
  for (const choice of abstentions) {
    let tick = 0;
    const core = await owner(site.origin, { ttl: 5 }, () => tick, undefined, [site.origin], fakeJev(site.origin, async ({ args }) => { const projection = JSON.parse(await readFile(args[4], "utf8")); tick = 6; return { code: 0, stdout: answer(choice, projection.entities.map((entity) => entity.id)) }; }));
    const session = core.session(); t.after(() => core.close()); await session.navigate(`${site.origin}/`); const observation = await session.capture(captureRequest({ request_id: `expiry-${choice}` })); assert.deepEqual(await session.jevResolve(observation.observation_id), { outcome: "expired_observation" });
  }
});

test("owner Jev consumer cancels stale replies and uses a real shell-free child with cleanup", { timeout: 15_000 }, async (t) => {
  const site = await fixture(), trusted = trustedPageHook(); let release, entered;
  const delayed = new Promise((resolve) => { release = resolve; }); const started = new Promise((resolve) => { entered = resolve; });
  const core = await owner(site.origin, {}, undefined, trusted.prepare, [site.origin], fakeJev(site.origin, async ({ args }) => { const projection = JSON.parse(await readFile(args[4], "utf8")); entered(); await delayed; return { code: 0, stdout: answer(projection.entities.find((entity) => entity.text === "Capture").id, projection.entities.map((entity) => entity.id)) }; }));
  const session = core.session(); const page = await trusted.page; t.after(async () => { await core.close(); await site.close(); }); await session.navigate(`${site.origin}/`); const stale = await session.capture(captureRequest()); const pending = session.jevResolve(stale.observation_id); await started; await page.evaluate(() => document.querySelector("button").setAttribute("data-stale", "yes")); release(); assert.equal((await pending).outcome, "stale_observation");
  const directory = await mkdtemp(join(tmpdir(), "anvil-jev-child-")); t.after(() => rm(directory, { recursive: true, force: true }));
  const executable = join(directory, "anvil-test");
  await writeFile(executable, `#!${process.execPath}\nconst fs=require('node:fs');const args=process.argv.slice(2);const input=args[4];const projection=JSON.parse(fs.readFileSync(input,'utf8'));const choice=projection.entities.find(x=>x.text==='Capture').id;const choices=[...projection.entities.map(x=>x.id),'NO_MATCH_IN_CANDIDATES','AMBIGUOUS','NEEDS_VISUAL_EVIDENCE'];fs.writeFileSync('trace.json',JSON.stringify({args,input,exists:fs.existsSync(input)}));console.log(JSON.stringify({ok:true,command:'jev evaluate',data:{schema:'anvil.jev.annotation.v1',provider:'typesafe',model:'jev-1.13.0',capability:'browser_element_resolution',status:'completed',reason:'validated',requested:true,used:true,request_started:true,elapsed_ms:1,rubric_digest:'a',input_digest:'b',answers:{selection:{type:'choice',probabilities:Object.fromEntries(choices.map(x=>[x,x===choice?1:0])),confidence:1,choice}},usage:{input_tokens:1,output_tokens:1}}}));`);
  await chmod(executable, 0o700);
  const child = await owner(site.origin, {}, undefined, undefined, [site.origin], { enabled: true, origin: site.origin, fields: jevFields, executable, cwd: directory }); const childSession = child.session(); t.after(() => child.close()); await childSession.navigate(`${site.origin}/`); const captured = await childSession.capture(captureRequest({ request_id: "child" })); assert.equal((await childSession.jevResolve(captured.observation_id)).outcome, "matched");
  const trace = JSON.parse(await readFile(join(directory, "trace.json"), "utf8")); assert.deepEqual(trace.args.slice(0, 4), ["jev", "evaluate", "browser_element_resolution", "--input"]); assert.deepEqual(trace.args.slice(5), ["--allow-export", "--json"]); assert.equal(trace.exists, true); assert.equal(existsSync(trace.input), false);
  const sleeper = join(directory, "anvil-sleeper"); await writeFile(sleeper, `#!${process.execPath}\nsetTimeout(()=>{},1000);`); await chmod(sleeper, 0o700);
  const timed = await owner(site.origin, {}, undefined, undefined, [site.origin], { enabled: true, origin: site.origin, fields: jevFields, executable: sleeper, cwd: directory, timeout: 20 }); const timedSession = timed.session(); t.after(() => timed.close()); await timedSession.navigate(`${site.origin}/`); const timedObservation = await timedSession.capture(captureRequest({ request_id: "timeout" })); assert.deepEqual(await timedSession.jevResolve(timedObservation.observation_id), { outcome: "timeout" });
  const cancelled = await owner(site.origin, {}, undefined, undefined, [site.origin], fakeJev(site.origin, async ({ args, signal }) => { const projection = JSON.parse(await readFile(args[4], "utf8")); await new Promise((resolve) => signal.addEventListener("abort", resolve, { once: true })); return { code: 0, stdout: answer(projection.entities[0].id, projection.entities.map((entity) => entity.id)) }; })); const cancelledSession = cancelled.session(); t.after(() => cancelled.close()); await cancelledSession.navigate(`${site.origin}/`); const cancelledObservation = await cancelledSession.capture(captureRequest({ request_id: "cancel" })); const controller = new AbortController(); const cancelling = cancelledSession.jevResolve(cancelledObservation.observation_id, { signal: controller.signal }); controller.abort(); await assert.rejects(cancelling, error("cancelled"));
});
