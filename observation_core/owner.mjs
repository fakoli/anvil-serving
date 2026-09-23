import { createHash, randomBytes, randomUUID } from "node:crypto";
import * as fs from "node:fs";
import path from "node:path";
import { createMediationLedger, LedgerError, reopenMediationLedger } from "../browser_owner/mediation_ledger.mjs";
import { PngError, validatePng } from "./png.mjs";

const SCHEMA = "observation-owner/v1";
const MEDIATION_SCHEMA = "observation-mediation/v1";
const MAX_OBSERVATIONS = 64;
const MAX_BYTES = 256 * 1024 * 1024;
const TTL_MS = 60 * 60 * 1000;
const MAX_ENVELOPE_BYTES = 8192;
const FILES = Object.freeze({ metadata: "observation-owner-metadata.json", receipts: "observation-owner-receipts.json", tombstones: "observation-owner-tombstones.json", ledger: "mediation-inspection-ledger.json" });
const ERROR_CODES = new Set(["budget_exhausted", "budget_state_unavailable", "unsupported_history", "invalid_image", "image_limit", "expired_observation", "stale_observation", "access_denied", "cancelled", "deadline_exceeded", "endpoint_unavailable", "invalid_response", "result_too_large"]);
const TRANSPORT_ERRORS = new Set(["cancelled", "deadline_exceeded", "endpoint_unavailable", "invalid_response", "result_too_large"]);
const INSPECTION_STATUS = new Set(["observed", "inconclusive", "unsupported"]);
const FACT_KINDS = new Set(["visible_text", "visual_fact"]);
const UNCERTAINTIES = new Set(["literal_unverified", "unverified_interpretation", "unreadable"]);

export class ObservationError extends Error {
  constructor(code) { super(code); this.code = code; }
}

const fail = (code) => { throw new ObservationError(code); };
const bytes = (value) => Buffer.byteLength(value, "utf8");
const digest = (value) => createHash("sha256").update(value).digest("hex");
const plain = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;
const opaque = (value, maximum = 64) => typeof value === "string" && bytes(value) > 0 && bytes(value) <= maximum && /^[A-Za-z0-9._:-]+$/.test(value);
const clone = (value) => structuredClone(value);

/**
 * Trusted Pi-adapter API. `create` requires priorSession:false; `reopen`
 * requires priorSession:true. bind({entryId, imagePart, image:{mimeType,data}})
 * accepts only an active image/png part; inspect({observationId, question},
 * {signal, followUp}) accepts only an opaque observation ID and bounded question. The
 * injected inspector receives {image:{mimeType,data}, question, signal} and returns exactly
 * {inspection_status, facts, reason}; it cannot choose owner metadata.
 */
export function createObservationOwner(options) { return open(options, false); }
export function reopenObservationOwner(options) { return open(options, true); }

function open(options, reopening) {
  const config = normalizeConfig(options, reopening);
  preflightDirectory(config.stateDirectory);
  if (reopening) {
    const state = readState(config);
    assertCompletedReceipts(config, state);
    let ledger;
    try { ledger = reopenMediationLedger(ledgerOptions(config)); } catch { fail("budget_state_unavailable"); }
    return new ObservationOwner(config, ledger, state);
  }
  for (const file of Object.values(FILES)) if (fs.existsSync(filePath(config, file))) fail("budget_state_unavailable");
  const state = emptyState(config);
  writeNew(config, FILES.metadata, state.metadata);
  writeNew(config, FILES.receipts, state.receipts);
  writeNew(config, FILES.tombstones, state.tombstones);
  let ledger;
  try { ledger = createMediationLedger(ledgerOptions(config)); } catch { fail("budget_state_unavailable"); }
  return new ObservationOwner(config, ledger, state);
}

class ObservationOwner {
  #config;
  #ledger;
  #state;
  #images = new Map();
  #closed = false;
  #poisoned = false;
  #active = null;
  #abort = null;

