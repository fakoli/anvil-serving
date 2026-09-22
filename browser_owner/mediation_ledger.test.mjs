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

function captureError(action) {
  try {
    action();
  } catch (error) {
    return error;
  }
  assert.fail("expected an error");
}

function trustedLeaseFrom(error) {
  assert.equal(error instanceof LedgerError, true);
  const descriptor = Object.getOwnPropertyDescriptor(error, "trustedLeaseIdentity");
  assert.equal(descriptor?.enumerable, false);
  assert.equal(typeof descriptor?.value, "function");
  const first = descriptor.value();
  const second = descriptor.value();
  assert.notEqual(first, second);
  assert.deepEqual(first, second);
  assert.equal(Object.keys(error).includes("trustedLeaseIdentity"), false);
  assert.equal(JSON.stringify(error).includes(first.nonce), false);
  assert.equal(String(error).includes(first.nonce), false);
  assert.equal(error.stack.includes(first.nonce), false);
  return first;
}

function hasNoTrustedLease(error) {
  assert.equal(Object.hasOwn(error, "trustedLeaseIdentity"), false);
  assert.equal(Object.keys(error).includes("trustedLeaseIdentity"), false);
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

test("question digests canonicalize before completed-identity lookup and persistence", (t) => {
  const setup = fixture();
  t.after(setup.cleanup);
  const ledger = setup.create();
  const lower = identity("a");
  const upper = { ...lower, question_digest: lower.question_digest.toUpperCase() };
  const operation = ledger.begin(lower);
  const completed = ledger.finish(operation.operation, { outcome: "success" });
  assert.deepEqual(ledger.begin(upper), { status: "completed", reference: completed.reference });
  assert.equal(ledger.status().attempts_used, 1);
  const persisted = readFileSync(path.join(setup.stateDirectory, "mediation-inspection-ledger.json"), "utf8");
  assert.equal(persisted.includes(upper.question_digest), false);
  assert.equal(persisted.includes(lower.question_digest), true);
});

test("failed initial publication exposes only a private attempted lease and does not recover a missing record", (t) => {
  const hooks = { failPhase: "file_write" };
  const setup = fixture({ hooks });
  t.after(setup.cleanup);
  const error = captureError(setup.create);
  assert.equal(code("durability_failed")(error), true);
  const attemptedLease = trustedLeaseFrom(error);
  const supervisor = createTrustedLedgerSupervisor(() => true);
  assert.throws(
    () => supervisor.recover({ ...setup.options, previousLease: attemptedLease }),
    code("ledger_unavailable"),
  );
  assert.equal(setup.create().status().attempts_used, 0);
});

test("existing initial record is authoritative and has no acquisition recovery capability", (t) => {
  const setup = fixture();
  t.after(setup.cleanup);
  const existing = setup.create();
  const recordPath = path.join(setup.stateDirectory, "mediation-inspection-ledger.json");
  const priorBytes = readFileSync(recordPath, "utf8");
  const error = captureError(setup.create);
  assert.equal(code("ledger_unavailable")(error), true);
  hasNoTrustedLease(error);
  assert.equal(readFileSync(recordPath, "utf8"), priorBytes);
  assert.equal(existing.status().attempts_used, 0);
});

test("failed reopen and recovery publications preserve authoritative leases", (t) => {
  const reopenHooks = {};
  const reopenedSetup = fixture({ hooks: reopenHooks });
  t.after(reopenedSetup.cleanup);
  reopenedSetup.create().close();
  const reopenPath = path.join(reopenedSetup.stateDirectory, "mediation-inspection-ledger.json");
  const releasedBytes = readFileSync(reopenPath, "utf8");
  reopenHooks.failPhase = "file_write";
  const reopenError = captureError(() => reopenMediationLedger(reopenedSetup.options));
  assert.equal(code("durability_failed")(reopenError), true);
  trustedLeaseFrom(reopenError);
  assert.equal(readFileSync(reopenPath, "utf8"), releasedBytes);
  assert.equal(reopenMediationLedger(reopenedSetup.options).status().in_flight, false);

  const recoveryHooks = {};
  const recoveredSetup = fixture({ hooks: recoveryHooks });
  t.after(recoveredSetup.cleanup);
  const first = recoveredSetup.create();
  const reservation = first.begin(identity("r"));
  const previousLease = first.trustedLeaseIdentity();
  const recordPath = path.join(recoveredSetup.stateDirectory, "mediation-inspection-ledger.json");
  const priorBytes = readFileSync(recordPath, "utf8");
  recoveryHooks.failPhase = "file_write";
  const supervisor = createTrustedLedgerSupervisor(() => true);
  const recoverError = captureError(() => supervisor.recover({
    ...recoveredSetup.options,
    previousLease,
  }));
  assert.equal(code("durability_failed")(recoverError), true);
  trustedLeaseFrom(recoverError);
  assert.equal(readFileSync(recordPath, "utf8"), priorBytes);
  const recovered = supervisor.recover({ ...recoveredSetup.options, previousLease });
  assert.deepEqual(recovered.status(), {
    attempts_used: 1,
    charged_ms: reservation.deadline_ms,
    in_flight: false,
    identity_count: 1,
  });
});

test("uncertain acquisition recovery needs the surviving exact lease", (t) => {
  const hooks = { failPhase: "after_replace" };
  const setup = fixture({ hooks });
  t.after(setup.cleanup);
  const error = captureError(setup.create);
  assert.equal(code("durability_failed")(error), true);
  const attemptedLease = trustedLeaseFrom(error);
  const supervisor = createTrustedLedgerSupervisor(() => true);
  assert.throws(
    () => supervisor.recover({
      ...setup.options,
      previousLease: { ...attemptedLease, nonce: "0".repeat(48) },
    }),
    code("ledger_unavailable"),
  );
  assert.throws(
    () => createTrustedLedgerSupervisor(() => false).recover({
      ...setup.options,
      previousLease: attemptedLease,
    }),
    code("ledger_unavailable"),
  );
  assert.throws(
    () => supervisor.recover({
      ...setup.options,
      binding: { ...binding, logical_turn_id: "other" },
      previousLease: attemptedLease,
    }),
    code("ledger_unavailable"),
  );
  assert.throws(() => reopenMediationLedger(setup.options), code("ledger_unavailable"));
  assert.equal(supervisor.recover({ ...setup.options, previousLease: attemptedLease }).status().attempts_used, 0);
});

test("unmarked replace failures remain acquisition failures for create, reopen, and recovery", (t) => {
  const initialHooks = { unmarkedFailurePhase: "replace" };
  const initialSetup = fixture({ hooks: initialHooks });
  t.after(initialSetup.cleanup);
  const initialError = captureError(initialSetup.create);
  assert.equal(code("durability_failed")(initialError), true);
  trustedLeaseFrom(initialError);
  assert.equal(initialSetup.create().status().attempts_used, 0);

  const reopenHooks = {};
  const reopenSetup = fixture({ hooks: reopenHooks });
  t.after(reopenSetup.cleanup);
  reopenSetup.create().close();
  const reopenPath = path.join(reopenSetup.stateDirectory, "mediation-inspection-ledger.json");
  const releasedBytes = readFileSync(reopenPath, "utf8");
  reopenHooks.unmarkedFailurePhase = "replace";
  const reopenError = captureError(() => reopenMediationLedger(reopenSetup.options));
  assert.equal(code("durability_failed")(reopenError), true);
  trustedLeaseFrom(reopenError);
  assert.equal(readFileSync(reopenPath, "utf8"), releasedBytes);
  assert.equal(reopenMediationLedger(reopenSetup.options).status().in_flight, false);

  const recoveryHooks = {};
  const recoverySetup = fixture({ hooks: recoveryHooks });
  t.after(recoverySetup.cleanup);
  const first = recoverySetup.create();
  first.begin(identity("u"));
  const previousLease = first.trustedLeaseIdentity();
  const recoveryPath = path.join(recoverySetup.stateDirectory, "mediation-inspection-ledger.json");
  const priorBytes = readFileSync(recoveryPath, "utf8");
  recoveryHooks.unmarkedFailurePhase = "replace";
  const supervisor = createTrustedLedgerSupervisor(() => true);
  const recoveryError = captureError(() => supervisor.recover({
    ...recoverySetup.options,
    previousLease,
  }));
  assert.equal(code("durability_failed")(recoveryError), true);
  trustedLeaseFrom(recoveryError);
  assert.equal(readFileSync(recoveryPath, "utf8"), priorBytes);
  assert.equal(supervisor.recover({ ...recoverySetup.options, previousLease }).status().in_flight, false);
});

test("ordinary inspection errors do not expose trusted lease recovery", (t) => {
  const setup = fixture();
  t.after(setup.cleanup);
  const invalidLauncher = captureError(() => createMediationLedger({ ...setup.options, binding: {} }));
  assert.equal(code("invalid_binding")(invalidLauncher), true);
  hasNoTrustedLease(invalidLauncher);
  const ledger = setup.create();
  const invalid = captureError(() => ledger.begin({}));
  assert.equal(code("invalid_inspection")(invalid), true);
  hasNoTrustedLease(invalid);
  const operation = ledger.begin(identity("a"));
  const close = captureError(() => ledger.close());
  assert.equal(code("inspection_in_flight")(close), true);
  hasNoTrustedLease(close);
  const mismatch = captureError(() => ledger.finish({}, { outcome: "success" }));
  assert.equal(code("operation_mismatch")(mismatch), true);
  hasNoTrustedLease(mismatch);
  assert.equal(ledger.finish(operation.operation, { outcome: "success" }).status, "completed");

  writeFileSync(path.join(setup.stateDirectory, "mediation-inspection-ledger.json"), "corrupt");
  const status = captureError(() => ledger.status());
  assert.equal(code("ledger_unavailable")(status), true);
  hasNoTrustedLease(status);
});
