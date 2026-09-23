import assert from "node:assert/strict";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import test from "node:test";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createFixtureObservationAdapter } from "./observation_adapter.mjs";

async function previewFixture() {
  const root = await mkdtemp(join(tmpdir(), "adapter-preview-")), viewer = join(root, "viewer.mjs"), proof = join(root, "proof.json");
  await writeFile(viewer, `#!${process.execPath}\nimport { createHash } from "node:crypto";import { lstat,readFile,writeFile } from "node:fs/promises";const file=process.argv.at(-1),[image,stat]=await Promise.all([readFile(file),lstat(file)]);await writeFile(process.env.FIXTURE_PREVIEW_PROOF,JSON.stringify({digest:createHash("sha256").update(image).digest("hex"),mode:stat.mode&0o777}));`); await chmod(viewer, 0o700);
  return { root, proof, preview: { viewer: [viewer], runtimeRoot: root } };
}

test("fixture adapter exposes bounded read-only receipts and an active opaque preview", { timeout: 15_000 }, async (t) => {
  const fixture = await previewFixture(), previous = process.env.FIXTURE_PREVIEW_PROOF; process.env.FIXTURE_PREVIEW_PROOF = fixture.proof;
  const adapter = await createFixtureObservationAdapter({ piSessionId: "fixture-pi-session", preview: fixture.preview });
  t.after(async () => { if (previous === undefined) delete process.env.FIXTURE_PREVIEW_PROOF; else process.env.FIXTURE_PREVIEW_PROOF = previous; await adapter.close(); await rm(fixture.root, { recursive: true, force: true }); });
  assert.deepEqual(await adapter.execute({ operation: "navigate" }), { schema: "browser-owner-adapter/v1", status: "refused", code: "invalid_request" });
  assert.equal((await adapter.execute({ operation: "preview" })).code, "unknown_observation");
  const capture = await adapter.execute({ operation: "capture" });
  assert.equal(capture.status, "ok"); assert.equal(capture.operation, "capture"); assert.equal(capture.binding.pi_session_id, "fixture-pi-session"); assert.match(capture.binding.owner_session_id, /^[0-9a-f-]{36}$/); assert.equal(JSON.stringify(capture).includes("iVBOR"), false, "PNG bytes escaped the owner");
  const preview = await adapter.execute({ operation: "preview" }); assert.deepEqual(preview, { schema: "browser-owner-adapter/v1", status: "ok", operation: "preview", binding: capture.binding, result: { status: "shown" } }); assert.doesNotMatch(JSON.stringify(preview), /iVBOR|data:image|file:|private_path/);
  const proof = JSON.parse(await readFile(fixture.proof, "utf8")); assert.match(proof.digest, /^[0-9a-f]{64}$/); assert.equal(proof.mode, 0o600);
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
