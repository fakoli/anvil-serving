import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { chromium } from "playwright";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import test from "node:test";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createFixtureObservationAdapter } from "./observation_adapter.mjs";


const fixtureHtml = '<!doctype html><main aria-label="Synthetic browser fixture"><button>Capture report</button><button disabled>Disabled capture</button><section role="status">Read-only status region</section></main>';
async function fixtureDigest() {
  const server = createServer((_request, response) => response.end(fixtureHtml)); await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve)); const url = `http://127.0.0.1:${server.address().port}/fixture`;
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || "/usr/bin/google-chrome", headless: true, args: ["--disable-gpu"] }); try { const context = await browser.newContext({ serviceWorkers: "block", acceptDownloads: false }), page = await context.newPage(); await page.goto(url, { waitUntil: "load" }); return createHash("sha256").update(await page.screenshot({ type: "png", caret: "initial" })).digest("hex"); } finally { await browser.close(); await new Promise((resolve) => server.close(resolve)); }
}

const previewViewer = join(dirname(fileURLToPath(import.meta.url)), "test_fixtures", "preview_viewer.mjs");

async function previewFixture(mode = "attest") {
  const root = await mkdtemp(join(tmpdir(), "adapter-preview-")), proof = join(root, "proof.json"), ready = join(root, "ready");
  return { root, proof, ready, preview: { viewer: [process.execPath, previewViewer, mode, proof, mode === "block" ? ready : "-"], runtimeRoot: root } };
}

test("fixture adapter exposes bounded read-only receipts and an active opaque preview", { timeout: 15_000 }, async (t) => {
  const fixture = await previewFixture(), expectedDigest = await fixtureDigest();
  const adapter = await createFixtureObservationAdapter({ piSessionId: "fixture-pi-session", preview: fixture.preview });
  t.after(async () => { await adapter.close(); await rm(fixture.root, { recursive: true, force: true }); });
  assert.deepEqual(await adapter.execute({ operation: "navigate" }), { schema: "browser-owner-adapter/v1", status: "refused", code: "invalid_request" });
  assert.equal((await adapter.execute({ operation: "preview" })).code, "unknown_observation");
  const capture = await adapter.execute({ operation: "capture" });
  assert.equal(capture.status, "ok"); assert.equal(capture.operation, "capture"); assert.equal(capture.binding.pi_session_id, "fixture-pi-session"); assert.match(capture.binding.owner_session_id, /^[0-9a-f-]{36}$/); assert.equal(JSON.stringify(capture).includes("iVBOR"), false, "PNG bytes escaped the owner");
  const preview = await adapter.execute({ operation: "preview" }); assert.deepEqual(preview, { schema: "browser-owner-adapter/v1", status: "ok", operation: "preview", binding: capture.binding, result: { status: "shown" } }); assert.doesNotMatch(JSON.stringify(preview), /iVBOR|data:image|file:|private_path/);
  const proof = JSON.parse(await readFile(fixture.proof, "utf8")); assert.equal(proof.digest, expectedDigest, "viewer did not receive the independent fixture screenshot"); assert.equal(proof.mode, 0o600);
  const disabled = capture.result.entities.find((entity) => entity.text === "Disabled capture"), status = capture.result.entities.find((entity) => entity.role === "status"); assert.equal(disabled.enabled, false); assert.equal(status.enabled, null);
  const resolved = await adapter.execute({ operation: "resolve", args: { observation_id: capture.result.observation_id, entity_id: disabled.id } }); assert.equal(resolved.result.enabled, false);
  assert.equal((await adapter.execute({ operation: "resolve", args: { observation_id: "00000000-0000-0000-0000-000000000000", entity_id: "e-1" } })).code, "unknown_observation");
  assert.equal((await adapter.execute({ operation: "release", args: { observation_id: capture.result.observation_id } })).status, "ok"); assert.equal((await adapter.execute({ operation: "preview" })).code, "unknown_observation");
});

test("queued preview follows the most recent completed capture", { timeout: 15_000 }, async (t) => {
  const fixture = await previewFixture(); let tick = 0;
  const adapter = await createFixtureObservationAdapter({ preview: fixture.preview, clock: () => tick }); t.after(async () => { await adapter.close(); await rm(fixture.root, { recursive: true, force: true }); });
  const first = await adapter.execute({ operation: "capture" }); tick = 60_001; const second = adapter.execute({ operation: "capture" }), preview = adapter.execute({ operation: "preview" }); const [latest, shown] = await Promise.all([second, preview]);
  assert.equal(shown.status, "ok", "preview selected the superseded expired observation"); assert.notEqual(first.result.observation_id, latest.result.observation_id, "fixture did not create a distinct second observation");
});

test("adapter forwards an already-aborted capture", { timeout: 15_000 }, async (t) => {
  const adapter = await createFixtureObservationAdapter(); t.after(() => adapter.close()); const controller = new AbortController(); controller.abort(); assert.equal((await adapter.execute({ operation: "capture" }, { signal: controller.signal })).code, "cancelled");
});

test("retained observation receipts keep their session binding after the newest is released", { timeout: 15_000 }, async (t) => {
  const adapter = await createFixtureObservationAdapter({ piSessionId: "retained-fixture-session" });
  t.after(() => adapter.close());
  const first = await adapter.execute({ operation: "capture" }), latest = await adapter.execute({ operation: "capture" });
  assert.equal(first.status, "ok"); assert.equal(latest.status, "ok");
  const releasedLatest = await adapter.execute({ operation: "release", args: { observation_id: latest.result.observation_id } });
  assert.equal(releasedLatest.status, "ok"); assert.deepEqual(releasedLatest.binding, latest.binding);
  assert.equal((await adapter.execute({ operation: "preview" })).code, "unknown_observation");
  const resolved = await adapter.execute({ operation: "resolve", args: { observation_id: first.result.observation_id, entity_id: first.result.entities[0].id } });
  assert.equal(resolved.status, "ok"); assert.deepEqual(resolved.binding, first.binding);
  const releasedFirst = await adapter.execute({ operation: "release", args: { observation_id: first.result.observation_id } });
  assert.equal(releasedFirst.status, "ok"); assert.deepEqual(releasedFirst.binding, first.binding);
});


test("adapter close revokes an in-flight preview before viewer completion", { timeout: 15_000 }, async (t) => {
  const fixture = await previewFixture("block");
  const adapter = await createFixtureObservationAdapter({ preview: fixture.preview }); t.after(async () => { await adapter.close(); await rm(fixture.root, { recursive: true, force: true }); });
  await adapter.execute({ operation: "capture" }); const preview = adapter.execute({ operation: "preview" }); const deadline = Date.now() + 5_000; while (!existsSync(fixture.ready)) { assert.ok(Date.now() < deadline, "viewer did not begin"); await new Promise((resolve) => setTimeout(resolve, 5)); } await adapter.close(); assert.ok(["owner_closed", "revoked"].includes((await preview).code)); assert.equal(existsSync(fixture.proof), false, "closed adapter allowed viewer delivery");
});
