import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  createMediationLedger,
  createTrustedLedgerSupervisor,
  LedgerError,
  reopenMediationLedger,
} from "./mediation_ledger.mjs";

const code = (expected) => (value) => value instanceof LedgerError && value.code === expected;
const binding = Object.freeze({ owner_authority_id: "owner-1", harness_session_id: "session-1", logical_turn_id: "turn-1" });
const identity = (suffix = "a") => ({
  observation_id: `observation-${suffix}`,
  source_entry_id: `entry-${suffix}`,
  source_part: `part-${suffix}`,
  question_digest: "a".repeat(63) + (suffix.charCodeAt(0) % 10),
  crop_id: `crop-${suffix}`,
  model_id: "vision-model-1",
  profile_id: "profile-1",
});

function fixture({ limits, hooks } = {}) {
  const stateDirectory = mkdtempSync(path.join(os.tmpdir(), "mediation-ledger-"));
  let tick = 0;
  const options = { stateDirectory, binding, limits, clock: () => tick, trustedTestHooks: hooks };
  return {
    stateDirectory,
    options,
    set time(value) { tick = value; },
    create: () => createMediationLedger(options),
    cleanup: () => rmSync(stateDirectory, { recursive: true, force: true }),
  };
}

test("reserves exact bounded attempt and time budgets before dispatch", (t) => {
  const setup = fixture({ limits: { maxAttempts: 2, cumulativeMs: 100, perCallMs: 50 } }); t.after(setup.cleanup);
  const ledger = setup.create();
  const first = ledger.begin(identity("a"));
  assert.equal(first.deadline_ms, 50);
  setup.time = 10;
  assert.deepEqual(ledger.finish(first.operation, { outcome: "success" }).status, "completed");
  assert.deepEqual(ledger.status(), { attempts_used: 1, charged_ms: 10, in_flight: false, identity_count: 1 });
  const second = ledger.begin(identity("b"), { deadlineMs: 99 });
  assert.equal(second.deadline_ms, 50);
  assert.deepEqual(ledger.finish(second.operation, { outcome: "cancelled" }).status, "cancelled");
  assert.equal(ledger.status().charged_ms, 60);
  assert.throws(() => ledger.begin(identity("c")), code("budget_exhausted"));
});

test("failed and cancelled attempts consume reservations while completed reuse is free", (t) => {
  const setup = fixture({ limits: { maxAttempts: 4, cumulativeMs: 100, perCallMs: 50 } }); t.after(setup.cleanup);
  const ledger = setup.create();
  const failed = ledger.begin(identity("a"));
  assert.equal(ledger.finish(failed.operation, { outcome: "failed" }).status, "failed");
  const followUpRequired = ledger.begin(identity("a"));
  assert.equal(followUpRequired.status, "followup_required");
  const followUp = ledger.begin(identity("a"), { followUp: true });
  setup.time = 1;
  assert.equal(ledger.finish(followUp.operation, { outcome: "success" }).status, "completed");
  const reused = ledger.begin(identity("a"));
  assert.equal(reused.status, "completed");
  assert.equal(ledger.status().attempts_used, 2);
  assert.equal(ledger.status().charged_ms, 51);
});

test("crash recovery consumes an outstanding reservation and fences old handles", (t) => {
  const setup = fixture({ limits: { maxAttempts: 3, cumulativeMs: 100, perCallMs: 50 } }); t.after(setup.cleanup);
  const first = setup.create();
  const operation = first.begin(identity("a"));
  const supervisor = createTrustedLedgerSupervisor(() => true);
  assert.throws(() => createTrustedLedgerSupervisor(() => false).recover({ ...setup.options, previousLease: first.trustedLeaseIdentity() }), code("ledger_unavailable"));
  for (const evidence of [{}, 1, "true", Promise.resolve(false)]) {
    assert.throws(() => createTrustedLedgerSupervisor(() => evidence).recover({ ...setup.options, previousLease: first.trustedLeaseIdentity() }), code("ledger_unavailable"));
  }
  assert.throws(() => supervisor.recover({ ...setup.options, previousLease: { ...first.trustedLeaseIdentity(), nonce: "0".repeat(48) } }), code("ledger_unavailable"));
  const recovered = supervisor.recover({ ...setup.options, previousLease: first.trustedLeaseIdentity() });
  assert.deepEqual(recovered.status(), { attempts_used: 1, charged_ms: 50, in_flight: false, identity_count: 1 });
  assert.equal(recovered.begin(identity("a")).status, "followup_required");
  assert.throws(() => first.finish(operation.operation, { outcome: "success" }), code("stale_lease"));
  assert.throws(() => first.close(), code("stale_lease"));
});

test("missing, corrupt, mismatched, and concurrently owned records fail closed", (t) => {
  const setup = fixture(); t.after(setup.cleanup);
  assert.throws(() => reopenMediationLedger(setup.options), code("ledger_unavailable"));
  const ledger = setup.create();
  assert.throws(() => createMediationLedger(setup.options), code("ledger_unavailable"));
  assert.throws(() => reopenMediationLedger(setup.options), code("ledger_unavailable"));
  writeFileSync(path.join(setup.stateDirectory, "mediation-inspection-ledger.json"), "not json");
  assert.throws(() => ledger.status(), code("ledger_unavailable"));
  const another = fixture(); t.after(another.cleanup);
  const clean = another.create(); clean.close();
  assert.throws(() => reopenMediationLedger({ ...another.options, binding: { ...binding, logical_turn_id: "other" } }), code("ledger_unavailable"));
});

