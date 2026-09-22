import { createHash } from "node:crypto";
import * as fs from "node:fs";
import path from "node:path";
import { Type } from "typebox";
import { createMediationLedger, LedgerError, reopenMediationLedger } from "../../../browser_owner/mediation_ledger.mjs";

const TOOL_QUESTION = "What is in the fixture tool image?";
const RECEIPT_FILE = "fixture-observation-receipts.json";
const MAX_RECEIPTS = 8;
const MAX_RECEIPT_BYTES = 32 * 1024;
const MAX_ANSWER_BYTES = 512;

class MediationError extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}

function bytes(value: string) { return Buffer.byteLength(value, "utf8"); }
function digest(value: string) { return createHash("sha256").update(value).digest("hex"); }
function opaque(value: unknown, limit = 64): value is string {
  return typeof value === "string" && bytes(value) > 0 && bytes(value) <= limit && /^[A-Za-z0-9._:-]+$/.test(value);
}

function rawMedia(value: any): boolean {
  if (Array.isArray(value)) return value.some(rawMedia);
  if (!value || typeof value !== "object") return false;
  if (value.type === "image" || value.type === "image_url") return true;
  if (typeof value.url === "string" && value.url.startsWith("data:image/")) return true;
  return Object.values(value).some(rawMedia);
}

function abort(ctx: any, code: string): never {
  console.error(`PI_IMAGE_MEDIATION_ERROR:${code}`);
  ctx.abort();
  throw new MediationError(code);
}

function canonicalPart(part: any) {
  if (part?.type === "text" && typeof part.text === "string") return { type: "text", text: part.text };
  if (part?.type === "image" && typeof part.mimeType === "string" && typeof part.data === "string") {
    return { type: "image", mimeType: part.mimeType, data: part.data };
  }
  if (part?.type === "toolCall" && opaque(part.id) && opaque(part.name)
      && part.arguments && typeof part.arguments === "object" && !Array.isArray(part.arguments)) {
    return { type: "toolCall", id: part.id, name: part.name, arguments: part.arguments };
  }
  throw new MediationError("unsupported_history");
}

function canonicalMessage(message: any) {
  if (!message || !["user", "assistant", "toolResult"].includes(message.role)) throw new MediationError("unsupported_history");
  if (typeof message.content === "string") return { role: message.role, content: message.content };
  if (!Array.isArray(message.content)) throw new MediationError("unsupported_history");
  const result: any = { role: message.role, content: message.content.map(canonicalPart) };
  if (message.role === "toolResult") {
    if (!opaque(message.toolCallId) || !opaque(message.toolName)) throw new MediationError("unsupported_history");
    result.toolCallId = message.toolCallId;
    result.toolName = message.toolName;
  }
  return result;
}

function alignedEntries(event: any, ctx: any) {
  const entries = ctx.sessionManager.buildContextEntries()
    .filter((entry: any) => entry?.type === "message")
    .map((entry: any) => {
      if (!opaque(entry.id, 128)) throw new MediationError("unsupported_history");
      return { entry, canonical: canonicalMessage(entry.message ?? entry) };
    });
  if (!Array.isArray(event.messages) || entries.length !== event.messages.length) throw new MediationError("unsupported_history");
  return entries.map((entry: any, index: number) => {
    const current = canonicalMessage(event.messages[index]);
    if (JSON.stringify(entry.canonical) !== JSON.stringify(current)) throw new MediationError("unsupported_history");
    return entry.entry;
  });
}

function questionFor(message: any) {
  if (message.role === "user") {
    const question = message.content.filter((part: any) => part.type === "text" && typeof part.text === "string").map((part: any) => part.text).join("\n");
    if (!question || bytes(question) > 512) throw new MediationError("question_required");
    return question;
  }
  if (message.role === "toolResult" && message.toolName === "fixture_image" && message.details?.observationQuestion === TOOL_QUESTION) return TOOL_QUESTION;
  throw new MediationError("unsupported_image_source");
}

function sourceFor(headerId: string, entry: any, message: any, partIndex: number) {
  if (!opaque(headerId, 64) || !opaque(entry.id, 128) || !Number.isSafeInteger(partIndex) || partIndex < 0) throw new MediationError("unsupported_image_source");
  const source = `pi:${headerId}:entry:${entry.id}:image:${partIndex}`;
  if (!opaque(source)) throw new MediationError("unsupported_image_source");
  return { source, question: questionFor(message), partIndex };
}

function receiptPath(directory: string) { return path.join(directory, RECEIPT_FILE); }

function readReceipts(directory: string) {
  const target = receiptPath(directory);
  if (!fs.existsSync(target)) return [];
  try {
    const raw = fs.readFileSync(target, "utf8");
    const receipts = JSON.parse(raw);
    if (bytes(raw) > MAX_RECEIPT_BYTES || !Array.isArray(receipts) || receipts.length > MAX_RECEIPTS) throw new Error();
    return receipts;
  } catch { throw new MediationError("budget_state_unavailable"); }
}

