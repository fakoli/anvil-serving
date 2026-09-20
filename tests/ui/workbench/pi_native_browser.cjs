"use strict";
// Pinned Pi Web browser acceptance with synthetic sessions and routed fake RPC only.
// Run against a separately prepared exact patched source; no provider is contacted.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { once } = require("node:events");
const { spawn } = require("node:child_process");
const { createServer } = require("node:net");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

function option(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

const source = option("source", process.env.PI_NATIVE_SOURCE);
const suppliedUrl = option("url", process.env.PI_NATIVE_URL);
const artifacts = option("artifacts", process.env.PI_NATIVE_ARTIFACT_DIR);
const parentOrigin = option("parent-origin", process.env.PI_NATIVE_PARENT_ORIGIN || "https://127.0.0.1:34443");
if ((!source && !suppliedUrl) || !artifacts) {
  throw new Error("usage: node pi_native_browser.cjs (--source PATCHED_SOURCE | --url NATIVE_URL) --artifacts ARTIFACT_DIR [--parent-origin HTTPS_ORIGIN]");
}
const originMatch = /^https:\/\/[a-z0-9.-]+(?::([1-9][0-9]{0,4}))?$/i.exec(parentOrigin || "");
if (!originMatch || (originMatch[1] && Number(originMatch[1]) > 65535)) {
  throw new Error("--parent-origin must be one exact HTTPS origin");
}
fs.mkdirSync(artifacts, { recursive: true });

function freePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(error => error ? reject(error) : resolve(port));
    });
  });
}

function syntheticSessions() {
  const agent = fs.mkdtempSync(path.join(os.tmpdir(), "pi-native-browser-"));
  const project = path.join(agent, "project");
  fs.mkdirSync(path.join(agent, "sessions", "fixture"), { recursive: true });
  fs.mkdirSync(project);
  for (const id of ["steer-session", "follow-session", "attachment-session", "embed-session", "switch-session"]) {
    const rows = [
      { type: "session", version: 3, id, timestamp: "2026-09-19T00:00:00.000Z", cwd: project },
      { type: "message", id: `${id}-user`, parentId: null, timestamp: "2026-09-19T00:00:00.000Z", message: { role: "user", content: `Retained ${id}` } },
    ];
    fs.writeFileSync(path.join(agent, "sessions", "fixture", `fixture_${id}.jsonl`), `${rows.map(JSON.stringify).join("\n")}\n`);
  }
  return agent;
}

async function waitForReady(url, child) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      if ((await fetch(`${url}/api/sessions`)).ok) return;
    } catch (_) {}
    assert.equal(child.exitCode, null, "pinned native server exited before readiness");
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error("pinned native server did not become ready within 12 seconds");
}