test("publication failures distinguish pre-replace from uncertain replace and sync phases", (t) => {
  for (const phase of ["file_write", "file_sync", "before_replace", "replace", "after_replace", "directory_sync"]) {
    const hooks = {};
    const setup = fixture({ hooks }); t.after(setup.cleanup);
    const ledger = setup.create(); hooks.failPhase = phase;
    assert.throws(() => ledger.begin(identity(phase[0])), code("durability_failed"), phase);
    if (["replace", "after_replace", "directory_sync"].includes(phase)) {
      assert.throws(() => ledger.status(), code("ledger_poisoned"), phase);
    } else {
      assert.equal(ledger.status().in_flight, false, phase);
      ledger.begin(identity("z"));
    }
    const supervisor = createTrustedLedgerSupervisor(() => true);
    const recovered = supervisor.recover({ ...setup.options, previousLease: ledger.trustedLeaseIdentity() });
    assert.equal(recovered.status().in_flight, false, phase);
  }
});

test("identity records are bounded opaque bindings and operation tokens cannot be forged", (t) => {
  const setup = fixture({ limits: { maxAttempts: 3, cumulativeMs: 100, perCallMs: 50, maxIdentities: 1 } }); t.after(setup.cleanup);
  const ledger = setup.create();
  const reservation = ledger.begin(identity("a"));
  assert.throws(() => ledger.finish({}, { outcome: "success" }), code("operation_mismatch"));
  assert.equal(ledger.finish(reservation.operation, { outcome: "success" }).status, "completed");
  assert.throws(() => ledger.begin(identity("b")), code("identity_capacity_exhausted"));
  const raw = readFileSync(path.join(setup.stateDirectory, "mediation-inspection-ledger.json"), "utf8");
  assert.equal(raw.includes("raw image"), false);
  assert.equal(raw.includes("provider answer"), false);
  assert.equal(raw.includes("https://"), false);
});

test("unmarked rename and directory-sync failures poison before a refund can publish", (t) => {
  for (const phase of ["replace", "directory_sync"]) {
    const hooks = {};
    const setup = fixture({ hooks }); t.after(setup.cleanup);
    const ledger = setup.create();
    const reservation = ledger.begin(identity(phase === "replace" ? "r" : "d"));
    setup.time = 1;
    hooks.unmarkedFailurePhase = phase;
    assert.throws(() => ledger.finish(reservation.operation, { outcome: "success" }), code("durability_failed"));
    for (const action of [
      () => ledger.status(),
      () => ledger.begin(identity("z")),
      () => ledger.finish(reservation.operation, { outcome: "success" }),
      () => ledger.close(),
    ]) assert.throws(action, code("ledger_poisoned"), phase);
  }
});

test("constructor rejects expanded limits, malformed trusted state, and unsupported platform assumptions", (t) => {
  const setup = fixture(); t.after(setup.cleanup);
  for (const limits of [{ maxAttempts: 33 }, { cumulativeMs: 120_001 }, { perCallMs: 30_001 }, { maxIdentities: 65 }, { perCallMs: 1.5 }, { unknown: 1 }, null, []]) {
    assert.throws(() => createMediationLedger({ ...setup.options, limits }), code("invalid_limits"));
  }
  assert.throws(() => createMediationLedger({ ...setup.options, binding: { ...binding, owner_authority_id: "" } }), code("invalid_binding"));
  assert.throws(() => createMediationLedger({ ...setup.options, binding: { ...binding, harness_session_id: "https://example.test/session" } }), code("invalid_binding"));
  assert.throws(() => createMediationLedger({ ...setup.options, trustedTestHooks: { failPhase: "other" } }), code("invalid_launcher"));
  const direct = mkdtempSync(path.join(os.tmpdir(), "mediation-public-")); t.after(() => rmSync(direct, { recursive: true, force: true }));
  chmodSync(direct, 0o755);
  assert.throws(() => createMediationLedger({ ...setup.options, stateDirectory: direct }), code("durability_unsupported"));
});

test("inspection identities reject raw text and URL-shaped fields before persistence", (t) => {
  const setup = fixture(); t.after(setup.cleanup);
  const ledger = setup.create();
  assert.throws(() => ledger.begin({ ...identity("a"), source_entry_id: "https://example.test/image" }), code("invalid_inspection"));
  assert.throws(() => ledger.begin({ ...identity("a"), question_digest: "what does this image show?" }), code("invalid_inspection"));
  const digest = ["a".repeat(64)];
  assert.throws(() => ledger.begin({ ...identity("a"), question_digest: digest }), code("invalid_inspection"));
  digest.push("later raw answer");
  assert.equal(ledger.status().attempts_used, 0);
});