function validReceipt(receipt: any, expected: any) {
  const fields = ["source", "observation", "question_digest", "image_digest", "crop", "model", "profile", "reference", "answer"];
  return Boolean(receipt) && typeof receipt === "object" && Object.keys(receipt).length === fields.length && fields.every((field) => field in receipt)
    && receipt.source === expected.source && receipt.observation === expected.observation && receipt.question_digest === expected.questionDigest
    && receipt.image_digest === expected.imageDigest && receipt.crop === expected.crop && receipt.model === expected.model
    && receipt.profile === expected.profile && typeof receipt.reference === "string" && typeof receipt.answer === "string" && bytes(receipt.answer) <= MAX_ANSWER_BYTES;
}

function publishReceipt(directory: string, receipt: any) {
  const receipts = readReceipts(directory);
  const prior = receipts.find((entry: any) => entry.source === receipt.source);
  if (prior) {
    if (!validReceipt(prior, receipt) || prior.reference !== receipt.reference || prior.answer !== receipt.answer) throw new MediationError("budget_state_unavailable");
    return;
  }
  if (receipts.length >= MAX_RECEIPTS) throw new MediationError("result_too_large");
  const next = JSON.stringify([...receipts, receipt]);
  if (bytes(next) > MAX_RECEIPT_BYTES) throw new MediationError("result_too_large");
  const target = receiptPath(directory);
  const temporary = `${target}.${process.pid}.tmp`;
  let replaced = false;
  try {
    const descriptor = fs.openSync(temporary, "wx", 0o600);
    try { fs.writeFileSync(descriptor, next, "utf8"); fs.fsyncSync(descriptor); } finally { fs.closeSync(descriptor); }
    fs.renameSync(temporary, target);
    replaced = true;
    const directoryDescriptor = fs.openSync(directory, "r");
    try { fs.fsyncSync(directoryDescriptor); } finally { fs.closeSync(directoryDescriptor); }
  } catch { throw new MediationError(replaced ? "budget_state_unavailable" : "result_too_large");
  } finally { try { fs.unlinkSync(temporary); } catch { /* preserve typed refusal */ } }
}

function primaryEnvelope(expected: any, answer: string) {
  const envelope = {
    schema: "observation-mediation/v1", mediation_id: `med-${expected.questionDigest.slice(0, 24)}`,
    source_ref: { source_id: expected.source, image_part: expected.partIndex }, observation_id: expected.observation,
    inspection_request_id: `inspect-${expected.imageDigest.slice(0, 24)}`, question_id: `question-${expected.questionDigest.slice(0, 24)}`,
    status: "inspected", inspection_status: "observed", facts: [{ kind: "visual_fact", text: answer, uncertainty: "unverified_interpretation", region_ref: null }], reason: "", error: null,
  };
  const text = JSON.stringify(envelope);
  if (bytes(text) > 8192) throw new MediationError("result_too_large");
  return { type: "text", text };
}

function trustedLauncher(ctx: any) {
  const directory = process.env.PI_FIXTURE_LEDGER_DIR;
  const owner = process.env.PI_FIXTURE_OWNER_ID;
  const turn = process.env.PI_FIXTURE_LOGICAL_TURN;
  const header = ctx.sessionManager.getHeader();
  const mode = process.env.PI_FIXTURE_LEDGER_MODE;
  if (!directory || !opaque(owner) || !opaque(turn) || !opaque(header?.id) || !["create", "reopen"].includes(mode || "")) {
    throw new MediationError("budget_state_unavailable");
  }
  return { directory, mode, binding: { owner_authority_id: owner, harness_session_id: header.id, logical_turn_id: turn } };
}

