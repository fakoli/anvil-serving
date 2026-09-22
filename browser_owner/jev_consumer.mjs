import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const projectionFields = ["schema", "request_id", "observation_id", "source", "target", "scope", "coverage", "entities"];
const outputLimit = 64 * 1024;
const byteLength = (value) => Buffer.byteLength(value, "utf8");

export class JevConsumerError extends Error { constructor(code) { super(code); this.code = code; } }
const fail = (code) => { throw new JevConsumerError(code); };

export function createJevConsumer(config = {}, documentOrigins) {
  if (!config || typeof config !== "object" || Array.isArray(config) || Object.getPrototypeOf(config) !== Object.prototype) fail("invalid_jev_policy");
  const names = new Set(["enabled", "origin", "fields", "executable", "cwd", "timeout", "runner"]);
  if (Object.keys(config).some((name) => !names.has(name)) || typeof config.enabled !== "boolean") fail("invalid_jev_policy");
  if (!config.enabled) return Object.freeze({ enabled: false });
  if (typeof config.origin !== "string" || !documentOrigins.has(config.origin) || !Array.isArray(config.fields) || config.fields.length !== projectionFields.length || config.fields.some((field, index) => field !== projectionFields[index])) fail("invalid_jev_policy");
  const timeout = config.timeout ?? 5_000;
  if (!Number.isInteger(timeout) || timeout <= 0 || timeout > 10_000 || (config.executable !== undefined && (typeof config.executable !== "string" || !config.executable)) || (config.cwd !== undefined && (typeof config.cwd !== "string" || !config.cwd)) || (config.runner !== undefined && typeof config.runner !== "function")) fail("invalid_jev_policy");
  if (!config.runner && process.platform === "win32") fail("jev_subprocess_unsupported");
  return Object.freeze({ enabled: true, origin: config.origin, fields: Object.freeze([...config.fields]), executable: config.executable || "anvil", cwd: config.cwd || process.cwd(), timeout, runner: config.runner || null });
}

export async function evaluateJev(policy, projection, signal) {
  if (!policy.enabled) return { outcome: "disabled" };
  const encoded = JSON.stringify(projection), inputDigest = digest(projection);
  if (byteLength(encoded) > 32 * 1024) return { outcome: "projection_too_large" };
  let directory;
  try {
    directory = await mkdtemp(join(tmpdir(), "anvil-jev-"));
    await chmod(directory, 0o700);
    const input = join(directory, "projection.json");
    await writeFile(input, encoded, { encoding: "utf8", mode: 0o600, flag: "wx" });
    if (signal?.aborted) return { outcome: "cancelled" };
    const result = policy.runner
      ? await policy.runner({ executable: policy.executable, args: argumentsFor(input), cwd: policy.cwd, signal })
      : await run(policy.executable, argumentsFor(input), policy.cwd, policy.timeout, signal);
    if (signal?.aborted || result?.cancelled) return { outcome: "cancelled" };
    if (result?.timeout) return { outcome: "timeout" };
    if (!result || typeof result.stdout !== "string" || byteLength(result.stdout) > outputLimit) return { outcome: "malformed_response" };
    if (result.overflow || result.code !== 0) return { outcome: "provider_unavailable" };
    return selectionFrom(result.stdout, projection.entities.map((entity) => entity.id), inputDigest);
  } catch (error) {
    if (error instanceof JevConsumerError) throw error;
    return { outcome: signal?.aborted ? "cancelled" : "provider_unavailable" };
  } finally {
    if (directory) await rm(directory, { recursive: true, force: true }).catch(() => {});
  }
}

function argumentsFor(input) { return ["jev", "evaluate", "browser_element_resolution", "--input", input, "--allow-export", "--json"]; }

