import assert from "node:assert/strict";
import { deflateSync } from "node:zlib";
import fs, { chmodSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createObservationOwner, reopenObservationOwner } from "./owner.mjs";

const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const crc32 = (buffer) => { let crc = 0xffffffff; for (const byte of buffer) { crc ^= byte; for (let bit = 0; bit < 8; bit += 1) crc = crc & 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1; } return (crc ^ 0xffffffff) >>> 0; };
const chunk = (type, data = Buffer.alloc(0)) => { const bytes = Buffer.from(type), result = Buffer.alloc(data.length + 12); result.writeUInt32BE(data.length); bytes.copy(result, 4); data.copy(result, 8); result.writeUInt32BE(crc32(Buffer.concat([bytes, data])), data.length + 8); return result; };
const image = (pixel = 0) => { const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(1); ihdr.writeUInt32BE(1, 4); ihdr.set([8, 2, 0, 0, 0], 8); return { mimeType: "image/png", data: Buffer.concat([signature, chunk("IHDR", ihdr), chunk("IDAT", deflateSync(Buffer.from([0, pixel, 0, 0]))), chunk("IEND")]).toString("base64") }; };
const fact = { kind: "visual_fact", text: "red", uncertainty: "unverified_interpretation", region_ref: null };

function fixture({ inspector = async () => ({ inspection_status: "observed", facts: [fact], reason: "" }), ledgerLimits } = {}) {
  const stateDirectory = mkdtempSync(path.join(os.tmpdir(), "observation-owner-")); chmodSync(stateDirectory, 0o700); let tick = 0;
  const common = { stateDirectory, authorityId: "authority-1", sessionId: "session-1", modelId: "vision-1", profileId: "profile-1", inspector, clock: () => tick, ...(ledgerLimits ? { ledgerLimits } : {}) };
  return { stateDirectory, common, set tick(value) { tick = value; }, create: () => createObservationOwner({ ...common, priorSession: false }), reopen: () => reopenObservationOwner({ ...common, priorSession: true }), cleanup: () => rmSync(stateDirectory, { recursive: true, force: true }) };
}

const bind = (owner, entryId = "entry-1", sourceImage = image()) => owner.bind({ entryId, imagePart: 0, image: sourceImage });

async function withFailingLedgerRename(stateDirectory, action) {
  const originalRename = fs.renameSync;
  let failed = false;
  const ledgerPath = path.join(stateDirectory, "mediation-inspection-ledger.json");
  fs.renameSync = (source, target) => {
    if (!failed && target === ledgerPath) {
      failed = true;
      const error = new Error("ledger_rename_failure");
      error.code = "EIO";
      throw error;
    }
    return originalRename(source, target);
  };
  syncBuiltinESMExports();
  try {
    return await action();
  } finally {
    fs.renameSync = originalRename;
    syncBuiltinESMExports();
    assert.equal(failed, true);
  }
}

test("binds one MIME-checked PNG privately and requires an explicit question", async (t) => {
  let received; const setup = fixture({ inspector: async (request) => { received = request; return { inspection_status: "observed", facts: [fact], reason: "" }; } }); const owner = setup.create(); t.after(async () => { try { await owner.close(); } finally { setup.cleanup(); } });
  assert.equal(bind(owner, "entry-bad", { ...image(), mimeType: "image/jpeg" }).error, "invalid_image");
  assert.equal(bind(owner, "entry-bad", { mimeType: "image/png", data: "bad" }).error, "invalid_image");
  const pending = bind(owner); assert.equal(pending.status, "question_required"); assert.equal(pending.inspection_request_id, null);
  const result = await owner.inspect({ observationId: pending.observation_id, question: "What color is the gauge?" });
  assert.equal(result.status, "inspected"); assert.equal(result.inspection_status, "observed"); assert.deepEqual(result.facts, [fact]); assert.equal(JSON.stringify(result).includes(image().data), false);
  assert.deepEqual(Object.keys(received).sort(), ["image", "question", "signal"]); assert.deepEqual(Object.keys(received.image).sort(), ["data", "mimeType"]); assert.equal(received.image.mimeType, "image/png"); assert.equal(Buffer.isBuffer(received.image.data), true);
});

test("rebinds only the exact active source and reuses the exact completed receipt", async (t) => {
  let calls = 0; const setup = fixture({ inspector: async () => ({ inspection_status: "observed", facts: [{ ...fact, text: `fact-${++calls}` }], reason: "" }) }); t.after(setup.cleanup);
  const owner = setup.create(); const first = bind(owner), replay = bind(owner); assert.equal(first.observation_id, replay.observation_id);
  const request = { observationId: first.observation_id, question: "read this" }; const completed = await owner.inspect(request), reused = await owner.inspect(request);
  assert.equal(calls, 1); assert.deepEqual(reused, completed); assert.equal(bind(owner, "entry-1", image(1)).error, "stale_observation"); await owner.close();
});