async function directRpcJourney(browser, nativeUrl) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const commands = [], running = new Set();
  await page.route("**/api/agent/running", route => route.fulfill({
    json: { sessionListVersion: 1, runningSessionIds: [...running], completionNotificationSuppressedSessionIds: [] },
  }));
  async function waitForCommands(session, count) {
    for (let attempt = 0; attempt < 30; attempt += 1) {
      if (commands.filter(command => command.session === session).length >= count) return;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error(`fake RPC did not receive ${count} commands for ${session}`);
  }
  for (const session of ["steer-session", "follow-session", "attachment-session"]) {
    await page.route(`**/api/agent/${session}/events`, route => route.fulfill({
      status: 200, headers: { "content-type": "text/event-stream" }, body: "data: {\"type\":\"connected\"}\n\n",
    }));
    await page.route(`**/api/agent/${session}`, async route => {
      if (route.request().method() === "POST") {
        const payload = route.request().postDataJSON();
        commands.push({ session, ...payload });
        if (payload.type === "prompt" && !payload.streamingBehavior) running.add(session);
        return route.fulfill({ json: { success: true, data: {} } });
      }
      const active = running.has(session);
      return route.fulfill({ json: { running: active, state: { isStreaming: active, isPromptRunning: active, queuedMessages: { steering: [], followUp: [] } } } });
    });
  }
  async function queue(id, behavior, label) {
    await page.goto(`${nativeUrl}/?session=${id}`, { waitUntil: "domcontentloaded" });
    await page.getByRole("paragraph").filter({ hasText: `Retained ${id}` }).waitFor();
    const input = page.locator("textarea").last();
    await input.fill(`start ${behavior}`);
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await waitForCommands(id, 1);
    const button = page.getByRole("button", { name: label, exact: true });
    await button.waitFor();
    await input.fill(`${behavior} exactly once`);
    assert.equal(await button.isDisabled(), false, `${behavior} must be available during a stream`);
    await input.press(behavior === "steer" ? "Enter" : "Alt+Enter");
    await waitForCommands(id, 2);
    await input.press("Escape");
    await waitForCommands(id, 3);
  }
  await queue("steer-session", "steer", "Steer");
  await queue("follow-session", "followup", "Follow-up");

  await page.goto(`${nativeUrl}/?session=attachment-session`, { waitUntil: "domcontentloaded" });
  await page.getByRole("paragraph").filter({ hasText: "Retained attachment-session" }).waitFor();
  const image = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLx8QAAAABJRU5ErkJggg==", "base64");
  await page.locator('input[type=file][accept="image/*"]').setInputFiles(Array.from({ length: 11 }, (_, index) => ({ name: `image-${index}.png`, mimeType: "image/png", buffer: image })));
  await page.locator('img[alt=""]').evaluateAll(images => { if (images.length !== 10) throw new Error(`expected 10 image previews, got ${images.length}`); });
  await page.locator('input[type=file][accept="image/*"]').setInputFiles({ name: "rejected.txt", mimeType: "text/plain", buffer: Buffer.from("not an image") });
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(await page.locator('img[alt=""]').count(), 10, "non-image upload must be rejected");
  await page.locator("textarea").last().fill("send bounded images");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await waitForCommands("attachment-session", 1);
  assert.equal(commands.find(command => command.session === "attachment-session" && command.type === "prompt")?.images?.length, 10);
  assert.deepEqual(commands.map(command => [command.session, command.type, command.streamingBehavior || null]), [
    ["steer-session", "prompt", null], ["steer-session", "prompt", "steer"], ["steer-session", "abort", null],
    ["follow-session", "prompt", null], ["follow-session", "prompt", "followUp"], ["follow-session", "abort", null],
    ["attachment-session", "prompt", null],
  ]);
  await page.screenshot({ path: path.join(artifacts, "native-direct-rpc.png"), fullPage: true });
  await context.close();
  return commands.map(({ session, type, streamingBehavior, images }) => ({ session, type, streaming_behavior: streamingBehavior || null, image_count: images?.length || 0 }));
}

