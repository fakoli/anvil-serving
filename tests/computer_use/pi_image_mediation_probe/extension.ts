import { createHash } from "node:crypto";
import * as fs from "node:fs";
import path from "node:path";
import { Type } from "typebox";
import { sessionEntryToContextMessages } from "@earendil-works/pi-coding-agent";
import { createMediationLedger, createTrustedLedgerSupervisor, LedgerError, reopenMediationLedger } from "../../../browser_owner/mediation_ledger.mjs";

const TOOL_QUESTION = "What is in the fixture tool image?";
const RECEIPT_FILE = "fixture-observation-receipts.json";
const RECEIPT_PENDING_FILE = "fixture-observation-receipts.pending";
const MAX_RECEIPTS = 8, MAX_RECEIPT_BYTES = 32 * 1024, MAX_ANSWER_BYTES = 8 * 1024, MAX_RESPONSE_BYTES = 64 * 1024;

class MediationError extends Error { readonly code: string; constructor(code: string) { super(code); this.code = code; } }
const bytes = (value: string) => Buffer.byteLength(value, "utf8");
const digest = (value: string) => createHash("sha256").update(value).digest("hex");
const wait = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const opaque = (value: unknown, limit = 64): value is string => typeof value === "string" && bytes(value) > 0 && bytes(value) <= limit && /^[A-Za-z0-9._:-]+$/.test(value);
const plain = (value: any) => Boolean(value) && typeof value === "object" && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;

function rawMedia(value: any): boolean {
  if (Array.isArray(value)) return value.some(rawMedia);
  if (!value || typeof value !== "object") return typeof value === "string" && /^data:image\//i.test(value);
  if (value.type === "image" || value.type === "image_url") return true;
  if (typeof value.url === "string" && value.url.startsWith("data:image/")) return true;
  return Object.values(value).some(rawMedia);
}
function abort(ctx: any, code: string): never { console.error(`PI_IMAGE_MEDIATION_ERROR:${code}`); ctx.abort(); throw new MediationError(code); }
function exactKeys(value: any, fields: string[]) { return plain(value) && Object.keys(value).length === fields.length && fields.every((field) => field in value); }

