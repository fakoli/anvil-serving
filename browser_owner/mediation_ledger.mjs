import { randomBytes, createHash } from "node:crypto";
import * as fs from "node:fs";
import path from "node:path";

const LEDGER_FILE = "mediation-inspection-ledger.json";
const SCHEMA = "anvil-serving.mediation-inspection-ledger/v1";
const DEFAULT_LIMITS = Object.freeze({
  maxAttempts: 32,
  cumulativeMs: 120_000,
  perCallMs: 30_000,
  maxIdentities: 64,
});
const WRITE_PHASES = new Set([
  "file_write",
  "file_sync",
  "before_replace",
  "replace",
  "after_replace",
  "directory_sync",
]);

export class LedgerError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

const fail = (code) => {
  throw new LedgerError(code);
};

const byteLength = (value) => Buffer.byteLength(value, "utf8");
const clone = (value) => structuredClone(value);
const now = () => performance.now();

/**
 * Create a new ledger in a launcher-selected private directory. This is a
 * trusted-launcher API: inspection requests only reach `begin` and `finish`.
 */
export function createMediationLedger(options) {
  const launcher = validateLauncher(options);
  preflightDirectory(launcher.stateDirectory);
  const recordPath = ledgerPath(launcher.stateDirectory);
  if (fs.existsSync(recordPath)) fail("ledger_unavailable");

  const lease = newLease(1);
  const record = emptyRecord(launcher.binding, launcher.limits, lease);
  publishAcquisition(lease, () => publishInitial(launcher, record));
  return new MediationLedger(launcher, lease);
}

/**
 * Reopen only a cleanly released ledger with the same trusted binding.
 */
export function reopenMediationLedger(options) {
  const launcher = validateLauncher(options);
  preflightDirectory(launcher.stateDirectory);
  const record = readRecord(launcher.stateDirectory);
  assertBinding(record, launcher.binding);
  assertLimits(record, launcher.limits);
  if (record.lease !== null || record.in_flight !== null) fail("ledger_unavailable");

  const lease = newLease(record.lease_generation + 1);
  record.lease_generation = lease.generation;
  record.lease = lease;
  publishAcquisition(lease, () => publish(launcher, record));
  return new MediationLedger(launcher, lease);
}

/**
 * Build the separate trusted recovery capability. Its exit proof is never an
 * inspection request field and is evaluated before recovery mutates state.
 */
export function createTrustedLedgerSupervisor(confirmFormerProcessExit) {
  if (typeof confirmFormerProcessExit !== "function") fail("invalid_supervisor");
  return Object.freeze({
    recover(options) {
      if (!plainObject(options)) fail("invalid_launcher");
      const { previousLease, ...launcherOptions } = options;
      const launcher = validateLauncher(launcherOptions);
      const evidence = confirmFormerProcessExit({ binding: clone(launcher.binding), previousLease: clone(previousLease) });
      if (evidence !== true) fail("ledger_unavailable");
      preflightDirectory(launcher.stateDirectory);
      const record = readRecord(launcher.stateDirectory);
      assertBinding(record, launcher.binding);
      assertLimits(record, launcher.limits);
      if (!sameLease(record.lease, previousLease)) fail("ledger_unavailable");

      const lease = newLease(record.lease_generation + 1);
      record.lease_generation = lease.generation;
      record.lease = lease;
      if (record.in_flight !== null) interruptReservation(record);
      publishAcquisition(lease, () => publish(launcher, record));
      return new MediationLedger(launcher, lease);
    },
  });
}

class MediationLedger {
  #launcher;
  #lease;
  #poisoned = false;
  #operations = new WeakMap();

  constructor(launcher, lease) {
    this.#launcher = launcher;
    this.#lease = lease;
    Object.freeze(this);
  }