async function embeddedJourney(browser, nativeUrl) {
  const result = [];
  for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport, ignoreHTTPSErrors: true });
    await context.route(`${parentOrigin}/**`, route => route.fulfill({ contentType: "text/html", body: `<!doctype html><meta charset="utf-8"><main><iframe title="embedded-pi" src="${nativeUrl}/?session=embed-session"></iframe></main><script>window.messages=[];window.sendOpen=({nativeId,bridgeId,sequence})=>document.querySelector('iframe').contentWindow.postMessage({v:1,type:'open_session',bridge_id:bridgeId,native_id:nativeId,request_id:'fixture-request',sequence},${JSON.stringify(nativeUrl)});addEventListener('message',event=>{if(event.origin===${JSON.stringify(nativeUrl)}&&event.source===document.querySelector('iframe').contentWindow)window.messages.push(event.data)})</script>` }));
    const direct = await context.newPage();
    await direct.goto(`${nativeUrl}/?session=embed-session`, { waitUntil: "domcontentloaded" });
    await direct.getByRole("paragraph").filter({ hasText: "Retained embed-session" }).waitFor();
    const directInput = direct.locator("textarea").last();
    await directInput.focus();
    await direct.keyboard.type(`direct-${viewport.width}`);
    await direct.keyboard.press("Shift+Enter");
    await direct.keyboard.type("draft");
    assert.equal(await directInput.inputValue(), `direct-${viewport.width}\ndraft`);
    await direct.screenshot({ path: path.join(artifacts, `native-direct-${viewport.width}.png`), fullPage: true });
    const embedded = await context.newPage();
    await embedded.goto(parentOrigin, { waitUntil: "domcontentloaded" });
    await embedded.waitForFunction(() => window.messages.some(message => message?.type === "session_changed" && message?.native_id === "embed-session"));
    const frame = embedded.frameLocator('iframe[title="embedded-pi"]');
    await frame.getByRole("paragraph").filter({ hasText: "Retained embed-session" }).waitFor();
    const bridgeId = await embedded.evaluate(() => window.messages.find(message => message?.type === "ready")?.bridge_id);
    assert.match(bridgeId, /^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$/);
    await embedded.evaluate(({ bridgeId }) => window.sendOpen({ nativeId: "switch-session", bridgeId, sequence: 1 }), { bridgeId });
    await frame.getByRole("paragraph").filter({ hasText: "Retained switch-session" }).waitFor();
    await embedded.waitForFunction(() => window.messages.some(message => message?.type === "session_changed" && message?.native_id === "switch-session"));
    for (const bad of [
      { nativeId: "embed-session", bridgeId: `${bridgeId}-wrong`, sequence: 2 },
      { nativeId: "embed-session", bridgeId, sequence: 1 },
      { nativeId: "embed-session", bridgeId, sequence: 0 },
    ]) await embedded.evaluate(bad => window.sendOpen(bad), bad);
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(await frame.getByRole("paragraph").filter({ hasText: "Retained switch-session" }).count(), 1, "invalid bridge or sequence must not change the native selection");
    const input = frame.locator("textarea").last();
    await input.focus();
    await embedded.keyboard.type(`embedded-${viewport.width}`);
    await embedded.keyboard.press("Shift+Enter");
    await embedded.keyboard.type("draft");
    assert.equal(await input.inputValue(), `embedded-${viewport.width}\ndraft`);
    const schema = await embedded.evaluate(() => {
      const ready = window.messages.find(message => message?.type === "ready");
      const changed = window.messages.find(message => message?.type === "session_changed");
      return { ready: Object.keys(ready).sort(), changed: Object.keys(changed).sort(), same_bridge: ready.bridge_id === changed.bridge_id, sequence: changed.sequence };
    });
    assert.deepEqual(schema.ready, ["bridge_id", "sequence", "type", "v"]);
    assert.deepEqual(schema.changed, ["bridge_id", "native_id", "sequence", "type", "v"]);
    assert.equal(schema.same_bridge, true);
    assert.ok(schema.sequence >= 1);
    await embedded.screenshot({ path: path.join(artifacts, `native-embedded-${viewport.width}.png`), fullPage: true });
    const wrongOrigin = "https://127.0.0.1:34444";
    await context.route(`${wrongOrigin}/**`, route => route.fulfill({ contentType: "text/html", body: `<!doctype html><iframe title="wrong-origin-pi" src="${nativeUrl}/?session=embed-session"></iframe><script>window.sendBad=()=>document.querySelector('iframe').contentWindow.postMessage({v:1,type:'open_session',bridge_id:'wrong-bridge',native_id:'switch-session',request_id:'fixture-request',sequence:1},${JSON.stringify(nativeUrl)})</script>` }));
    const wrong = await context.newPage();
    await wrong.goto(wrongOrigin, { waitUntil: "domcontentloaded" });
    const wrongFrame = wrong.frameLocator('iframe[title="wrong-origin-pi"]');
    await wrongFrame.getByRole("paragraph").filter({ hasText: "Retained embed-session" }).waitFor();
    await wrong.evaluate(() => window.sendBad());
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(await wrongFrame.getByRole("paragraph").filter({ hasText: "Retained embed-session" }).count(), 1, "wrong-origin message must not change the native selection");
    await wrong.close();
    result.push({ viewport: viewport.width, bridge_schema: true, focused_draft: true, open_session: true, rejected_invalid_open: true });
    await context.close();
  }
  return result;
}

(async () => {
  let child = null;
  let temporaryAgent = null;
  let nativeUrl = suppliedUrl;
  let browser;
  try {
    if (source) {
      const sourceRoot = path.resolve(source);
      const entry = path.join(sourceRoot, "node_modules", "next", "dist", "bin", "next");
      if (!fs.existsSync(entry)) throw new Error("--source must be an isolated prepared pinned build with node_modules");
      temporaryAgent = syntheticSessions();
      const port = await freePort();
      nativeUrl = `http://127.0.0.1:${port}`;
      child = spawn(process.execPath, [entry, "start", "-H", "127.0.0.1", "-p", String(port)], {
        cwd: sourceRoot, env: { ...process.env, PI_CODING_AGENT_DIR: temporaryAgent, NEXT_TELEMETRY_DISABLED: "1" }, stdio: "ignore",
      });
      await waitForReady(nativeUrl, child);
    }
    if (!/^http:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}$/.test(nativeUrl || "")) throw new Error("--url must be an exact loopback HTTP origin");
    browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || undefined, headless: true });
    const commands = await directRpcJourney(browser, nativeUrl);
    const embedded = await embeddedJourney(browser, nativeUrl);
    const receipt = { fixture: "synthetic-pinned-pi-web", provider_calls: 0, direct_rpc: commands, embedded };
    fs.writeFileSync(path.join(artifacts, "pi-native-browser-receipt.json"), JSON.stringify(receipt, null, 2));
    console.log(JSON.stringify(receipt));
  } finally {
    await browser?.close();
    child?.kill("SIGTERM");
    if (child) await once(child, "exit").catch(() => {});
    if (temporaryAgent) fs.rmSync(temporaryAgent, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