function canonicalPart(part: any) {
  if (part?.type === "text" && typeof part.text === "string") return { type: "text", text: part.text };
  if (part?.type === "image" && typeof part.mimeType === "string" && typeof part.data === "string") return { type: "image", mimeType: part.mimeType, data: part.data };
  if (part?.type === "toolCall" && opaque(part.id) && opaque(part.name) && plain(part.arguments)) return { type: "toolCall", id: part.id, name: part.name, arguments: part.arguments };
  throw new MediationError("unsupported_history");
}
function canonicalMessage(message: any) {
  if ((message?.role === "compactionSummary" || message?.type === "compaction") && typeof message.summary === "string" && Number.isSafeInteger(message.tokensBefore)) {
    const timestamp = message.role === "compactionSummary" ? message.timestamp : new Date(message.timestamp).getTime();
    if (Number.isFinite(timestamp)) return { role: "compactionSummary", summary: message.summary, tokensBefore: message.tokensBefore, timestamp };
  }
  if ((message?.role === "branchSummary" || message?.type === "branch_summary") && typeof message.summary === "string" && opaque(message.fromId)) {
    const timestamp = message.role === "branchSummary" ? message.timestamp : new Date(message.timestamp).getTime();
    if (Number.isFinite(timestamp)) return { role: "branchSummary", summary: message.summary, fromId: message.fromId, timestamp };
  }
  if (!message || !["user", "assistant", "toolResult"].includes(message.role)) throw new MediationError("unsupported_history");
  const result: any = { role: message.role };
  if (typeof message.content === "string") result.content = message.content;
  else if (Array.isArray(message.content)) result.content = message.content.map(canonicalPart);
  else throw new MediationError("unsupported_history");
  if (message.role === "toolResult") {
    if (!opaque(message.toolCallId) || !opaque(message.toolName) || !exactKeys(message.details, ["observationQuestion"]) || message.details.observationQuestion !== TOOL_QUESTION) throw new MediationError("unsupported_history");
    result.toolCallId = message.toolCallId; result.toolName = message.toolName; result.details = { observationQuestion: TOOL_QUESTION };
  }
  return result;
}
function alignedEntries(event: any, ctx: any) {
  const entries = ctx.sessionManager.buildContextEntries().flatMap((entry: any) => sessionEntryToContextMessages(entry).map((message: any) => {
    if (!opaque(entry.id, 128)) throw new MediationError("unsupported_history");
    return { entry, canonical: canonicalMessage(message) };
  }));
  if (!Array.isArray(event.messages) || entries.length !== event.messages.length) throw new MediationError("unsupported_history");
  return entries.map((candidate: any, index: number) => {
    const current = canonicalMessage(event.messages[index]);
    if (process.env.PI_FIXTURE_ALIGNMENT_MISMATCH === "1" && index === 0) current.role = "assistant";
    if (process.env.PI_FIXTURE_TOOL_IDENTITY_MISMATCH === "1" && current.role === "toolResult") current.toolCallId = "wrong-tool-call";
    if (process.env.PI_FIXTURE_QUESTION_DETAIL_MISMATCH === "1" && current.role === "toolResult") current.details.observationQuestion = "wrong question";
    if (JSON.stringify(candidate.canonical) !== JSON.stringify(current)) throw new MediationError("unsupported_history");
    return candidate;
  });
}
function questionFor(message: any) {
  if (message.role === "user" && Array.isArray(message.content)) {
    const question = message.content.filter((part: any) => part.type === "text" && typeof part.text === "string").map((part: any) => part.text).join("\n");
    if (!question || bytes(question) > 512) throw new MediationError("question_required");
    return question;
  }
  if (message.role === "toolResult" && message.toolName === "fixture_image" && exactKeys(message.details, ["observationQuestion"]) && message.details.observationQuestion === TOOL_QUESTION) return TOOL_QUESTION;
  throw new MediationError("unsupported_image_source");
}
function sourceFor(headerId: string, entry: any, message: any, partIndex: number) {
  if (!opaque(headerId) || !opaque(entry.id, 128) || !Number.isSafeInteger(partIndex) || partIndex < 0) throw new MediationError("unsupported_image_source");
  const source = `pi:${headerId}:entry:${entry.id}:image:${partIndex}`;
  if (!opaque(source)) throw new MediationError("unsupported_image_source");
  return { source, question: questionFor(message), partIndex };
}
function receiptPath(directory: string) { return path.join(directory, RECEIPT_FILE); }
function pendingPath(directory: string) { return path.join(directory, RECEIPT_PENDING_FILE); }
function syncDirectory(directory: string) { const fd = fs.openSync(directory, "r"); try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); } }
function readReceipts(directory: string) {
  const target = receiptPath(directory);
  // A durable pre-publication fence survives every uncertain replacement phase.
  if (fs.existsSync(pendingPath(directory))) throw new MediationError("budget_state_unavailable");
  if (!fs.existsSync(target)) return [];
  try {
    if (fs.statSync(target).size > MAX_RECEIPT_BYTES) throw new Error();
    const raw = fs.readFileSync(target, "utf8"), receipts = JSON.parse(raw);
    if (bytes(raw) > MAX_RECEIPT_BYTES || !Array.isArray(receipts) || receipts.length > MAX_RECEIPTS) throw new Error();
    return receipts;
  } catch { throw new MediationError("budget_state_unavailable"); }
}
function validReceipt(receipt: any, expected: any) {
  const fields = ["owner_authority_id", "harness_session_id", "logical_turn_id", "source", "entry_id", "part", "observation", "question_digest", "image_digest", "crop", "model", "profile", "reference", "answer"];
  return exactKeys(receipt, fields) && fields.slice(0, -2).every((field) => receipt[field] === expected[field]) && typeof receipt.reference === "string" && typeof receipt.answer === "string" && bytes(receipt.answer) <= MAX_ANSWER_BYTES;
}
function receiptFor(expected: any, reference: string, answer: string) {
  const fields = ["owner_authority_id", "harness_session_id", "logical_turn_id", "source", "entry_id", "part", "observation", "question_digest", "image_digest", "crop", "model", "profile"];
  return { ...Object.fromEntries(fields.map((field) => [field, expected[field]])), reference, answer };
}
function receiptFault(phase: string, requested: string | undefined) { if (requested === phase) throw new Error(`fixture receipt ${phase} failure`); }
function publishReceipt(directory: string, receipt: any, failurePhase?: string) {
  const receipts = readReceipts(directory), prior = receipts.find((entry: any) => entry.source === receipt.source);
  if (prior) { if (!validReceipt(prior, receipt) || prior.reference !== receipt.reference || prior.answer !== receipt.answer) throw new MediationError("budget_state_unavailable"); return; }
  if (receipts.length >= MAX_RECEIPTS) throw new MediationError("result_too_large");
  const next = JSON.stringify([...receipts, receipt]); if (bytes(next) > MAX_RECEIPT_BYTES) throw new MediationError("result_too_large");
  const target = receiptPath(directory), temporary = `${target}.${process.pid}.tmp`, pending = pendingPath(directory);
  try {
    // Do not replace a visible receipt until the crash fence is durable.  If any
    // later step fails, its presence makes a fresh owner refuse the receipt.
    const fence = fs.openSync(pending, "wx", 0o600);
    try { fs.writeFileSync(fence, `${digest(next)}\n`, "utf8"); fs.fsyncSync(fence); } finally { fs.closeSync(fence); }
    receiptFault("pending_directory_sync", failurePhase); syncDirectory(directory);
    const fd = fs.openSync(temporary, "wx", 0o600); try { fs.writeFileSync(fd, next, "utf8"); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
    receiptFault("before_replace", failurePhase); fs.renameSync(temporary, target);
    receiptFault("receipt_directory_sync", failurePhase); syncDirectory(directory);
    receiptFault("pending_remove", failurePhase); fs.unlinkSync(pending);
    receiptFault("pending_remove_directory_sync", failurePhase); syncDirectory(directory);
  } catch {
    throw new MediationError("budget_state_unavailable");
  } finally { try { fs.unlinkSync(temporary); } catch { /* preserve typed refusal */ } }
}

function validFact(fact: any) { return exactKeys(fact, ["kind", "text", "uncertainty", "region_ref"]) && ["visible_text", "visual_fact"].includes(fact.kind) && typeof fact.text === "string" && bytes(fact.text) <= 512 && ["literal_unverified", "unverified_interpretation", "unreadable"].includes(fact.uncertainty) && fact.region_ref === null && (fact.uncertainty !== "unreadable" || fact.text === "") && !rawMedia(fact.text); }
function normalizeInspection(status: any, facts: any, reason: any) {
  if (!Array.isArray(facts) || facts.length > 32 || !facts.every(validFact) || typeof reason !== "string" || bytes(reason) > 256 || rawMedia(reason)) throw new MediationError("invalid_response");
  if (status === "observed" && reason === "") return { inspection_status: "observed", facts, reason: "" };
  if (["inconclusive", "unsupported"].includes(status) && facts.length === 0) return { inspection_status: status, facts: [], reason };
  throw new MediationError("invalid_response");
}
function validateInspection(value: any, expected: any) {
  const base = ["schema", "request_id", "observation_id", "status", "error", "image_ref", "crop", "input_dimensions", "inspected_area", "model_identity", "profile_identity", "capture", "facts", "truncated"];
  if (!exactKeys(value, value?.status === "observed" || value?.status === "error" ? base : [...base, "reason"]) || value.schema !== "observation-inspect/v1" || value.request_id !== expected.request_id || value.observation_id !== expected.observation || value.image_ref !== expected.image_ref || value.crop !== null || JSON.stringify(value.input_dimensions) !== JSON.stringify(expected.input_dimensions) || JSON.stringify(value.inspected_area) !== JSON.stringify({ kind: "whole_image" }) || value.model_identity !== expected.model || value.profile_identity !== expected.profile || JSON.stringify(value.capture) !== JSON.stringify(expected.capture) || value.truncated !== false || !Array.isArray(value.facts) || value.facts.length > 32 || !value.facts.every(validFact)) throw new MediationError("invalid_response");
  if (value.status === "observed" && value.error === null) return normalizeInspection("observed", value.facts, "");
  if (["inconclusive", "unsupported"].includes(value.status) && value.error === null) return normalizeInspection(value.status, value.facts, value.reason);
  throw new MediationError("invalid_response");
}
function primaryEnvelope(expected: any, inspection: any) {
  const envelope = { schema: "observation-mediation/v1", mediation_id: `med-${expected.question_digest.slice(0, 24)}`, source_ref: { source_id: expected.source, image_part: expected.part }, observation_id: expected.observation, inspection_request_id: expected.request_id, question_id: `question-${expected.question_digest.slice(0, 24)}`, status: "inspected", inspection_status: inspection.inspection_status, facts: inspection.facts, reason: inspection.reason, error: null };
  const text = JSON.stringify(envelope); if (bytes(text) > 8192) throw new MediationError("result_too_large"); return { type: "text", text };
}
function receiptInspection(answer: string) { try { const value = JSON.parse(answer); if (!exactKeys(value, ["inspection_status", "facts", "reason"])) throw new Error(); return normalizeInspection(value.inspection_status, value.facts, value.reason); } catch { throw new MediationError("budget_state_unavailable"); } }
function deadline(ctx: any, duration: number) {
  const controller = new AbortController(); let expired = false;
  const onAbort = () => controller.abort(); if (ctx.signal?.aborted) onAbort(); else ctx.signal?.addEventListener("abort", onAbort, { once: true });
  const timer = setTimeout(() => { expired = true; controller.abort(); }, duration);
  return { signal: controller.signal, expired: () => expired, close: () => { clearTimeout(timer); ctx.signal?.removeEventListener("abort", onAbort); } };
}
async function boundedJson(response: Response, signal: AbortSignal, expired: () => boolean) {
  if (!response.body) throw new MediationError("invalid_response");
  const reader = response.body.getReader(), chunks: Uint8Array[] = []; let size = 0;
  try { while (true) { if (signal.aborted) throw new MediationError(expired() ? "deadline_exceeded" : "cancelled"); const part = await reader.read(); if (part.done) break; size += part.value.byteLength; if (size > MAX_RESPONSE_BYTES) throw new MediationError("result_too_large"); chunks.push(part.value); } return JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch (error) { if (error instanceof MediationError) throw error; throw new MediationError(expired() ? "deadline_exceeded" : signal.aborted ? "cancelled" : "invalid_response"); } finally { try { await reader.cancel(); } catch {} }
}
async function inspect(ctx: any, source: any, image: any, expected: any, duration: number) {
  const limited = deadline(ctx, duration);
  const inspection = { schema: "observation-inspect/v1", request_id: expected.request_id, observation_id: expected.observation, status: "observed", error: null, image_ref: expected.image_ref, crop: null, input_dimensions: expected.input_dimensions, inspected_area: { kind: "whole_image" }, model_identity: expected.model, profile_identity: expected.profile, capture: expected.capture, facts: [{ kind: "visual_fact", text: "Fixture visual fact.", uncertainty: "unverified_interpretation", region_ref: null }], truncated: false };
  try {
    const response = await fetch(`${process.env.PI_FIXTURE_VISION_URL}/chat/completions`, { method: "POST", headers: { "content-type": "application/json" }, signal: limited.signal, body: JSON.stringify({ model: expected.model, fixture_mode: process.env.PI_FIXTURE_VISION_MODE || "valid", fixture_inspection: inspection, messages: [{ role: "user", content: [{ type: "text", text: source.question }, { type: "image_url", image_url: { url: `data:${image.mimeType};base64,${image.data}` } }] }] }) });
    if (!response.ok) throw new MediationError("endpoint_unavailable");
    return validateInspection(await boundedJson(response, limited.signal, limited.expired), expected);
  } catch (error) { if (error instanceof MediationError) throw error; throw new MediationError(limited.expired() ? "deadline_exceeded" : limited.signal.aborted ? "cancelled" : "endpoint_unavailable"); } finally { limited.close(); }
}
function activeCompactionId(ctx: any) {
  const compactions = ctx.sessionManager.buildContextEntries().filter((entry: any) => entry.type === "compaction");
  if (compactions.length > 1 || compactions.some((entry: any) => !opaque(entry.id, 128))) throw new MediationError("unsupported_history");
  return compactions[0]?.id;
}

function trustedLauncher(ctx: any) {
  const directory = process.env.PI_FIXTURE_LEDGER_DIR, owner = process.env.PI_FIXTURE_OWNER_ID, turn = process.env.PI_FIXTURE_LOGICAL_TURN, header = ctx.sessionManager.getHeader(), mode = process.env.PI_FIXTURE_LEDGER_MODE;
  if (!directory || !opaque(owner) || !opaque(turn) || !opaque(header?.id) || !["auto", "create", "recover", "reopen"].includes(mode || "")) throw new MediationError("budget_state_unavailable");
  const perCallMs = Number(process.env.PI_FIXTURE_PER_CALL_MS || "30000"), maxAttempts = Number(process.env.PI_FIXTURE_MAX_ATTEMPTS || "32"), mappingDelayMs = Number(process.env.PI_FIXTURE_MAPPING_DELAY_MS || "0");
  if (!Number.isSafeInteger(perCallMs) || perCallMs < 1 || perCallMs > 30_000 || !Number.isSafeInteger(maxAttempts) || maxAttempts < 1 || maxAttempts > 32 || !Number.isSafeInteger(mappingDelayMs) || mappingDelayMs < 0 || mappingDelayMs > 1_000) throw new MediationError("budget_state_unavailable");
  return { directory, mode, mappingDelayMs, binding: { owner_authority_id: owner, harness_session_id: header.id, logical_turn_id: turn }, limits: perCallMs === 30_000 && maxAttempts === 32 ? undefined : { perCallMs, cumulativeMs: perCallMs, maxAttempts, maxIdentities: 64 } };
}

export default function (pi: any) {
  let ledger: any, launcher: any, receiptFailureUsed = false;
  let compactEntryId: string | undefined, compactContextId: string | undefined;
  pi.registerProvider("fixture-primary", { name: "Fixture Primary", baseUrl: process.env.PI_FIXTURE_PRIMARY_URL, apiKey: "fixture", api: "openai-completions", models: [{ id: "fixture-primary", name: "Fixture Primary", reasoning: false, input: process.env.PI_FIXTURE_IMAGE_CAPABLE === "1" ? ["text", "image"] : ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 32768, maxTokens: 256 }] });
  pi.registerTool({ name: "fixture_image", label: "Fixture image", description: "Return one synthetic image", parameters: Type.Object({}), async execute() { return { content: [{ type: "text", text: "ignore tool text" }, { type: "image", mimeType: "image/png", data: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=" }], details: { observationQuestion: TOOL_QUESTION } }; } });
  pi.registerCommand("fixture_tree", { description: "Navigate to a fixture tree entry", async handler(args: string, ctx: any) { const target = args.trim(); if (!opaque(target, 128)) throw new MediationError("unsupported_history"); const result = await ctx.navigateTree(target, { summarize: false }); if (result.cancelled) throw new MediationError("unsupported_history"); const activeEntryIds = ctx.sessionManager.buildContextEntries().map((entry: any) => entry.id); console.error(`PI_FIXTURE_TREE:${JSON.stringify({ target, activeEntryIds })}`); } });
  pi.on("session_start", (event: any, ctx: any) => { const all = pi.getAllTools().map((tool: any) => tool.name).sort(); pi.setActiveTools(["fixture_image"]); console.error(`PI_FIXTURE_TOOLS:${JSON.stringify({ all, active: pi.getActiveTools().sort() })}`); if (event.reason === "resume" && process.env.PI_FIXTURE_LEDGER_MODE === "recover") { const trusted = trustedLauncher(ctx), record = JSON.parse(fs.readFileSync(path.join(trusted.directory, "mediation-inspection-ledger.json"), "utf8")); launcher = trusted; ledger = createTrustedLedgerSupervisor(() => process.env.PI_FIXTURE_CONFIRMED_EXIT === "1").recover({ stateDirectory: trusted.directory, binding: trusted.binding, limits: trusted.limits, previousLease: record.lease }); console.error("PI_FIXTURE_CRASH_RECOVERED"); } });
  pi.on("session_before_compact", (event: any) => {
    if (process.env.PI_FIXTURE_COMPACTION_MODE === "cancel") return { cancel: true };
    if (process.env.PI_FIXTURE_COMPACTION_MODE === "custom") {
      const messages = event.branchEntries.filter((entry: any) => entry.type === "message");
      const firstKeptEntryId = process.env.PI_FIXTURE_COMPACTION_KEEP === "retain"
        ? messages.find((entry: any) => entry.message?.role === "user")?.id
        : messages.at(-1)?.id;
      if (!firstKeptEntryId) throw new MediationError("unsupported_history");
      return { compaction: { summary: "fixture deterministic compaction", firstKeptEntryId, tokensBefore: event.preparation.tokensBefore } };
    }
  });
  pi.on("session_compact", (event: any, ctx: any) => {
    compactEntryId = event.compactionEntry?.id;
    const activeId = activeCompactionId(ctx);
    if (!opaque(compactEntryId, 128) || activeId !== compactEntryId) throw new MediationError("unsupported_history");
    console.error(`PI_FIXTURE_COMPACTION:${JSON.stringify({ id: compactEntryId, activeId, fromExtension: event.fromExtension })}`);
    if (compactContextId) {
      if (compactContextId !== compactEntryId) throw new MediationError("unsupported_history");
      console.error(`PI_FIXTURE_COMPACTION_CONTEXT:${JSON.stringify({ id: compactEntryId, activeId })}`);
      compactEntryId = undefined; compactContextId = undefined;
    }
  });
  pi.on("session_shutdown", async (_event: any, ctx: any) => {
    if (!ledger || !launcher) return;
    await ledger.close();
    fs.writeFileSync(path.join(launcher.directory, "fixture-clean-release.json"), JSON.stringify({ ...launcher.binding, closed: true }), { mode: 0o600 });
    syncDirectory(launcher.directory);
  });
  pi.on("context", async (event: any, ctx: any) => {
    try {
      const active = alignedEntries(event, ctx), trusted = trustedLauncher(ctx);
      if (!launcher) launcher = trusted;
      if (JSON.stringify(launcher) !== JSON.stringify(trusted)) throw new MediationError("budget_state_unavailable");
      const compactions = active.filter((candidate: any) => candidate.entry.type === "compaction");
      const activeId = activeCompactionId(ctx);
      if (compactions.length > 1 || (compactions.length === 1 && compactions[0].entry.id !== activeId) || (compactions.length === 0 && activeId)) throw new MediationError("unsupported_history");
      if (compactions.length === 1) {
        compactContextId = compactions[0].entry.id;
        if (compactEntryId) {
          if (compactContextId !== compactEntryId) throw new MediationError("unsupported_history");
          console.error(`PI_FIXTURE_COMPACTION_CONTEXT:${JSON.stringify({ id: compactEntryId, activeId })}`);
          compactEntryId = undefined; compactContextId = undefined;
        }
      }
      if (!ledger) {
        const options = { stateDirectory: trusted.directory, binding: trusted.binding, limits: trusted.limits };
        if (trusted.mode === "recover") {
          const record = JSON.parse(fs.readFileSync(path.join(trusted.directory, "mediation-inspection-ledger.json"), "utf8"));
          ledger = createTrustedLedgerSupervisor(() => process.env.PI_FIXTURE_CONFIRMED_EXIT === "1").recover({ ...options, previousLease: record.lease });
        } else if (trusted.mode === "reopen" || trusted.mode === "auto" && fs.existsSync(path.join(trusted.directory, "mediation-inspection-ledger.json"))) ledger = reopenMediationLedger(options);
        else ledger = createMediationLedger(options);
      }
      const work: any[] = [];
      for (const [messageIndex, message] of event.messages.entries()) {
        if (!Array.isArray(message.content)) continue;
        const images = message.content.map((part: any, partIndex: number) => ({ part, partIndex })).filter(({ part }: any) => part.type === "image");
        if (images.length > 1) throw new MediationError("question_required");
        for (const { part, partIndex } of images) { if (part.mimeType !== "image/png" || typeof part.data !== "string") throw new MediationError("unsupported_image"); const source = sourceFor(trusted.binding.harness_session_id, active[messageIndex].entry, message, partIndex); work.push({ messageIndex, partIndex, part, source, entry: active[messageIndex].entry }); }
      }
      const reuseRequested = event.messages.some((message: any) => message.role === "user" && (message.content === "reuse retained fixture image" || Array.isArray(message.content) && message.content.some((part: any) => part.type === "text" && part.text === "reuse retained fixture image")));
      if (process.env.PI_FIXTURE_REQUIRE_RETAINED === "1" && reuseRequested && (process.env.PI_FIXTURE_COMPACTION_KEEP === "remove" || work.length === 0)) {
        if (process.env.PI_FIXTURE_COMPACTION_KEEP === "remove" && work.length !== 0) throw new MediationError("unsupported_history");
        throw new MediationError("retained_unavailable");
      }
      const replacements = new Map<string, any>();
      for (const item of work) {
        if (process.env.PI_FIXTURE_GUARD_THROW === "1") throw new Error("fixture injected mediation exception");
        const expected: any = { owner_authority_id: trusted.binding.owner_authority_id, harness_session_id: trusted.binding.harness_session_id, logical_turn_id: trusted.binding.logical_turn_id, source: item.source.source, entry_id: item.entry.id, part: item.partIndex, observation: `obs-${digest(item.source.source).slice(0, 24)}`, question_digest: digest(item.source.question), image_digest: digest(item.part.data), crop: "whole-image", model: "fixture-vision", profile: "fixture-profile-v1", request_id: `inspect-${digest(item.part.data).slice(0, 24)}`, image_ref: `image-${digest(item.part.data).slice(0, 24)}`, input_dimensions: { width: 1, height: 1 }, capture: { navigation_epoch: 1, dom_epoch: 4, viewport_epoch: 1 } };
        const identity = { observation_id: expected.observation, source_entry_id: expected.entry_id, source_part: `image-${item.partIndex}`, question_digest: expected.question_digest, crop_id: expected.crop, model_id: expected.model, profile_id: expected.profile };
        const reservation = ledger.begin(identity, { followUp: process.env.PI_FIXTURE_FOLLOW_UP === "1" }), reservedAt = performance.now();
        let inspection: any;
        if (reservation.status === "completed") {
          const receipt = readReceipts(trusted.directory).find((value: any) => validReceipt(value, expected) && value.reference === reservation.reference);
          if (!receipt) throw new MediationError("budget_state_unavailable"); inspection = receiptInspection(receipt.answer);
        } else {
          if (reservation.status !== "reserved") throw new MediationError("budget_state_unavailable");
          try {
            const pauseAfterReserveMs = Number(process.env.PI_FIXTURE_PAUSE_AFTER_RESERVE_MS || "0");
            if (!Number.isSafeInteger(pauseAfterReserveMs) || pauseAfterReserveMs < 0 || pauseAfterReserveMs > 1_000) throw new MediationError("budget_state_unavailable");
            if (pauseAfterReserveMs) await wait(pauseAfterReserveMs);
            if (process.env.PI_FIXTURE_APPEND_THROW === "1") throw new Error();
            if (trusted.mappingDelayMs) await new Promise((resolve) => setTimeout(resolve, trusted.mappingDelayMs));
            pi.appendEntry("anvil-observation-mediation/v1", { source_id: expected.source, observation_id: expected.observation, entry_id: expected.entry_id, image_part: item.partIndex, question_digest: expected.question_digest, image_digest: expected.image_digest, crop_id: expected.crop, model_id: expected.model, profile_id: expected.profile });
            console.error(`PI_IMAGE_MEDIATION_BINDING:${JSON.stringify({ source: expected.source })}`);
            if (process.env.PI_FIXTURE_CANCEL_BEFORE_VISION === "1") ctx.abort();
            const remainingMs = reservation.deadline_ms - Math.ceil(performance.now() - reservedAt);
            if (remainingMs <= 0) throw new MediationError("deadline_exceeded");
            inspection = await inspect(ctx, item.source, item.part, expected, remainingMs);
          } catch (error) { ledger.finish(reservation.operation, { outcome: error instanceof MediationError && error.code === "cancelled" ? "cancelled" : "failed" }); throw error; }
          const finished = ledger.finish(reservation.operation, { outcome: "success" });
          try { const phase = !receiptFailureUsed ? process.env.PI_FIXTURE_RECEIPT_FAIL_PHASE : undefined; receiptFailureUsed ||= Boolean(phase); publishReceipt(trusted.directory, receiptFor(expected, finished.reference, JSON.stringify(inspection)), phase); } catch (error) { throw error; }
        }
        replacements.set(`${item.messageIndex}:${item.partIndex}`, primaryEnvelope(expected, inspection));
      }
      return { messages: event.messages.map((message: any, messageIndex: number) => !Array.isArray(message.content) ? message : { ...message, content: message.content.map((part: any, partIndex: number) => replacements.get(`${messageIndex}:${partIndex}`) ?? part) }) };
    } catch (error) { if (error instanceof MediationError) abort(ctx, error.code); if (error instanceof LedgerError) abort(ctx, error.code === "budget_exhausted" ? "budget_exhausted" : "budget_state_unavailable"); console.error(`PI_FIXTURE_GUARD_EXCEPTION:${error instanceof Error ? error.message : String(error)}`); abort(ctx, "guard_exception"); }
  });
  pi.on("before_provider_request", (event: any, ctx: any) => { try { if (process.env.PI_FIXTURE_INJECT_RAW_PROVIDER === "1") event.payload = { ...event.payload, fixture: { nested: { type: "image" } } }; if (rawMedia(event.payload)) abort(ctx, "raw_media_guard"); return event.payload; } catch (error) { if (error instanceof MediationError) throw error; abort(ctx, "guard_exception"); } });
}
