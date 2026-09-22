import { spawn } from "node:child_process";
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
  return Object.freeze({ enabled: true, origin: config.origin, fields: Object.freeze([...config.fields]), executable: config.executable || "anvil", cwd: config.cwd || process.cwd(), timeout, runner: config.runner || null });
}

export async function evaluateJev(policy, projection, signal) {
  if (!policy.enabled) return { outcome: "disabled" };
  const encoded = JSON.stringify(projection);
  if (byteLength(encoded) > 32 * 1024) return { outcome: "projection_too_large" };
  let directory;
  try {
    directory = await mkdtemp(join(tmpdir(), "anvil-jev-"));
    await chmod(directory, 0o700);
    const input = join(directory, "projection.json");
    await writeFile(input, encoded, { encoding: "utf8", mode: 0o600, flag: "wx" });
    const result = policy.runner
      ? await policy.runner({ executable: policy.executable, args: argumentsFor(input), cwd: policy.cwd, signal })
      : await run(policy.executable, argumentsFor(input), policy.cwd, policy.timeout, signal);
    if (signal?.aborted || result?.cancelled) return { outcome: "cancelled" };
    if (result?.timeout) return { outcome: "timeout" };
    if (!result || typeof result.stdout !== "string" || byteLength(result.stdout) > outputLimit) return { outcome: "malformed_response" };
    if (result.overflow || result.code !== 0) return { outcome: "provider_unavailable" };
    return selectionFrom(result.stdout, projection.entities.map((entity) => entity.id));
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
    let child, done = false, timer, stdout = "", stderr = "", overflow = false;
    const finish = (result) => { if (!done) { done = true; clearTimeout(timer); signal?.removeEventListener("abort", cancel); resolve(result); } };
    const cancel = () => { child?.kill(); finish({ cancelled: true }); };
    try {
      child = spawn(executable, args, { cwd, shell: false, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
      const collect = (name) => (chunk) => { if (overflow) return; const text = chunk.toString("utf8"); if (byteLength(stdout) + byteLength(stderr) + byteLength(text) > outputLimit) { overflow = true; child.kill(); } else if (name === "stdout") stdout += text; else stderr += text; };
      child.stdout.on("data", collect("stdout")); child.stderr.on("data", collect("stderr"));
      child.on("error", () => finish({ code: null, stdout, overflow }));
      child.on("close", (code) => finish({ code, stdout, overflow }));
      timer = setTimeout(() => { child.kill(); finish({ timeout: true }); }, timeout);
      if (signal?.aborted) cancel(); else signal?.addEventListener("abort", cancel, { once: true });
    } catch { finish({ code: null, stdout, overflow }); }
  });
}

function selectionFrom(text, offered) {
  let envelope;
  try { envelope = parseUniqueJson(text); } catch { return { outcome: "malformed_response" }; }
  if (!plain(envelope) || Object.keys(envelope).length !== 3 || envelope.ok !== true || envelope.command !== "jev evaluate" || !plain(envelope.data)) return { outcome: "malformed_response" };
  const annotation = envelope.data;
  if (annotation.schema !== "anvil.jev.annotation.v1" || annotation.capability !== "browser_element_resolution" || annotation.status !== "completed" || annotation.used !== true || !plain(annotation.answers) || Object.keys(annotation.answers).length !== 1 || !plain(annotation.answers.selection) || annotation.answers.selection.type !== "choice" || typeof annotation.answers.selection.choice !== "string") return { outcome: "malformed_response" };
  const choice = annotation.answers.selection.choice;
  if (![...offered, "NO_MATCH_IN_CANDIDATES", "AMBIGUOUS", "NEEDS_VISUAL_EVIDENCE"].includes(choice)) return { outcome: "unknown_selection" };
  return { outcome: "selection", selection: choice };
}

const plain = (value) => value && typeof value === "object" && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;

function parseUniqueJson(input) {
  let index = 0;
  const whitespace = () => { while (/\s/.test(input[index] || "")) index += 1; };
  const string = () => { const start = index; if (input[index++] !== '"') throw Error(); while (index < input.length) { const char = input[index++]; if (char === '"') return JSON.parse(input.slice(start, index)); if (char === "\\") index += 1; if (char < " ") throw Error(); } throw Error(); };
  const value = () => { whitespace(); if (input[index] === '"') return string(); if (input[index] === "{") { index += 1; const object = {}; whitespace(); if (input[index] === "}") { index += 1; return object; } while (true) { whitespace(); const key = string(); whitespace(); if (input[index++] !== ":" || Object.hasOwn(object, key)) throw Error(); object[key] = value(); whitespace(); if (input[index] === "}") { index += 1; return object; } if (input[index++] !== ",") throw Error(); } } if (input[index] === "[") { index += 1; const array = []; whitespace(); if (input[index] === "]") { index += 1; return array; } while (true) { array.push(value()); whitespace(); if (input[index] === "]") { index += 1; return array; } if (input[index++] !== ",") throw Error(); } } const match = /^(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/.exec(input.slice(index)); if (!match) throw Error(); index += match[0].length; return JSON.parse(match[0]); };
  const result = value(); whitespace(); if (index !== input.length) throw Error(); return result;
}
