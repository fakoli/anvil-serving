import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { chromium } from "playwright";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import test from "node:test";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createFixtureObservationAdapter } from "./observation_adapter.mjs";


const fixtureHtml = '<!doctype html><main aria-label="Synthetic browser fixture"><button>Capture report</button><button disabled>Disabled capture</button><section role="status">Read-only status region</section></main>';
async function fixtureDigest() {
  const server = createServer((_request, response) => response.end(fixtureHtml)); await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve)); const url = `http://127.0.0.1:${server.address().port}/fixture`;
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || "/usr/bin/google-chrome", headless: true, args: ["--disable-gpu"] }); try { const context = await browser.newContext({ serviceWorkers: "block", acceptDownloads: false }), page = await context.newPage(); await page.goto(url, { waitUntil: "load" }); return createHash("sha256").update(await page.screenshot({ type: "png", caret: "initial" })).digest("hex"); } finally { await browser.close(); await new Promise((resolve) => server.close(resolve)); }
}

async function previewFixture() {
  const root = await mkdtemp(join(tmpdir(), "adapter-preview-")), viewer = join(root, "viewer.mjs"), proof = join(root, "proof.json");
  await writeFile(viewer, `#!${process.execPath}\nimport { createHash } from "node:crypto";import { lstat,readFile,writeFile } from "node:fs/promises";const file=process.argv.at(-1);if(process.env.FIXTURE_PREVIEW_STARTED){await writeFile(process.env.FIXTURE_PREVIEW_STARTED,"started")}if(process.env.FIXTURE_PREVIEW_DELAY){await new Promise(resolve=>setTimeout(resolve,Number(process.env.FIXTURE_PREVIEW_DELAY)))}const [image,stat]=await Promise.all([readFile(file),lstat(file)]);await writeFile(process.env.FIXTURE_PREVIEW_PROOF,JSON.stringify({digest:createHash("sha256").update(image).digest("hex"),mode:stat.mode&0o777}));`); await chmod(viewer, 0o700);
  return { root, proof, preview: { viewer: [viewer], runtimeRoot: root } };
}

test("fixture adapter exposes bounded read-only receipts and an active opaque preview", { timeout: 15_000 }, async (t) => {
  const fixture = await previewFixture(), expectedDigest = await fixtureDigest(), previous = process.env.FIXTURE_PREVIEW_PROOF; process.env.FIXTURE_PREVIEW_PROOF = fixture.proof;
  const adapter = await createFixtureObservationAdapter({ piSessionId: "fixture-pi-session", preview: fixture.preview });
  t.after(async () => { if (previous === undefined) delete process.env.FIXTURE_PREVIEW_PROOF; else process.env.FIXTURE_PREVIEW_PROOF = previous; await adapter.close(); await rm(fixture.root, { recursive: true, force: true }); });
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
  const fixture = await previewFixture(), previous = process.env.FIXTURE_PREVIEW_PROOF; process.env.FIXTURE_PREVIEW_PROOF = fixture.proof; let tick = 0;
  const adapter = await createFixtureObservationAdapter({ preview: fixture.preview, clock: () => tick }); t.after(async () => { if (previous === undefined) delete process.env.FIXTURE_PREVIEW_PROOF; else process.env.FIXTURE_PREVIEW_PROOF = previous; await adapter.close(); await rm(fixture.root, { recursive: true, force: true }); });
  const first = await adapter.execute({ operation: "capture" }); tick = 60_001; const second = adapter.execute({ operation: "capture" }), preview = adapter.execute({ operation: "preview" }); const [latest, shown] = await Promise.all([second, preview]);
  assert.equal(shown.status, "ok", "preview selected the superseded expired observation"); assert.notEqual(first.result.observation_id, latest.result.observation_id, "fixture did not create a distinct second observation");
});

test("adapter forwards an already-aborted capture", { timeout: 15_000 }, async (t) => {
  const adapter = await createFixtureObservationAdapter(); t.after(() => adapter.close()); const controller = new AbortController(); controller.abort(); assert.equal((await adapter.execute({ operation: "capture" }, { signal: controller.signal })).code, "cancelled");
});


test("adapter close revokes an in-flight preview before viewer completion", { timeout: 15_000 }, async (t) => {
  const fixture = await previewFixture(), started = join(fixture.root, "started"); const previousProof = process.env.FIXTURE_PREVIEW_PROOF, previousStarted = process.env.FIXTURE_PREVIEW_STARTED, previousDelay = process.env.FIXTURE_PREVIEW_DELAY; process.env.FIXTURE_PREVIEW_PROOF = fixture.proof; process.env.FIXTURE_PREVIEW_STARTED = started; process.env.FIXTURE_PREVIEW_DELAY = "250";
  const adapter = await createFixtureObservationAdapter({ preview: fixture.preview }); t.after(async () => { if (previousProof === undefined) delete process.env.FIXTURE_PREVIEW_PROOF; else process.env.FIXTURE_PREVIEW_PROOF = previousProof; if (previousStarted === undefined) delete process.env.FIXTURE_PREVIEW_STARTED; else process.env.FIXTURE_PREVIEW_STARTED = previousStarted; if (previousDelay === undefined) delete process.env.FIXTURE_PREVIEW_DELAY; else process.env.FIXTURE_PREVIEW_DELAY = previousDelay; await adapter.close(); await rm(fixture.root, { recursive: true, force: true }); });
  await adapter.execute({ operation: "capture" }); const preview = adapter.execute({ operation: "preview" }); const deadline = Date.now() + 500; while (!existsSync(started)) { assert.ok(Date.now() < deadline, "viewer did not begin"); await new Promise((resolve) => setTimeout(resolve, 5)); } await adapter.close(); assert.ok(["owner_closed", "revoked"].includes((await preview).code)); await new Promise((resolve) => setTimeout(resolve, 280)); assert.equal(existsSync(fixture.proof), false, "closed adapter allowed viewer delivery");
});