  constructor(config, ledger, state) { this.#config = config; this.#ledger = ledger; this.#state = state; Object.freeze(this); }

  bind(request) {
    if (this.#closed || this.#poisoned) return this.#error("budget_state_unavailable");
    try {
      this.#expire();
      const input = validateBind(request);
      if (input.image.mimeType !== "image/png") return this.#error("invalid_image");
      let accepted;
      try { accepted = validatePng(input.image.data, this.#config.pngLimits); } catch (error) { return this.#error(pngError(error)); }
      const sourceId = sourceIdFor(this.#config.binding, input.entryId, input.imagePart);
      const existing = this.#state.metadata.observations.find((entry) => entry.source_ref.source_id === sourceId && entry.source_ref.image_part === input.imagePart);
      if (this.#state.tombstones.entries.some((entry) => entry.source_id === sourceId && entry.image_part === input.imagePart)) return this.#error("expired_observation");
      const imageDigest = digest(accepted.png);
      if (existing) {
        if (existing.digest !== imageDigest || existing.entry_id !== input.entryId) return this.#error("stale_observation");
        this.#images.set(existing.observation_id, accepted.png);
        return this.#questionRequired(existing);
      }
      if (this.#state.metadata.observations.length + this.#state.tombstones.entries.length >= MAX_OBSERVATIONS || this.#state.metadata.bytes_used + accepted.png.length > MAX_BYTES) return this.#error("image_limit");
      const now = this.#now();
      const entry = {
        observation_id: `observation-${randomUUID()}`,
        source_ref: { source_id: sourceId, image_part: input.imagePart },
        entry_id: input.entryId,
        digest: imageDigest,
        byte_length: accepted.png.length,
        width: accepted.width,
        height: accepted.height,
        created_at: now,
        last_read_at: now,
      };
      this.#state.metadata.observations.push(entry);
      this.#state.metadata.bytes_used += entry.byte_length;
      this.#publish(FILES.metadata, this.#state.metadata);
      this.#images.set(entry.observation_id, accepted.png);
      return this.#questionRequired(entry);
    } catch (error) { return this.#error(error instanceof ObservationError ? error.code : "budget_state_unavailable"); }
  }

  async inspect(request, { signal, followUp = false } = {}) {
    if (this.#closed || this.#poisoned) return this.#error("budget_state_unavailable");
    if (this.#active) return this.#error("budget_state_unavailable");
    const run = this.#inspect(request, signal, followUp);
    this.#active = run;
    try { return await run; } finally { this.#active = null; }
  }

  async close() {
    if (this.#closed) return;
    this.#closed = true;
    this.#abort?.abort();
    await this.#active?.catch(() => {});
    try { this.#ledger.close(); } catch { this.#poisoned = true; }
  }

  async #inspect(request, signal, followUp) {
    let entry, requestId, question, questionDigest, questionId;
    try {
      this.#expire();
      ({ observationId: requestId, question } = validateInspect(request));
      if (typeof followUp !== "boolean") fail("budget_state_unavailable");
      entry = this.#state.metadata.observations.find((candidate) => candidate.observation_id === requestId);
      if (!entry) {
        const tombstone = this.#state.tombstones.entries.find((candidate) => candidate.observation_id === requestId);
        return tombstone ? this.#error("expired_observation", { observation_id: tombstone.observation_id, source_ref: { source_id: tombstone.source_id, image_part: tombstone.image_part } }) : this.#error("access_denied");
      }
      if (!this.#images.has(entry.observation_id)) return this.#error("stale_observation", entry);
      questionDigest = digest(question); questionId = `question-${questionDigest.slice(0, 32)}`;
    } catch (error) { return this.#error(error instanceof ObservationError ? error.code : "budget_state_unavailable"); }

    const identity = { observation_id: entry.observation_id, source_entry_id: entry.source_ref.source_id, source_part: `part-${entry.source_ref.image_part}`, question_digest: questionDigest, crop_id: "whole-image", model_id: this.#config.modelId, profile_id: this.#config.profileId };
    let reservation;
    try { reservation = this.#ledger.begin(identity, { followUp }); } catch (error) { return this.#error(ledgerError(error), entry, null, questionId); }
    if (reservation.status === "completed") return this.#cached(reservation.reference, identity, entry, questionId);
    if (reservation.status === "followup_required") return this.#error("budget_state_unavailable", entry, null, questionId);

    const controller = new AbortController();
    this.#abort = controller;
    const outcome = await callInspector(this.#config.inspector, { mimeType: "image/png", data: Buffer.from(this.#images.get(entry.observation_id)) }, question, controller, signal, reservation.deadline_ms);
    this.#abort = null;
    if (this.#closed || outcome.cancelled) return this.#finishError(reservation.operation, "cancelled", "cancelled", entry, questionId);
    if (outcome.deadline) return this.#finishError(reservation.operation, "cancelled", "deadline_exceeded", entry, questionId);
    if (outcome.error) return this.#finishError(reservation.operation, "failed", transportError(outcome.error), entry, questionId);

    let inspection;
    try { inspection = validateInspection(outcome.value); } catch (error) { return this.#finishError(reservation.operation, "failed", error instanceof ObservationError && error.code === "result_too_large" ? "result_too_large" : "invalid_response", entry, questionId); }
    let response;
    try {
      response = this.#inspected(entry, `inspection-${"0".repeat(24)}`, questionId, inspection);
      if (bytes(JSON.stringify(response)) > MAX_ENVELOPE_BYTES) return this.#finishError(reservation.operation, "failed", "result_too_large", entry, questionId);
    } catch { return this.#finishError(reservation.operation, "failed", "invalid_response", entry, questionId); }
    let finished;
    try { finished = this.#ledger.finish(reservation.operation, { outcome: "success" }); } catch { return this.#error("budget_state_unavailable", entry, null, questionId); }
    response = envelope({ ...response, inspection_request_id: finished.reference });
    try {
      this.#state.receipts.receipts.push({ ledger_reference: finished.reference, identity, envelope: clone(response) });
      this.#publish(FILES.receipts, this.#state.receipts);
      this.#touch(entry);
    } catch { return this.#error("budget_state_unavailable", entry, null, questionId); }
    return response;
  }

  #cached(reference, identity, entry, questionId) {
    const receipt = this.#state.receipts.receipts.find((candidate) => candidate.ledger_reference === reference && JSON.stringify(candidate.identity) === JSON.stringify(identity));
    if (!receipt) return this.#error("budget_state_unavailable", entry, null, questionId);
    try { this.#touch(entry); } catch { return this.#error("budget_state_unavailable", entry, null, questionId); }
    return clone(receipt.envelope);
  }

  #finishError(operation, outcome, code, entry, questionId) {
    try { this.#ledger.finish(operation, { outcome }); } catch { this.#poisoned = true; return this.#error("budget_state_unavailable", entry, null, questionId); }
    return this.#error(code, entry, null, questionId);
  }

  #questionRequired(entry) { return envelope({ mediation_id: `mediation-${randomUUID()}`, source_ref: clone(entry.source_ref), observation_id: entry.observation_id, inspection_request_id: null, question_id: null, status: "question_required", inspection_status: null, facts: [], reason: "question_required", error: null }); }
  #inspected(entry, inspectionRequestId, questionId, inspection) { return envelope({ mediation_id: `mediation-${randomUUID()}`, source_ref: clone(entry.source_ref), observation_id: entry.observation_id, inspection_request_id: inspectionRequestId || null, question_id: questionId, status: "inspected", inspection_status: inspection.inspection_status, facts: inspection.facts, reason: inspection.reason, error: null }); }
  #error(code, entry, inspectionRequestId = null, questionId = null) {
    if (code === "access_denied") return Object.freeze({ schema: MEDIATION_SCHEMA, mediation_id: `mediation-${randomUUID()}`, status: "error", error: "access_denied" });
    const safe = ERROR_CODES.has(code) ? code : "budget_state_unavailable";
    return envelope({ mediation_id: `mediation-${randomUUID()}`, source_ref: entry ? clone(entry.source_ref) : null, observation_id: entry?.observation_id ?? null, inspection_request_id: inspectionRequestId, question_id: questionId, status: "error", inspection_status: null, facts: [], reason: safe, error: safe });
  }
  #touch(entry) { entry.last_read_at = this.#now(); this.#publish(FILES.metadata, this.#state.metadata); }
  #expire() {
    const now = this.#now();
    const expired = this.#state.metadata.observations.filter((entry) => now - entry.last_read_at >= TTL_MS);
    if (expired.length === 0) return;
    if (this.#state.tombstones.entries.length + expired.length > MAX_OBSERVATIONS) fail("budget_state_unavailable");
    for (const entry of expired) this.#state.tombstones.entries.push({ observation_id: entry.observation_id, entry_id: entry.entry_id, source_id: entry.source_ref.source_id, image_part: entry.source_ref.image_part, expired_at: now });
    this.#publish(FILES.tombstones, this.#state.tombstones);
    this.#state.metadata.observations = this.#state.metadata.observations.filter((entry) => !expired.includes(entry));
    this.#state.metadata.bytes_used -= expired.reduce((total, entry) => total + entry.byte_length, 0);
    this.#publish(FILES.metadata, this.#state.metadata);
    for (const entry of expired) this.#images.delete(entry.observation_id);
  }
  #publish(file, value) { try { writeReplace(this.#config, file, value); } catch { this.#poisoned = true; fail("budget_state_unavailable"); } }
  #now() { const value = this.#config.clock(); if (!Number.isSafeInteger(value) || value < 0) fail("budget_state_unavailable"); return value; }
}

function normalizeConfig(value, reopening) {
  const fields = new Set(["stateDirectory", "authorityId", "sessionId", "modelId", "profileId", "inspector", "priorSession", "clock", "pngLimits", "ledgerLimits"]);
  if (!plain(value) || Object.keys(value).some((key) => !fields.has(key)) || value.priorSession !== reopening || typeof value.stateDirectory !== "string" || !path.isAbsolute(value.stateDirectory) || !opaque(value.authorityId) || !opaque(value.sessionId) || !opaque(value.modelId) || !opaque(value.profileId) || typeof value.inspector !== "function" || (value.clock !== undefined && typeof value.clock !== "function")) fail("budget_state_unavailable");
  const pngLimits = validatePngLimits(value.pngLimits);
  const binding = { owner_authority_id: value.authorityId, harness_session_id: value.sessionId, logical_turn_id: `turn-${digest(`${value.authorityId}\u0000${value.sessionId}`)}` };
  return { stateDirectory: value.stateDirectory, binding, modelId: value.modelId, profileId: value.profileId, inspector: value.inspector, clock: value.clock ?? Date.now, pngLimits, ledgerLimits: value.ledgerLimits };
}

function validatePngLimits(value) {
  if (value === undefined) return {};
  if (!plain(value) || Object.keys(value).some((key) => key !== "maxEncodedBytes" && key !== "maxPixels")) fail("budget_state_unavailable");
  for (const [name, maximum] of [["maxEncodedBytes", 8 * 1024 * 1024], ["maxPixels", 8_000_000]]) if (Object.hasOwn(value, name) && (!Number.isInteger(value[name]) || value[name] < 1 || value[name] > maximum)) fail("budget_state_unavailable");
  return clone(value);
}

function ledgerOptions(config) { return { stateDirectory: config.stateDirectory, binding: config.binding, ...(config.ledgerLimits === undefined ? {} : { limits: config.ledgerLimits }), clock: config.clock }; }
function sourceIdFor(binding, entryId, imagePart) { return `source-${digest(`${binding.owner_authority_id}\u0000${binding.harness_session_id}\u0000${entryId}\u0000${imagePart}`).slice(0, 48)}`; }
function validateBind(value) {
  if (!plain(value) || Object.keys(value).length !== 3 || !opaque(value.entryId) || !Number.isSafeInteger(value.imagePart) || value.imagePart < 0 || !plain(value.image) || Object.keys(value.image).length !== 2 || typeof value.image.mimeType !== "string" || typeof value.image.data !== "string") fail("access_denied");
  return value;
}
function validateInspect(value) {
  if (!plain(value) || Object.keys(value).length !== 2 || !opaque(value.observationId) || typeof value.question !== "string" || bytes(value.question) === 0 || bytes(value.question) > 512) fail("access_denied");
  return value;
}
function validateInspection(value) {
  if (!plain(value) || Object.keys(value).length !== 3 || !INSPECTION_STATUS.has(value.inspection_status) || !Array.isArray(value.facts) || typeof value.reason !== "string" || bytes(value.reason) > 256 || value.facts.length > 32) fail("invalid_response");
  if (value.inspection_status === "observed" ? value.reason !== "" : value.facts.length !== 0) fail("invalid_response");
  for (const fact of value.facts) {
    if (!plain(fact) || Object.keys(fact).length !== 4 || !FACT_KINDS.has(fact.kind) || typeof fact.text !== "string" || bytes(fact.text) > 512 || !UNCERTAINTIES.has(fact.uncertainty) || fact.region_ref !== null || (fact.uncertainty === "unreadable" && fact.text !== "")) fail("invalid_response");
  }
  if (bytes(JSON.stringify(value)) > 65536) fail("result_too_large");
  return clone(value);
}
function envelope(value) {
  const result = { schema: MEDIATION_SCHEMA, ...value };
  const fields = ["schema", "mediation_id", "source_ref", "observation_id", "inspection_request_id", "question_id", "status", "inspection_status", "facts", "reason", "error"];
  const source = result.source_ref;
  if (Object.keys(result).length !== fields.length || fields.some((field) => !Object.hasOwn(result, field)) || !opaque(result.mediation_id) || !["inspected", "question_required", "error"].includes(result.status) || !Array.isArray(result.facts) || typeof result.reason !== "string" || bytes(result.reason) > 256 || (source !== null && (!plain(source) || Object.keys(source).length !== 2 || !opaque(source.source_id) || !Number.isSafeInteger(source.image_part) || source.image_part < 0))) fail("budget_state_unavailable");
  if (result.observation_id !== null && !opaque(result.observation_id) || result.inspection_request_id !== null && !opaque(result.inspection_request_id) || result.question_id !== null && !opaque(result.question_id) || ![null, ...INSPECTION_STATUS].includes(result.inspection_status) || result.error !== null && !ERROR_CODES.has(result.error)) fail("budget_state_unavailable");
  if (result.status === "inspected" && (result.source_ref === null || result.observation_id === null || result.inspection_request_id === null || result.question_id === null || result.error !== null || result.inspection_status === null || (result.inspection_status !== "observed" && result.facts.length !== 0))) fail("budget_state_unavailable");
  if (result.status === "question_required" && (result.source_ref === null || result.observation_id === null || result.inspection_request_id !== null || result.question_id !== null || result.inspection_status !== null || result.error !== null || result.facts.length !== 0)) fail("budget_state_unavailable");
  if (result.status === "error" && (result.inspection_status !== null || result.facts.length !== 0 || result.error === null)) fail("budget_state_unavailable");
  return Object.freeze(result);
}
function pngError(error) { return error instanceof PngError && error.code === "image_limit" ? "image_limit" : "invalid_image"; }
function ledgerError(error) { return error instanceof LedgerError && error.code === "budget_exhausted" ? "budget_exhausted" : "budget_state_unavailable"; }
function transportError(error) { return TRANSPORT_ERRORS.has(error?.code) ? error.code : "endpoint_unavailable"; }

async function callInspector(inspector, image, question, controller, signal, deadlineMs) {
  let timeout, remove;
  const cancelled = Object.freeze({});
  let expired = false;
  const stopped = new Promise((resolve) => controller.signal.addEventListener("abort", () => resolve(expired ? { deadline: true } : { cancelled: true }), { once: true }));
  const abort = () => controller.abort();
  if (signal?.aborted) abort(); else if (signal?.addEventListener) { signal.addEventListener("abort", abort, { once: true }); remove = () => signal.removeEventListener("abort", abort); }
  timeout = setTimeout(() => { expired = true; controller.abort(); }, deadlineMs);
  const work = Promise.resolve().then(() => controller.signal.aborted ? cancelled : inspector(Object.freeze({ image, question, signal: controller.signal }))).then((value) => value === cancelled ? { cancelled: true } : { value }, (error) => ({ error }));
  try { return await Promise.race([work, stopped]); } finally { clearTimeout(timeout); remove?.(); }
}

function emptyState(config) {
  const common = { schema: SCHEMA, binding: config.binding, model_id: config.modelId, profile_id: config.profileId, png_limits: config.pngLimits };
  return { metadata: { ...common, kind: "metadata", bytes_used: 0, observations: [] }, receipts: { ...common, kind: "receipts", receipts: [] }, tombstones: { ...common, kind: "tombstones", entries: [] } };
}
function readState(config) {
  const state = { metadata: readJson(config, FILES.metadata, 128 * 1024), receipts: readJson(config, FILES.receipts, 1024 * 1024), tombstones: readJson(config, FILES.tombstones, 128 * 1024) };
  const expected = emptyState(config);
  for (const [kind, record] of Object.entries(state)) {
    const base = expected[kind];
    if (!plain(record) || record.schema !== SCHEMA || record.kind !== kind || JSON.stringify(record.binding) !== JSON.stringify(base.binding) || record.model_id !== base.model_id || record.profile_id !== base.profile_id || JSON.stringify(record.png_limits) !== JSON.stringify(base.png_limits)) fail("budget_state_unavailable");
  }
  if (!Number.isSafeInteger(state.metadata.bytes_used) || state.metadata.bytes_used < 0 || !Array.isArray(state.metadata.observations) || state.metadata.observations.length > MAX_OBSERVATIONS || !Array.isArray(state.receipts.receipts) || state.receipts.receipts.length > MAX_OBSERVATIONS || !Array.isArray(state.tombstones.entries) || state.tombstones.entries.length > MAX_OBSERVATIONS || state.metadata.observations.length + state.tombstones.entries.length > MAX_OBSERVATIONS) fail("budget_state_unavailable");
  const ids = new Set(), activeSources = new Set(); let total = 0;
  for (const entry of state.metadata.observations) { validateEntry(entry, config.binding); const source = `${entry.source_ref.source_id}:${entry.source_ref.image_part}`; if (ids.has(entry.observation_id) || activeSources.has(source)) fail("budget_state_unavailable"); ids.add(entry.observation_id); activeSources.add(source); total += entry.byte_length; }
  if (total !== state.metadata.bytes_used || total > MAX_BYTES) fail("budget_state_unavailable");
  const tombstones = new Set();
  for (const entry of state.tombstones.entries) { const source = plain(entry) ? `${entry.source_id}:${entry.image_part}` : ""; if (!plain(entry) || Object.keys(entry).length !== 5 || !opaque(entry.observation_id) || !opaque(entry.entry_id) || !opaque(entry.source_id) || !Number.isSafeInteger(entry.image_part) || entry.image_part < 0 || entry.source_id !== sourceIdFor(config.binding, entry.entry_id, entry.image_part) || !Number.isSafeInteger(entry.expired_at) || entry.expired_at < 0 || tombstones.has(source) || activeSources.has(source) || ids.has(entry.observation_id)) fail("budget_state_unavailable"); tombstones.add(source); ids.add(entry.observation_id); }
  for (const receipt of state.receipts.receipts) {
    if (!plain(receipt) || Object.keys(receipt).length !== 3 || !opaque(receipt.ledger_reference) || !plain(receipt.identity) || !plain(receipt.envelope)) fail("budget_state_unavailable");
    try { validateReceipt(receipt, config); } catch { fail("budget_state_unavailable"); }
    const source = `${receipt.envelope.source_ref?.source_id}:${receipt.envelope.source_ref?.image_part}`;
    if (!state.metadata.observations.some((entry) => entry.observation_id === receipt.envelope.observation_id && `${entry.source_ref.source_id}:${entry.source_ref.image_part}` === source) && !tombstones.has(source)) fail("budget_state_unavailable");
  }
  return state;
}
function validateReceipt(receipt, config) {
  const fields = ["observation_id", "source_entry_id", "source_part", "question_digest", "crop_id", "model_id", "profile_id"];
  const identity = receipt.identity, response = receipt.envelope;
  if (Object.keys(identity).length !== fields.length || fields.some((field) => !Object.hasOwn(identity, field)) || !opaque(identity.observation_id) || !opaque(identity.source_entry_id) || !/^part-[0-9]+$/.test(identity.source_part) || !/^[a-f0-9]{64}$/.test(identity.question_digest) || identity.crop_id !== "whole-image" || identity.model_id !== config.modelId || identity.profile_id !== config.profileId) fail("budget_state_unavailable");
  envelope(response); validateInspection({ inspection_status: response.inspection_status, facts: response.facts, reason: response.reason });
  const part = Number(identity.source_part.slice(5));
  if (!Number.isSafeInteger(part) || response.observation_id !== identity.observation_id || response.source_ref?.source_id !== identity.source_entry_id || response.source_ref?.image_part !== part || response.question_id !== `question-${identity.question_digest.slice(0, 32)}` || response.inspection_request_id !== receipt.ledger_reference || bytes(JSON.stringify(response)) > MAX_ENVELOPE_BYTES) fail("budget_state_unavailable");
}
function validateEntry(entry, binding) { if (!plain(entry) || Object.keys(entry).length !== 9 || !opaque(entry.observation_id) || !plain(entry.source_ref) || Object.keys(entry.source_ref).length !== 2 || !opaque(entry.source_ref.source_id) || !Number.isSafeInteger(entry.source_ref.image_part) || entry.source_ref.image_part < 0 || !opaque(entry.entry_id) || entry.source_ref.source_id !== sourceIdFor(binding, entry.entry_id, entry.source_ref.image_part) || !/^[a-f0-9]{64}$/.test(entry.digest) || !Number.isSafeInteger(entry.byte_length) || entry.byte_length < 1 || entry.byte_length > 8 * 1024 * 1024 || !Number.isSafeInteger(entry.width) || entry.width < 1 || !Number.isSafeInteger(entry.height) || entry.height < 1 || !Number.isSafeInteger(entry.created_at) || !Number.isSafeInteger(entry.last_read_at) || entry.created_at < 0 || entry.last_read_at < entry.created_at) fail("budget_state_unavailable"); }
function assertCompletedReceipts(config, state) {
  const ledger = readJson(config, FILES.ledger, 128 * 1024);
  if (!Array.isArray(ledger.identities)) fail("budget_state_unavailable");
  const completed = new Map(ledger.identities.filter((entry) => entry.status === "completed").map((entry) => [entry.reference, entry.binding]));
  if (completed.size !== state.receipts.receipts.length || state.receipts.receipts.some((receipt) => !completed.has(receipt.ledger_reference) || JSON.stringify(receipt.identity) !== JSON.stringify(completed.get(receipt.ledger_reference)))) fail("budget_state_unavailable");
}
function preflightDirectory(directory) { try { const stat = fs.lstatSync(directory); if (!stat.isDirectory() || stat.isSymbolicLink() || (stat.mode & 0o077) !== 0 || process.platform !== "linux") fail("budget_state_unavailable"); } catch (error) { if (error instanceof ObservationError) throw error; fail("budget_state_unavailable"); } }
function filePath(config, file) { return path.join(config.stateDirectory, file); }
function readJson(config, file, maximum) { try { const target = filePath(config, file), stat = fs.lstatSync(target); if (!stat.isFile() || stat.isSymbolicLink() || (stat.mode & 0o077) !== 0 || stat.size < 2 || stat.size > maximum) fail("budget_state_unavailable"); return JSON.parse(fs.readFileSync(target, "utf8")); } catch (error) { if (error instanceof ObservationError) throw error; fail("budget_state_unavailable"); } }
function writeNew(config, file, value) { writeAtomic(config, file, value, true); }
function writeReplace(config, file, value) { writeAtomic(config, file, value, false); }
function writeAtomic(config, file, value, initial) {
  const target = filePath(config, file), temporary = path.join(config.stateDirectory, `.${file}.${randomBytes(12).toString("hex")}.tmp`);
  let descriptor;
  try { descriptor = fs.openSync(temporary, "wx", 0o600); fs.writeFileSync(descriptor, `${JSON.stringify(value)}\n`, "utf8"); fs.fsyncSync(descriptor); fs.closeSync(descriptor); descriptor = undefined; if (initial) fs.linkSync(temporary, target); else fs.renameSync(temporary, target); const directory = fs.openSync(config.stateDirectory, "r"); try { fs.fsyncSync(directory); } finally { fs.closeSync(directory); } } catch { fail("budget_state_unavailable"); } finally { if (descriptor !== undefined) fs.closeSync(descriptor); try { fs.unlinkSync(temporary); } catch {} }
}