test("failed work needs explicit follow-up and retains its budget charge", async (t) => {
  let calls = 0; const setup = fixture({ inspector: async () => { calls += 1; throw new Error("down"); }, ledgerLimits: { maxAttempts: 2, cumulativeMs: 100, perCallMs: 50 } }); t.after(setup.cleanup);
  const owner = setup.create(); const pending = bind(owner); const request = { observationId: pending.observation_id, question: "read this" };
  assert.equal((await owner.inspect(request)).error, "endpoint_unavailable"); assert.equal((await owner.inspect(request)).error, "budget_state_unavailable"); assert.equal((await owner.inspect(request, { followUp: true })).error, "endpoint_unavailable"); assert.equal(calls, 2); await owner.close();
});

test("typed transport failures survive and pre-aborted inspection performs zero callbacks", async (t) => {
  let calls = 0; const setup = fixture({ inspector: async () => { calls += 1; const error = new Error("private detail"); error.code = "result_too_large"; throw error; } }); t.after(setup.cleanup);
  const owner = setup.create(), pending = bind(owner); assert.equal((await owner.inspect({ observationId: pending.observation_id, question: "read this" })).error, "result_too_large"); await owner.close();
  let preAbortedCalls = 0; const second = fixture({ inspector: async () => { preAbortedCalls += 1; return { inspection_status: "observed", facts: [fact], reason: "" }; } }); t.after(second.cleanup);
  const secondOwner = second.create(), secondPending = bind(secondOwner), controller = new AbortController(); controller.abort(); assert.equal((await secondOwner.inspect({ observationId: secondPending.observation_id, question: "read this" }, { signal: controller.signal })).error, "cancelled"); assert.equal(preAbortedCalls, 0); await secondOwner.close();
  const malformed = fixture({ inspector: async () => ({ cancelled: true }) }); t.after(malformed.cleanup); const malformedOwner = malformed.create(), malformedPending = bind(malformedOwner); assert.equal((await malformedOwner.inspect({ observationId: malformedPending.observation_id, question: "read this" })).error, "invalid_response"); await malformedOwner.close();
});

test("canonical inconclusive output may carry an empty bounded reason", async (t) => {
  const setup = fixture({ inspector: async () => ({ inspection_status: "inconclusive", facts: [], reason: "" }) }); t.after(setup.cleanup);
  const owner = setup.create(), pending = bind(owner); const result = await owner.inspect({ observationId: pending.observation_id, question: "read this" }); assert.equal(result.status, "inspected"); assert.equal(result.inspection_status, "inconclusive"); assert.equal(result.reason, ""); await owner.close();
});

test("cancellation ignores a late inspector result and clean close permits exact rebind on reopen", async (t) => {
  let release; const late = new Promise((resolve) => { release = resolve; }); const setup = fixture({ inspector: async () => late });
  const owner = setup.create(), pending = bind(owner), controller = new AbortController(); const work = owner.inspect({ observationId: pending.observation_id, question: "read this" }, { signal: controller.signal }); controller.abort();
  assert.equal((await work).error, "cancelled"); release({ inspection_status: "observed", facts: [fact], reason: "" }); await owner.close();
  const reopened = setup.reopen(); t.after(async () => { try { await reopened.close(); } finally { setup.cleanup(); } }); const rebound = bind(reopened); assert.equal(rebound.observation_id, pending.observation_id);
});

test("close revokes an admitted inspector immediately before clean ledger release", async (t) => {
  let started; const held = new Promise((resolve) => { started = resolve; }); const setup = fixture({ inspector: async () => held });
  const owner = setup.create(), pending = bind(owner); const work = owner.inspect({ observationId: pending.observation_id, question: "read this" }); await new Promise((resolve) => setTimeout(resolve, 0)); await owner.close();
  assert.equal((await work).error, "cancelled"); started({ inspection_status: "observed", facts: [fact], reason: "" }); const reopened = setup.reopen(); t.after(async () => { try { await reopened.close(); } finally { setup.cleanup(); } }); assert.equal(bind(reopened).observation_id, pending.observation_id);
});

test("invalid ledger limits leave corrected first initialization available", async (t) => {
  const setup = fixture({ ledgerLimits: { maxAttempts: 33 } }); t.after(setup.cleanup);
  assert.throws(setup.create, { code: "budget_state_unavailable" });
  assert.deepEqual(readdirSync(setup.stateDirectory), []);
  const corrected = createObservationOwner({ ...setup.common, ledgerLimits: undefined, priorSession: false });
  await corrected.close();
});

test("failed ledger release rejects close and leaves the lease active", async (t) => {
  const setup = fixture(); t.after(setup.cleanup);
  const owner = setup.create();
  await assert.rejects(
    () => withFailingLedgerRename(setup.stateDirectory, () => owner.close()),
    { code: "budget_state_unavailable" },
  );
  assert.equal(bind(owner).error, "budget_state_unavailable");
  await assert.rejects(owner.close(), { code: "budget_state_unavailable" });
  assert.notEqual(JSON.parse(readFileSync(path.join(setup.stateDirectory, "mediation-inspection-ledger.json"), "utf8")).lease, null);
  assert.throws(setup.reopen, { code: "budget_state_unavailable" });
});