function run(executable, args, cwd, timeout, signal) {
  return new Promise((resolve) => {
    let child, done = false, closed = false, escalated = false, code, timer, grace, cleanup, reason, stdout = "", stderr = "", overflow = false;
    const finish = (result) => { if (!done) { done = true; clearTimeout(timer); clearTimeout(grace); clearTimeout(cleanup); signal?.removeEventListener("abort", cancel); resolve(result); } };
    const signalChild = (name) => {
      if (!child?.pid) return;
      try { if (process.platform !== "win32") process.kill(-child.pid, name); else child.kill(name); } catch { child.kill(name); }
    };
    const groupAlive = () => {
      if (process.platform === "win32" || !child?.pid) return false;
      try { process.kill(-child.pid, 0); return true; } catch (error) { return error?.code !== "ESRCH"; }
    };
    const stopped = () => {
      if (!closed || done) return;
      if (groupAlive()) { cleanup = setTimeout(stopped, 10); return; }
      finish({ code, stdout, overflow, timeout: reason === "timeout", cancelled: reason === "cancelled" });
    };
    const stop = (next) => { if (reason) return; reason = next; signalChild("SIGTERM"); grace = setTimeout(() => { escalated = true; signalChild("SIGKILL"); stopped(); }, 100); };
    const cancel = () => stop("cancelled");
    try {
      child = spawn(executable, args, { cwd, shell: false, detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
      const collect = (name) => (chunk) => { if (overflow) return; const text = chunk.toString("utf8"); if (byteLength(stdout) + byteLength(stderr) + byteLength(text) > outputLimit) { overflow = true; stop("overflow"); } else if (name === "stdout") stdout += text; else stderr += text; };
      child.stdout.on("data", collect("stdout")); child.stderr.on("data", collect("stderr"));
      child.on("error", () => { closed = true; code = null; if (reason) stopped(); else finish({ code, stdout, overflow }); });
      child.on("close", (result) => { closed = true; code = result; if (reason) { if (escalated) stopped(); } else finish({ code, stdout, overflow }); });
      timer = setTimeout(() => stop("timeout"), timeout);
      if (signal?.aborted) cancel(); else signal?.addEventListener("abort", cancel, { once: true });
    } catch { finish({ code: null, stdout, overflow }); }
  });
}

function selectionFrom(text, offered, inputDigest) {
  let envelope;
  try { envelope = parseUniqueJson(text); } catch { return { outcome: "malformed_response" }; }
  if (!plain(envelope) || Object.keys(envelope).length !== 3 || envelope.ok !== true || envelope.command !== "jev evaluate" || !plain(envelope.data)) return { outcome: "malformed_response" };
  const annotation = envelope.data;
  const annotationFields = ["schema", "provider", "model", "capability", "status", "reason", "requested", "used", "request_started", "elapsed_ms", "rubric_digest", "input_digest", "answers", "usage"];
  if (!closed(annotation, annotationFields) || annotation.schema !== "anvil.jev.annotation.v1" || annotation.capability !== "browser_element_resolution" || annotation.status !== "completed" || annotation.reason !== "validated" || annotation.requested !== true || annotation.used !== true || annotation.request_started !== true || !Number.isInteger(annotation.elapsed_ms) || annotation.elapsed_ms < 0 || typeof annotation.provider !== "string" || typeof annotation.model !== "string" || typeof annotation.rubric_digest !== "string" || annotation.input_digest !== inputDigest || !plain(annotation.answers) || !closed(annotation.usage, ["input_tokens", "output_tokens"]) || !Number.isInteger(annotation.usage.input_tokens) || annotation.usage.input_tokens < 0 || !Number.isInteger(annotation.usage.output_tokens) || annotation.usage.output_tokens < 0 || !closed(annotation.answers, ["selection"]) || !plain(annotation.answers.selection)) return { outcome: "malformed_response" };
  const choiceAnswer = annotation.answers.selection, choices = [...offered, "NO_MATCH_IN_CANDIDATES", "AMBIGUOUS", "NEEDS_VISUAL_EVIDENCE"];
  if (!closed(choiceAnswer, ["type", "probabilities", "confidence", "choice"]) || choiceAnswer.type !== "choice" || typeof choiceAnswer.choice !== "string" || !plain(choiceAnswer.probabilities) || !closed(choiceAnswer.probabilities, choices) || !Number.isFinite(choiceAnswer.confidence) || choiceAnswer.confidence < 0 || choiceAnswer.confidence > 1 || !choices.every((choice) => Number.isFinite(choiceAnswer.probabilities[choice]) && choiceAnswer.probabilities[choice] >= 0 && choiceAnswer.probabilities[choice] <= 1) || Math.abs(Object.values(choiceAnswer.probabilities).reduce((sum, value) => sum + value, 0) - 1) > 0.02) return { outcome: "malformed_response" };
  const choice = choiceAnswer.choice;
  if (!choices.includes(choice)) return { outcome: "unknown_selection" };
  return { outcome: "selection", selection: choice };
}

const plain = (value) => value && typeof value === "object" && !Array.isArray(value) && [Object.prototype, null].includes(Object.getPrototypeOf(value));
const closed = (value, keys) => plain(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));

function digest(value) {
  return createHash("sha256").update(canonicalJson(value), "utf8").digest("hex");
}

function canonicalJson(value) {
  if (value === null || typeof value === "boolean") return String(value);
  if (typeof value === "string") { if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(value)) fail("invalid_projection"); return JSON.stringify(value); }
  if (typeof value === "number") { if (!Number.isFinite(value)) fail("invalid_projection"); return JSON.stringify(value); }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (!plain(value)) fail("invalid_projection");
  return `{${Object.keys(value).sort().map((key) => `${canonicalJson(key)}:${canonicalJson(value[key])}`).join(",")}}`;
}

function parseUniqueJson(input) {
  let index = 0;
  const whitespace = () => { while ([" ", "\n", "\r", "\t"].includes(input[index])) index += 1; };
  const string = () => { const start = index; if (input[index++] !== '"') throw Error(); while (index < input.length) { const char = input[index++]; if (char === '"') return JSON.parse(input.slice(start, index)); if (char === "\\") index += 1; if (char < " ") throw Error(); } throw Error(); };
  const value = () => { whitespace(); if (input[index] === '"') return string(); if (input[index] === "{") { index += 1; const object = Object.create(null), seen = new Set(); whitespace(); if (input[index] === "}") { index += 1; return object; } while (true) { whitespace(); const key = string(); whitespace(); if (input[index++] !== ":" || seen.has(key)) throw Error(); seen.add(key); Object.defineProperty(object, key, { value: value(), enumerable: true, writable: true, configurable: true }); whitespace(); if (input[index] === "}") { index += 1; return object; } if (input[index++] !== ",") throw Error(); } } if (input[index] === "[") { index += 1; const array = []; whitespace(); if (input[index] === "]") { index += 1; return array; } while (true) { array.push(value()); whitespace(); if (input[index] === "]") { index += 1; return array; } if (input[index++] !== ",") throw Error(); } } const match = /^(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/.exec(input.slice(index)); if (!match) throw Error(); index += match[0].length; return JSON.parse(match[0]); };
  const result = value(); whitespace(); if (index !== input.length) throw Error(); return result;
}