export default function (pi: any) {
  pi.registerProvider("fixture-primary", {
    name: "Fixture Primary", baseUrl: process.env.PI_FIXTURE_PRIMARY_URL, apiKey: "fixture", api: "openai-completions",
    models: [{ id: "fixture-primary", name: "Fixture Primary", reasoning: false, input: process.env.PI_FIXTURE_IMAGE_CAPABLE === "1" ? ["text", "image"] : ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 4096, maxTokens: 256 }],
  });
  pi.registerTool({ name: "fixture_image", label: "Fixture image", description: "Return one synthetic image", parameters: Type.Object({}), async execute() {
    return { content: [{ type: "text", text: "ignore tool text" }, { type: "image", mimeType: "image/png", data: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=" }], details: { observationQuestion: TOOL_QUESTION } };
  } });
  pi.on("session_start", () => {
    const all = pi.getAllTools().map((tool: any) => tool.name).sort();
    pi.setActiveTools(["fixture_image"]);
    console.error(`PI_FIXTURE_TOOLS:${JSON.stringify({ all, active: pi.getActiveTools().sort() })}`);
  });

  let ledger: any;
  let ledgerDirectory = "";
  pi.on("context", async (event: any, ctx: any) => {
    try {
      const active = alignedEntries(event, ctx);
      const trusted = trustedLauncher(ctx);
      if (!ledger) {
        const options = { stateDirectory: trusted.directory, binding: trusted.binding };
        ledger = trusted.mode === "create" ? createMediationLedger(options) : reopenMediationLedger(options);
        ledgerDirectory = trusted.directory;
      }
      if (ledgerDirectory !== trusted.directory) throw new MediationError("budget_state_unavailable");
      const messages = [];
      for (const [messageIndex, message] of event.messages.entries()) {
        const entry = active[messageIndex];
        if (!Array.isArray(message.content)) { messages.push(message); continue; }
        const content = [];
        for (const [partIndex, part] of message.content.entries()) {
          if (part.type !== "image") { content.push(part); continue; }
          if (part.mimeType !== "image/png" || typeof part.data !== "string") throw new MediationError("unsupported_image");
          if (process.env.PI_FIXTURE_GUARD_THROW === "1") throw new Error("fixture injected mediation exception");
          const source = sourceFor(trusted.binding.harness_session_id, entry, message, partIndex);
          const expected = { source: source.source, partIndex, observation: `obs-${digest(source.source).slice(0, 24)}`, questionDigest: digest(source.question), imageDigest: digest(part.data), crop: "whole-image", model: "fixture-vision", profile: "fixture-profile-v1" };
          const identity = { observation_id: expected.observation, source_entry_id: entry.id, source_part: `image-${partIndex}`, question_digest: expected.questionDigest, crop_id: expected.crop, model_id: expected.model, profile_id: expected.profile };
          const reservation = ledger.begin(identity);
          let answer: string;
          if (reservation.status === "completed") {
            const receipt = readReceipts(ledgerDirectory).find((item: any) => validReceipt(item, expected) && item.reference === reservation.reference);
            if (!receipt) throw new MediationError("budget_state_unavailable");
            answer = receipt.answer;
          } else {
            if (reservation.status !== "reserved") throw new MediationError("budget_state_unavailable");
            pi.appendEntry("anvil-observation-mediation/v1", { source_id: expected.source, observation_id: expected.observation, entry_id: entry.id, image_part: partIndex, question_digest: expected.questionDigest, image_digest: expected.imageDigest, crop_id: expected.crop, model_id: expected.model, profile_id: expected.profile });
            console.error(`PI_IMAGE_MEDIATION_BINDING:${JSON.stringify({ source: expected.source, question: source.question })}`);
            let response;
            try { response = await fetch(`${process.env.PI_FIXTURE_VISION_URL}/chat/completions`, { method: "POST", headers: { "content-type": "application/json" }, signal: ctx.signal, body: JSON.stringify({ model: "fixture-vision", messages: [{ role: "user", content: [{ type: "text", text: source.question }, { type: "image_url", image_url: { url: `data:${part.mimeType};base64,${part.data}` } }] }] }) });
            } catch { ledger.finish(reservation.operation, { outcome: "failed" }); throw new MediationError("endpoint_unavailable"); }
            if (!response.ok) { ledger.finish(reservation.operation, { outcome: "failed" }); throw new MediationError("endpoint_unavailable"); }
            let payload;
            try { payload = await response.json();
            } catch { ledger.finish(reservation.operation, { outcome: "failed" }); throw new MediationError("invalid_response"); }
            answer = payload.choices?.[0]?.message?.content;
            if (typeof answer !== "string" || bytes(answer) > MAX_ANSWER_BYTES) { ledger.finish(reservation.operation, { outcome: "failed" }); throw new MediationError("invalid_response"); }
            const finished = ledger.finish(reservation.operation, { outcome: "success" });
            publishReceipt(ledgerDirectory, { source: expected.source, observation: expected.observation, question_digest: expected.questionDigest, image_digest: expected.imageDigest, crop: expected.crop, model: expected.model, profile: expected.profile, reference: finished.reference, answer });
          }
          content.push(primaryEnvelope(expected, answer));
        }
        messages.push({ ...message, content });
      }
      return { messages };
    } catch (error) {
      if (error instanceof MediationError) abort(ctx, error.code);
      if (error instanceof LedgerError) abort(ctx, error.code === "budget_exhausted" ? "budget_exhausted" : "budget_state_unavailable");
      abort(ctx, "guard_exception");
    }
  });
  pi.on("before_provider_request", (event: any, ctx: any) => {
    try {
      if (process.env.PI_FIXTURE_INJECT_RAW_PROVIDER === "1") event.payload = { ...event.payload, fixture: { nested: { type: "image" } } };
      if (rawMedia(event.payload)) abort(ctx, "raw_media_guard");
      return event.payload;
    } catch (error) {
      if (error instanceof MediationError) throw error;
      abort(ctx, "guard_exception");
    }
  });
}