test("enforces budget exhaustion, session binding, and fail-closed reopened state", async (t) => {
  const setup = fixture({ ledgerLimits: { maxAttempts: 1, cumulativeMs: 100, perCallMs: 50 } }); t.after(setup.cleanup);
  const owner = setup.create(); const one = bind(owner, "entry-1"), two = bind(owner, "entry-2"); await owner.inspect({ observationId: one.observation_id, question: "first" }); assert.equal((await owner.inspect({ observationId: two.observation_id, question: "second" })).error, "budget_exhausted"); await owner.close();
  assert.throws(() => reopenObservationOwner({ ...setup.common, sessionId: "other", priorSession: true }));
  writeFileSync(path.join(setup.stateDirectory, "observation-owner-receipts.json"), "{}");
  assert.throws(setup.reopen); assert.throws(() => createObservationOwner({ ...setup.common, priorSession: false }));
});

test("completed receipts are required for clean reuse and private state rejects symlink or overlap corruption", async (t) => {
  let calls = 0; const setup = fixture({ inspector: async () => ({ inspection_status: "observed", facts: [{ ...fact, text: `fact-${++calls}` }], reason: "" }) }); t.after(setup.cleanup);
  const owner = setup.create(), pending = bind(owner), request = { observationId: pending.observation_id, question: "read this" }; await owner.inspect(request); await owner.close();
  const reopened = setup.reopen(); bind(reopened); assert.deepEqual(await reopened.inspect(request), await reopened.inspect(request)); assert.equal(calls, 1); await reopened.close();
  const receiptsPath = path.join(setup.stateDirectory, "observation-owner-receipts.json"), receipts = JSON.parse(readFileSync(receiptsPath, "utf8")); receipts.receipts = []; writeFileSync(receiptsPath, JSON.stringify(receipts)); assert.throws(setup.reopen);
  const symlinkSetup = fixture(); t.after(symlinkSetup.cleanup); const symlinkOwner = symlinkSetup.create(); await symlinkOwner.close(); const symlinkPath = path.join(symlinkSetup.stateDirectory, "observation-owner-receipts.json"); unlinkSync(symlinkPath); symlinkSync("observation-owner-metadata.json", symlinkPath); assert.throws(symlinkSetup.reopen);
  const overlapSetup = fixture(); t.after(overlapSetup.cleanup); const overlapOwner = overlapSetup.create(), overlap = bind(overlapOwner); await overlapOwner.close(); const tombstonePath = path.join(overlapSetup.stateDirectory, "observation-owner-tombstones.json"), tombstones = JSON.parse(readFileSync(tombstonePath, "utf8")); tombstones.entries = [{ observation_id: overlap.observation_id, entry_id: "entry-1", source_id: overlap.source_ref.source_id, image_part: overlap.source_ref.image_part, expired_at: 0 }]; writeFileSync(tombstonePath, JSON.stringify(tombstones)); assert.throws(overlapSetup.reopen);
});

test("reopen rejects swapped or oversized completed receipt envelopes", async (t) => {
  const setup = fixture(); t.after(setup.cleanup); const owner = setup.create(), first = bind(owner, "entry-1"), second = bind(owner, "entry-2"); await owner.inspect({ observationId: first.observation_id, question: "first" }); await owner.inspect({ observationId: second.observation_id, question: "second" }); await owner.close();
  const receiptPath = path.join(setup.stateDirectory, "observation-owner-receipts.json"), swapped = JSON.parse(readFileSync(receiptPath, "utf8")); swapped.receipts[0].envelope = swapped.receipts[1].envelope; writeFileSync(receiptPath, JSON.stringify(swapped)); assert.throws(setup.reopen);
  const oversized = fixture(); t.after(oversized.cleanup); const oversizedOwner = oversized.create(), pending = bind(oversizedOwner); await oversizedOwner.inspect({ observationId: pending.observation_id, question: "read this" }); await oversizedOwner.close(); const payload = JSON.parse(readFileSync(path.join(oversized.stateDirectory, "observation-owner-receipts.json"), "utf8")); payload.receipts[0].envelope.facts = Array.from({ length: 32 }, () => ({ kind: "visual_fact", text: "x".repeat(512), uncertainty: "unverified_interpretation", region_ref: null })); writeFileSync(path.join(oversized.stateDirectory, "observation-owner-receipts.json"), JSON.stringify(payload)); assert.throws(oversized.reopen);
});

test("missing prior state cannot create a new budget and retained sources expire into tombstones", async (t) => {
  const setup = fixture(); t.after(setup.cleanup); assert.throws(() => reopenObservationOwner({ ...setup.common, priorSession: true }));
  const owner = setup.create(), pending = bind(owner); setup.tick = 60 * 60 * 1000; assert.equal((await owner.inspect({ observationId: pending.observation_id, question: "read this" })).error, "expired_observation"); assert.equal(bind(owner).error, "expired_observation"); await owner.close();
  const tombstones = readFileSync(path.join(setup.stateDirectory, "observation-owner-tombstones.json"), "utf8"); assert.match(tombstones, /expired_at/); assert.equal(pending.status, "question_required");
});
