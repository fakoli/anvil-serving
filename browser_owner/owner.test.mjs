import assert from "node:assert/strict";
import { createServer } from "node:http";
import { existsSync } from "node:fs";
import test from "node:test";
import { chromium } from "playwright";
import { OwnerError, createBrowserOwner } from "./owner.mjs";

const watchdog = setTimeout(() => { process.stderr.write("browser owner test watchdog expired\n"); process.exit(1); }, 60_000);
test.after(() => clearTimeout(watchdog));
const executablePath = () => process.env.CHROMIUM_EXECUTABLE || "/usr/bin/google-chrome";
const page = (kind = "") => `<!doctype html><main aria-label="Synthetic fixture"><h1>Read-only report</h1><button aria-label="Capture">Capture</button><button disabled>Disabled</button>${kind === "input" || kind === "input-long" ? `<input aria-label="Value" value="${kind === "input-long" ? "x".repeat(257) : "before"}">` : ""}<section role="status">Status</section><img alt="Chart">${kind === "many" ? Array.from({ length: 70 }, (_, index) => `<button aria-label="extra-${index}">extra</button>`).join("") : ""}${kind === "canvas" ? "<canvas></canvas>" : ""}${kind === "shadow" ? "<x-private></x-private>" : ""}${kind === "network" ? '<img src="/asset"><script>window.open("/popup"); new WebSocket(location.origin.replace("http", "ws") + "/socket");</script>' : ""}</main>${kind === "frame" ? '<iframe src="/"></iframe>' : ""}<script>${kind === "shadow" ? 'customElements.define("x-private", class extends HTMLElement { constructor() { super(); this.attachShadow({mode:"open"}).innerHTML="<button>private</button>"; } })' : ""}${kind === "spoof" ? 'Element.prototype.matches=()=>true; Element.prototype.getBoundingClientRect=()=>({width:1,height:1,top:0,right:1,bottom:1,left:0}); document.elementFromPoint=()=>document.body' : ""}</script>`;
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

async function owner(origin, limits = {}, clock, prepare, subresourceOrigins = [origin]) {
  if (!existsSync(executablePath())) throw new Error("missing_browser");
  return createBrowserOwner({ launch: async () => {
    const browser = await chromium.launch({ executablePath: executablePath(), headless: true, args: ["--disable-gpu"] });
    if (prepare) await prepare(browser);
    return browser;
  }, documentOrigins: [origin], subresourceOrigins, limits, clock });
}

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
  assert.equal(typeof observation.coverage.complete, "boolean");
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

test("owner expires, evicts and revokes observations without late retention", { timeout: 15_000 }, async (t) => {
  const site = await fixture(); let tick = 0; const core = await owner(site.origin, { maxObservations: 1, ttl: 5 }, () => tick); const session = core.session(); t.after(async () => { await core.close(); await site.close(); });
  await session.navigate(`${site.origin}/`); const first = await session.capture(captureRequest()); const second = await session.capture(captureRequest());
  await assert.rejects(session.resolve(first.observation_id, "e-1"), error("unknown_observation"));
  tick = 6; await assert.rejects(session.resolve(second.observation_id, "e-1"), error("expired_observation"));
  await session.revoke(); await assert.rejects(session.capture(captureRequest()), error("owner_closed"));
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
