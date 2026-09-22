import assert from "node:assert/strict";
import test from "node:test";
import { createJevConsumer, evaluateJev, JevConsumerError } from "./jev_consumer.mjs";

const origin = "http://127.0.0.1:9000";
const fields = ["schema", "request_id", "observation_id", "source", "target", "scope", "coverage", "entities"];
const projection = { schema: "browser-element-resolution-projection/v1", request_id: "request", observation_id: "observation", source: "dom", target: { description: "capture", qualifiers: [] }, scope: { kind: "document", root: "document" }, coverage: { state: "complete", reason: null }, entities: [{ id: "e-1", role: "button", text: "Capture", nearby: "", state: { exists: true, in_viewport: true, occluded: false, enabled: true }, predicate_reasons: { exists: null, in_viewport: null, occluded: null, enabled: null } }] };
const envelope = (choice) => JSON.stringify({ ok: true, command: "jev evaluate", data: { schema: "anvil.jev.annotation.v1", capability: "browser_element_resolution", status: "completed", used: true, answers: { selection: { type: "choice", choice } } } });

test("consumer requires a trusted immutable origin and exact field list", () => {
  assert.deepEqual(createJevConsumer({ enabled: false }, new Set([origin])), { enabled: false });
  for (const config of [{ enabled: true, origin, fields: [...fields].reverse() }, { enabled: true, origin: "http://127.0.0.1:9001", fields }, { enabled: true, origin, fields, executable: "" }]) assert.throws(() => createJevConsumer(config, new Set([origin])), (error) => error instanceof JevConsumerError && error.code === "invalid_jev_policy");
});

test("consumer accepts only the completed used selection envelope", async () => {
  const policy = createJevConsumer({ enabled: true, origin, fields, runner: async () => ({ code: 0, stdout: envelope("e-1") }) }, new Set([origin]));
  assert.deepEqual(await evaluateJev(policy, projection), { outcome: "selection", selection: "e-1" });
  const malformed = createJevConsumer({ enabled: true, origin, fields, runner: async () => ({ code: 0, stdout: '{"ok":true,"ok":true}' }) }, new Set([origin]));
  assert.deepEqual(await evaluateJev(malformed, projection), { outcome: "malformed_response" });
});