  /** Trusted supervisor plumbing only; inspection requests never receive it. */
  trustedLeaseIdentity() {
    return clone(this.#lease);
  }

  begin(identity, { deadlineMs, followUp = false } = {}) {
    const normalized = validateIdentity(identity);
    if (typeof followUp !== "boolean") fail("invalid_inspection");
    if (deadlineMs !== undefined && !positiveInteger(deadlineMs)) fail("invalid_inspection");
    const record = this.#currentRecord();
    const key = identityKey(normalized);
    const prior = record.identities.find((entry) => entry.key === key);
    if (prior?.status === "completed") {
      return clone({ status: "completed", reference: prior.reference });
    }
    if (prior && !followUp) {
      return clone({ status: "followup_required", reference: prior.reference });
    }
    if (!prior && record.identities.length >= record.limits.maxIdentities) fail("identity_capacity_exhausted");
    if (record.in_flight !== null) fail("inspection_in_flight");
    if (record.attempts_used >= record.limits.maxAttempts) fail("budget_exhausted");

    const remaining = record.limits.cumulativeMs - record.charged_ms;
    if (remaining <= 0) fail("budget_exhausted");
    const reservationMs = Math.min(
      record.limits.perCallMs,
      deadlineMs ?? record.limits.perCallMs,
      remaining,
    );
    if (reservationMs <= 0) fail("budget_exhausted");

    const operationNonce = privateNonce();
    record.attempts_used += 1;
    record.charged_ms += reservationMs;
    record.in_flight = {
      binding: normalized,
      identity_key: key,
      nonce: operationNonce,
      reserved_ms: reservationMs,
    };
    this.#publish(record);

    const operation = Object.freeze({});
    this.#operations.set(operation, {
      identity: normalized,
      key,
      nonce: operationNonce,
      startedAt: this.#launcher.clock(),
    });
    return Object.freeze({ status: "reserved", operation, deadline_ms: reservationMs });
  }

  finish(operation, { outcome } = {}) {
    if (!new Set(["success", "failed", "cancelled"]).has(outcome)) fail("invalid_inspection");
    const local = this.#operations.get(operation);
    if (!local) fail("operation_mismatch");
    const record = this.#currentRecord();
    if (!record.in_flight || record.in_flight.nonce !== local.nonce) fail("operation_mismatch");

    const reservation = record.in_flight.reserved_ms;
    if (outcome === "success") {
      const elapsed = measuredElapsed(this.#launcher.clock, local.startedAt, reservation);
      record.charged_ms -= reservation - elapsed;
    }
    const prior = record.identities.find((entry) => entry.key === local.key);
    const reference = prior?.reference ?? referenceFor(local.key);
    const entry = {
      key: local.key,
      binding: local.identity,
      reference,
      status: outcome === "success" ? "completed" : outcome,
      attempts: (prior?.attempts ?? 0) + 1,
    };
    if (prior) Object.assign(prior, entry);
    else {
      if (record.identities.length >= record.limits.maxIdentities) fail("identity_capacity_exhausted");
      record.identities.push(entry);
    }
    record.in_flight = null;
    this.#publish(record);
    this.#operations.delete(operation);
    return clone({ status: entry.status, reference });
  }

  close() {
    const record = this.#currentRecord();
    if (record.in_flight !== null) fail("inspection_in_flight");
    record.lease = null;
    this.#publish(record);
  }

  status() {
    const record = this.#currentRecord();
    return clone({
      attempts_used: record.attempts_used,
      charged_ms: record.charged_ms,
      in_flight: record.in_flight !== null,
      identity_count: record.identities.length,
    });
  }

  #currentRecord() {
    if (this.#poisoned) fail("ledger_poisoned");
    const record = readRecord(this.#launcher.stateDirectory);
    assertBinding(record, this.#launcher.binding);
    assertLimits(record, this.#launcher.limits);
    if (!sameLease(record.lease, this.#lease)) fail("stale_lease");
    return record;
  }

  #publish(record) {
    try {
      publish(this.#launcher, record);
    } catch (error) {
      if (error?.uncertain === true) this.#poisoned = true;
      throw error;
    }
  }
}

function validateLauncher(value) {
  if (!plainObject(value)) fail("invalid_launcher");
  const allowed = new Set(["stateDirectory", "binding", "limits", "clock", "trustedTestHooks"]);
  if (Object.keys(value).some((key) => !allowed.has(key))) fail("invalid_launcher");
  if (typeof value.stateDirectory !== "string" || !path.isAbsolute(value.stateDirectory)) fail("invalid_launcher");
  const binding = validateBinding(value.binding);
  if (value.limits === null) fail("invalid_limits");
  const limits = normalizeLimits(value.limits === undefined ? {} : value.limits);
  if (value.clock !== undefined && typeof value.clock !== "function") fail("invalid_launcher");
  if (value.trustedTestHooks !== undefined && (!plainObject(value.trustedTestHooks) || !hasOnlyOptionalKeys(value.trustedTestHooks, new Set(["failPhase", "unmarkedFailurePhase"])))) fail("invalid_launcher");
  if (value.trustedTestHooks?.failPhase !== undefined && !WRITE_PHASES.has(value.trustedTestHooks.failPhase)) fail("invalid_launcher");
  if (value.trustedTestHooks?.unmarkedFailurePhase !== undefined && !WRITE_PHASES.has(value.trustedTestHooks.unmarkedFailurePhase)) fail("invalid_launcher");
  return {
    stateDirectory: value.stateDirectory,
    binding,
    limits,
    clock: value.clock ?? now,
    trustedTestHooks: value.trustedTestHooks,
  };
}

function validateBinding(value) {
  const fields = ["owner_authority_id", "harness_session_id", "logical_turn_id"];
  if (!plainObject(value) || Object.keys(value).length !== fields.length || fields.some((field) => !opaqueIdentifier(value[field]))) fail("invalid_binding");
  return Object.fromEntries(fields.map((field) => [field, value[field]]));
}

function validateIdentity(value) {
  const fields = ["observation_id", "source_entry_id", "source_part", "question_digest", "crop_id", "model_id", "profile_id"];
  if (!plainObject(value) || Object.keys(value).length !== fields.length) fail("invalid_inspection");
  for (const field of fields) {
    if (field !== "question_digest" && !opaqueIdentifier(value[field])) fail("invalid_inspection");
  }
  if (typeof value.question_digest !== "string" || !/^[a-f0-9]{64}$/i.test(value.question_digest)) fail("invalid_inspection");
  return Object.fromEntries(fields.map((field) => [
    field,
    field === "question_digest" ? value[field].toLowerCase() : value[field],
  ]));
}

function normalizeLimits(value) {
  if (!plainObject(value)) fail("invalid_limits");
  const names = new Set(Object.keys(DEFAULT_LIMITS));
  if (Object.keys(value).some((key) => !names.has(key))) fail("invalid_limits");
  const result = { ...DEFAULT_LIMITS };
  for (const name of names) {
    if (value[name] === undefined) continue;
    if (!positiveInteger(value[name]) || value[name] > DEFAULT_LIMITS[name]) fail("invalid_limits");
    result[name] = value[name];
  }
  if (result.perCallMs > result.cumulativeMs) fail("invalid_limits");
  return result;
}

function emptyRecord(binding, limits, lease) {
  return {
    schema: SCHEMA,
    binding,
    limits,
    lease_generation: lease.generation,
    lease,
    attempts_used: 0,
    charged_ms: 0,
    in_flight: null,
    identities: [],
  };
}

function readRecord(stateDirectory) {
  const target = ledgerPath(stateDirectory);
  let payload;
  try {
    const stat = fs.statSync(target);
    if (!stat.isFile() || stat.size <= 0 || stat.size > 128 * 1024) fail("ledger_unavailable");
    payload = JSON.parse(fs.readFileSync(target, "utf8"));
  } catch (error) {
    if (error instanceof LedgerError) throw error;
    fail("ledger_unavailable");
  }
  validateRecord(payload);
  return payload;
}

function validateRecord(record) {
  const recordFields = new Set(["schema", "binding", "limits", "lease_generation", "lease", "attempts_used", "charged_ms", "in_flight", "identities"]);
  if (!plainObject(record) || !hasOnlyKeys(record, recordFields) || record.schema !== SCHEMA) fail("ledger_unavailable");
  try {
    validateBinding(record.binding);
    const limits = normalizeLimits(record.limits);
    if (!positiveInteger(record.lease_generation) || !Number.isInteger(record.attempts_used) || record.attempts_used < 0 || !Number.isInteger(record.charged_ms) || record.charged_ms < 0 || record.charged_ms > limits.cumulativeMs || !Array.isArray(record.identities) || record.identities.length > limits.maxIdentities) fail("ledger_unavailable");
    if (record.lease !== null && !validLease(record.lease)) fail("ledger_unavailable");
    if (record.in_flight !== null) {
      const inFlightFields = new Set(["binding", "identity_key", "nonce", "reserved_ms"]);
      if (!plainObject(record.in_flight) || !hasOnlyKeys(record.in_flight, inFlightFields) || typeof record.in_flight.identity_key !== "string" || !validNonce(record.in_flight.nonce) || !positiveInteger(record.in_flight.reserved_ms) || record.in_flight.reserved_ms > limits.perCallMs) fail("ledger_unavailable");
      const binding = validateIdentity(record.in_flight.binding);
      if (record.in_flight.identity_key !== identityKey(binding)) fail("ledger_unavailable");
    }
    const keys = new Set();
    for (const entry of record.identities) {
      const entryFields = new Set(["key", "binding", "reference", "status", "attempts"]);
      if (!plainObject(entry) || !hasOnlyKeys(entry, entryFields) || typeof entry.key !== "string" || !/^inspection-[a-f0-9]{24}$/.test(entry.reference) || !["completed", "failed", "cancelled", "interrupted"].includes(entry.status) || !positiveInteger(entry.attempts)) fail("ledger_unavailable");
      const binding = validateIdentity(entry.binding);
      if (entry.key !== identityKey(binding) || entry.reference !== referenceFor(entry.key) || keys.has(entry.key)) fail("ledger_unavailable");
      keys.add(entry.key);
    }
  } catch {
    fail("ledger_unavailable");
  }
}

function preflightDirectory(stateDirectory) {
  if (process.platform !== "linux") fail("durability_unsupported");
  try {
    const stat = fs.statSync(stateDirectory);
    if (!stat.isDirectory() || (stat.mode & 0o077) !== 0) fail("durability_unsupported");
    const descriptor = fs.openSync(stateDirectory, "r");
    try { fs.fsyncSync(descriptor); } finally { fs.closeSync(descriptor); }
  } catch (error) {
    if (error instanceof LedgerError) throw error;
    fail("durability_unsupported");
  }
}

function publishInitial(launcher, record) {
  const target = ledgerPath(launcher.stateDirectory);
  const temporary = temporaryPath(launcher.stateDirectory);
  let linkAttempted = false;
  try {
    writeTemporary(launcher, temporary, record);
    failAt(launcher, "before_replace");
    linkAttempted = true;
    failAt(launcher, "replace");
    fs.linkSync(temporary, target);
    failAt(launcher, "after_replace");
    syncDirectory(launcher);
  } catch (error) {
    if (error.code === "EEXIST") fail("ledger_unavailable");
    const uncertain = linkAttempted
      || error?.phase === "replace"
      || error?.phase === "after_replace"
      || error?.phase === "directory_sync";
    throw publicationError(error, uncertain);
  } finally { removeTemporary(temporary); }
}

function publish(launcher, record) {
  const target = ledgerPath(launcher.stateDirectory);
  const temporary = temporaryPath(launcher.stateDirectory);
  let replaceAttempted = false;
  try {
    writeTemporary(launcher, temporary, record);
    failAt(launcher, "before_replace");
    replaceAttempted = true;
    failAt(launcher, "replace");
    fs.renameSync(temporary, target);
    failAt(launcher, "after_replace");
    syncDirectory(launcher);
  } catch (error) {
    const uncertain = replaceAttempted || error?.phase === "replace" || error?.phase === "after_replace" || error?.phase === "directory_sync";
    throw publicationError(error, uncertain);
  } finally { removeTemporary(temporary); }
}

function writeTemporary(launcher, temporary, record) {
  let descriptor;
  try {
    failAt(launcher, "file_write");
    descriptor = fs.openSync(temporary, "wx", 0o600);
    fs.writeFileSync(descriptor, `${JSON.stringify(record)}\n`, "utf8");
    failAt(launcher, "file_sync");
    fs.fsyncSync(descriptor);
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor);
  }
}

function syncDirectory(launcher) {
  failAt(launcher, "directory_sync");
  const descriptor = fs.openSync(launcher.stateDirectory, "r");
  try { fs.fsyncSync(descriptor); } finally { fs.closeSync(descriptor); }
}

function failAt(launcher, phase) {
  const hooks = launcher.trustedTestHooks;
  const marked = hooks?.failPhase === phase;
  const unmarked = hooks?.unmarkedFailurePhase === phase;
  if (!marked && !unmarked) return;
  if (marked) delete hooks.failPhase;
  if (unmarked) delete hooks.unmarkedFailurePhase;
  const error = new Error(`injected_${phase}`);
  if (marked) error.phase = phase;
  throw error;
}

function removeTemporary(temporary) {
  try { fs.unlinkSync(temporary); } catch { /* preserve the typed publication result */ }
}

function publicationError(error, uncertain) {
  if (error instanceof LedgerError) return error;
  const wrapped = new LedgerError("durability_failed");
  wrapped.uncertain = uncertain;
  return wrapped;
}

function publishAcquisition(lease, publishRecord) {
  try {
    publishRecord();
  } catch (error) {
    if (error instanceof LedgerError && error.code === "durability_failed") {
      attachTrustedLeaseIdentity(error, lease);
    }
    throw error;
  }
}

function attachTrustedLeaseIdentity(error, lease) {
  const attemptedLease = clone(lease);
  Object.defineProperty(error, "trustedLeaseIdentity", {
    enumerable: false,
    value: () => clone(attemptedLease),
  });
}

function interruptReservation(record) {
  const operation = record.in_flight;
  const prior = record.identities.find((entry) => entry.key === operation.identity_key);
  const entry = {
    key: operation.identity_key,
    binding: prior?.binding ?? operation.binding,
    reference: prior?.reference ?? referenceFor(operation.identity_key),
    status: "interrupted",
    attempts: (prior?.attempts ?? 0) + 1,
  };
  if (prior) Object.assign(prior, entry);
  else record.identities.push(entry);
  record.in_flight = null;
}

function assertBinding(record, expected) {
  if (JSON.stringify(record.binding) !== JSON.stringify(expected)) fail("ledger_unavailable");
}

function assertLimits(record, expected) {
  if (JSON.stringify(record.limits) !== JSON.stringify(expected)) fail("ledger_unavailable");
}

function newLease(generation) {
  return { generation, nonce: privateNonce() };
}

function validLease(value) {
  return plainObject(value) && hasOnlyKeys(value, new Set(["generation", "nonce"])) && positiveInteger(value.generation) && validNonce(value.nonce);
}

function sameLease(left, right) {
  return validLease(left) && validLease(right) && left.generation === right.generation && left.nonce === right.nonce;
}

function validNonce(value) {
  return typeof value === "string" && /^[a-f0-9]{48}$/.test(value);
}

function privateNonce() {
  return randomBytes(24).toString("hex");
}

function identityKey(identity) {
  return createHash("sha256").update(JSON.stringify(identity)).digest("hex");
}

function referenceFor(key) {
  return `inspection-${key.slice(0, 24)}`;
}

function measuredElapsed(clock, startedAt, reservation) {
  const measured = Math.ceil(clock() - startedAt);
  if (!Number.isFinite(measured)) fail("invalid_clock");
  return Math.min(reservation, Math.max(0, measured));
}

function ledgerPath(stateDirectory) {
  return path.join(stateDirectory, LEDGER_FILE);
}

function temporaryPath(stateDirectory) {
  return path.join(stateDirectory, `.${LEDGER_FILE}.${privateNonce()}.tmp`);
}

function plainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;
}

function hasOnlyKeys(value, allowed) {
  return Object.keys(value).every((key) => allowed.has(key)) && Object.keys(value).length === allowed.size;
}

function hasOnlyOptionalKeys(value, allowed) {
  return Object.keys(value).every((key) => allowed.has(key));
}

function positiveInteger(value) {
  return Number.isSafeInteger(value) && value > 0;
}

function opaqueIdentifier(value) {
  return typeof value === "string" && byteLength(value) > 0 && byteLength(value) <= 128 && /^[A-Za-z0-9._:-]+$/.test(value);
}
