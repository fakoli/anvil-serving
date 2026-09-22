import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { createConnection } from "node:net";
import { tmpdir } from "node:os";
import { once } from "node:events";
import { join } from "node:path";
import { startFakePrimary } from "./fake-primary.mjs";
import { startFakeVision } from "./fake-vision.mjs";

async function assertBoundedShutdown(start, model) {
  const fixture = await start();
  const url = `${fixture.baseUrl}/chat/completions`;
  const pending = fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ model, fixture_delay: true }) });
  await fixture.waitForCaptures(1);
  const endpoint = new URL(fixture.baseUrl);
  const socket = createConnection({ host: "127.0.0.1", port: Number(endpoint.port) });
  socket.on("error", () => {});
  await once(socket, "connect");
  socket.write("POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 10\r\n\r\n{");
  await fixture.waitForRequests(2);
  const socketClosed = once(socket, "close");
  await fixture.close();
  await assert.rejects(pending);
  await socketClosed;
  await assert.rejects(fetch(url));
}

const primary = await startFakePrimary();
const vision = await startFakeVision();
const sessionDir = await mkdtemp(join(tmpdir(), "pi-image-fixture-"));

try {
  const primaryRequest = { model: "fixture-primary", stream: true, messages: [{ role: "user", content: "primary question" }] };
  const primaryBody = JSON.stringify(primaryRequest);
  const primaryResponse = await fetch(`${primary.baseUrl}/chat/completions`, { method: "POST", headers: { "content-type": "application/json" }, body: primaryBody });
  assert.equal(primaryResponse.status, 200);
  const primaryStream = await primaryResponse.text();
  assert.match(primaryStream, /"role":"assistant"/);
  assert.match(primaryStream, /"finish_reason":"stop"/);
  assert.match(primaryStream, /data: \[DONE\]/);
  assert.equal(primary.captures[0].body, primaryBody);

  const visionRequest = { model: "fixture-vision", messages: [{ role: "user", content: [{ type: "text", text: "What is in this synthetic image?" }, { type: "image_url", image_url: { url: "data:image/png;base64,c3ludGhldGlj" } }] }] };
  const visionResponse = await fetch(`${vision.baseUrl}/chat/completions`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(visionRequest) });
  assert.equal(visionResponse.status, 200);
  assert.equal((await visionResponse.json()).choices[0].message.content, vision.response);
  assert.deepEqual(vision.captures[0].associations, [{ question: "What is in this synthetic image?", image: "data:image/png;base64,c3ludGhldGlj" }]);

  const controller = new AbortController();
  const cancelled = fetch(`${primary.baseUrl}/chat/completions`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ fixture_delay: true }), signal: controller.signal });
  await primary.waitForCaptures(2);
  controller.abort();
  await assert.rejects(cancelled, { name: "AbortError" });
  await primary.captures[1].closed;
  assert.equal(primary.captures[1].cancelled, true);

  const rejected = await fetch(`${vision.baseUrl}/chat/completions`, { method: "POST", headers: { "content-type": "application/json" }, body: "{" });
  assert.equal(rejected.status, 400);
  const oversized = await fetch(`${vision.baseUrl}/chat/completions`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ padding: "x".repeat(64 * 1024) }) });
  assert.equal(oversized.status, 413);
  await assertBoundedShutdown(startFakePrimary, "fixture-primary");
  await assertBoundedShutdown(startFakeVision, "fixture-vision");
  process.stdout.write("pi provider fixtures: ok\n");
} finally {
  await Promise.all([primary.close(), vision.close()]);
  await rm(sessionDir, { recursive: true, force: true });
}
