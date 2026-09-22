import assert from "node:assert/strict";
import test from "node:test";
import { createFixtureObservationAdapter } from "./observation_adapter.mjs";

test("fixture adapter exposes only bounded read-only owner receipts", { timeout: 15_000 }, async (t) => {
  const adapter = await createFixtureObservationAdapter({ piSessionId: "fixture-pi-session" });
  t.after(() => adapter.close());
  assert.deepEqual(await adapter.execute({ operation: "navigate" }), { schema: "browser-owner-adapter/v1", status: "refused", code: "invalid_request" });
  const capture = await adapter.execute({ operation: "capture" });
  assert.equal(capture.status, "ok");
  assert.equal(capture.operation, "capture");
  assert.equal(capture.binding.pi_session_id, "fixture-pi-session");
  assert.match(capture.binding.owner_session_id, /^[0-9a-f-]{36}$/);
  assert.equal(JSON.stringify(capture).includes("iVBOR"), false, "PNG bytes escaped the owner");
  const disabled = capture.result.entities.find((entity) => entity.text === "Disabled capture");
  const status = capture.result.entities.find((entity) => entity.role === "status");
  assert.equal(disabled.enabled, false);
  assert.equal(status.enabled, null);
  const resolved = await adapter.execute({ operation: "resolve", args: { observation_id: capture.result.observation_id, entity_id: disabled.id } });
  assert.equal(resolved.result.enabled, false);
  assert.equal((await adapter.execute({ operation: "resolve", args: { observation_id: "00000000-0000-0000-0000-000000000000", entity_id: "e-1" } })).code, "unknown_observation");
  assert.equal((await adapter.execute({ operation: "release", args: { observation_id: capture.result.observation_id } })).status, "ok");
  assert.equal((await adapter.execute({ operation: "resolve", args: { observation_id: capture.result.observation_id, entity_id: disabled.id } })).code, "unknown_observation");
});

test("adapter closure refuses retained observation IDs", { timeout: 15_000 }, async () => {
  const adapter = await createFixtureObservationAdapter();
  const capture = await adapter.execute({ operation: "capture" });
  await adapter.close();
  assert.equal((await adapter.execute({ operation: "resolve", args: { observation_id: capture.result.observation_id, entity_id: "e-1" } })).code, "owner_closed");
});

test("adapter forwards an already-aborted capture", { timeout: 15_000 }, async (t) => {
  const adapter = await createFixtureObservationAdapter();
  t.after(() => adapter.close());
  const controller = new AbortController(); controller.abort();
  assert.equal((await adapter.execute({ operation: "capture" }, { signal: controller.signal })).code, "cancelled");
});
