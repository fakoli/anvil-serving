import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { startFakePrimary } from "./fake-primary.mjs";
import { startFakeVision } from "./fake-vision.mjs";

const primary = await startFakePrimary();
const vision = await startFakeVision();
const sessionDir = await mkdtemp(join(tmpdir(), "pi-image-fixture-"));

try {
  const primaryRequest = { model: "fixture-primary", stream: true, messages: [{ role: "user", content: "primary question" }] };
  const primaryBody = JSON.stringify(primaryRequest);
  const primaryResponse = await fetch(`${primary.baseUrl}/chat/completions`, { method: "POST", headers: { "content-type": "application/json" }, body: primaryBody });
  assert.equal(primaryResponse.status, 200);
  assert.match(await primaryResponse.text(), /fixture primary response/);
  assert.equal(primary.captures[0].body, primaryBody);

  const visionRequest = { model: "fixture-vision", messages: [{ role: "user", content: [{ type: "text", text: "What is in this synthetic image?" }, { type: "image_url", image_url: { url: "data:image/png;base64,c3ludGhldGlj" } }] }] };
  const visionResponse = await fetch(`${vision.baseUrl}/chat/completions`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(visionRequest) });
  assert.equal(visionResponse.status, 200);
  assert.equal((await visionResponse.json()).choices[0].message.content, vision.response);
  assert.deepEqual(vision.captures[0].associations, [{ question: "What is in this synthetic image?", image: "data:image/png;base64,c3ludGhldGlj" }]);

  const controller = new AbortController();
  const cancelled = fetch(`${primary.baseUrl}/chat/completions`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ fixture_delay: true }), signal: controller.signal });
  await new Promise((resolve) => setTimeout(resolve, 10));
  controller.abort();
  await assert.rejects(cancelled, { name: "AbortError" });
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(primary.captures[1].cancelled, true);

  const rejected = await fetch(`${vision.baseUrl}/chat/completions`, { method: "POST", headers: { "content-type": "application/json" }, body: "{" });
  assert.equal(rejected.status, 400);
  const oversized = await fetch(`${vision.baseUrl}/chat/completions`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ padding: "x".repeat(64 * 1024) }) });
  assert.equal(oversized.status, 413);
  process.stdout.write("pi provider fixtures: ok\n");
} finally {
  await Promise.all([primary.close(), vision.close()]);
  await rm(sessionDir, { recursive: true, force: true });
}
