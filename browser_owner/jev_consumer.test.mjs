import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import test from "node:test";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createJevConsumer, evaluateJev, JevConsumerError } from "./jev_consumer.mjs";

const origin = "http://127.0.0.1:9000";
const fields = ["schema", "request_id", "observation_id", "source", "target", "scope", "coverage", "entities"];
const projection = { schema: "browser-element-resolution-projection/v1", request_id: "request", observation_id: "observation", source: "dom", target: { description: "capture", qualifiers: [] }, scope: { kind: "document", root: "document" }, coverage: { state: "complete", reason: null }, entities: [{ id: "e-1", role: "button", text: "Capture", nearby: "", state: { exists: true, in_viewport: true, occluded: false, enabled: true }, predicate_reasons: { exists: null, in_viewport: null, occluded: null, enabled: null } }] };
const encode = (value) => value === null || typeof value === "boolean" ? String(value) : typeof value === "string" || typeof value === "number" ? JSON.stringify(value) : Array.isArray(value) ? `[${value.map(encode).join(",")}]` : `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${encode(value[key])}`).join(",")}}`;
const digest = (value) => createHash("sha256").update(encode(value), "utf8").digest("hex");
const envelope = (choice, extra = {}, inputDigest = digest(projection), offered = ["e-1"]) => {
  const choices = [...offered, "NO_MATCH_IN_CANDIDATES", "AMBIGUOUS", "NEEDS_VISUAL_EVIDENCE"];
  return JSON.stringify({ ok: true, command: "jev evaluate", data: { schema: "anvil.jev.annotation.v1", provider: "typesafe", model: "jev-1.13.0", capability: "browser_element_resolution", status: "completed", reason: "validated", requested: true, used: true, request_started: true, elapsed_ms: 1, rubric_digest: "a", input_digest: inputDigest, answers: { selection: { type: "choice", probabilities: Object.fromEntries(choices.map((item) => [item, item === choice ? 1 : 0])), confidence: 1, choice, ...extra } }, usage: { input_tokens: 1, output_tokens: 1 } } });
};

test("consumer requires a trusted immutable origin and exact field list", () => {
  assert.deepEqual(createJevConsumer({ enabled: false }, new Set([origin])), { enabled: false });
  for (const config of [{ enabled: true, origin, fields: [...fields].reverse() }, { enabled: true, origin: "http://127.0.0.1:9001", fields }, { enabled: true, origin, fields, executable: "" }]) assert.throws(() => createJevConsumer(config, new Set([origin])), (error) => error instanceof JevConsumerError && error.code === "invalid_jev_policy");
});

test("consumer accepts only the completed used selection envelope", async () => {
  const policy = createJevConsumer({ enabled: true, origin, fields, runner: async () => ({ code: 0, stdout: envelope("e-1") }) }, new Set([origin]));
  assert.deepEqual(await evaluateJev(policy, projection), { outcome: "selection", selection: "e-1" });
  const malformed = createJevConsumer({ enabled: true, origin, fields, runner: async () => ({ code: 0, stdout: '{"ok":true,"ok":true}' }) }, new Set([origin]));
  assert.deepEqual(await evaluateJev(malformed, projection), { outcome: "malformed_response" });
  for (const stdout of [envelope("e-1", { action: "click" }), envelope("e-1").replace("{", '{"__proto__":1,"__proto__":2,'), `\u00a0${envelope("e-1")}`]) {
    const rejected = createJevConsumer({ enabled: true, origin, fields, runner: async () => ({ code: 0, stdout }) }, new Set([origin]));
    assert.deepEqual(await evaluateJev(rejected, projection), { outcome: "malformed_response" });
  }
});

test("consumer binds Anvil's non-ASCII canonical state digest", async () => {
  const vector = { schema: "browser-element-resolution-projection/v1", request_id: "réquest", observation_id: "observation", source: "dom", target: { description: "capture", qualifiers: [] }, scope: { kind: "document", root: "document" }, coverage: { state: "complete", reason: null }, entities: [] };
  const vectorDigest = "8d470df9c0aa266634f4520bf32aedb7233363d2ae69c53c545c978cfbc926b1";
  const policy = createJevConsumer({ enabled: true, origin, fields, runner: async () => ({ code: 0, stdout: envelope("NO_MATCH_IN_CANDIDATES", {}, vectorDigest, []) }) }, new Set([origin]));
  assert.deepEqual(await evaluateJev(policy, vector), { outcome: "selection", selection: "NO_MATCH_IN_CANDIDATES" });
  const stale = { ...vector, request_id: "other" };
  assert.deepEqual(await evaluateJev(policy, stale), { outcome: "malformed_response" });
});

test("consumer refuses real subprocesses on Windows but retains disabled and injected runners", () => {
  const descriptor = Object.getOwnPropertyDescriptor(process, "platform");
  Object.defineProperty(process, "platform", { ...descriptor, value: "win32" });
  try {
    assert.deepEqual(createJevConsumer({ enabled: false }, new Set([origin])), { enabled: false });
    assert.doesNotThrow(() => createJevConsumer({ enabled: true, origin, fields, runner: async () => ({}) }, new Set([origin])));
    assert.throws(() => createJevConsumer({ enabled: true, origin, fields }, new Set([origin])), (error) => error instanceof JevConsumerError && error.code === "jev_subprocess_unsupported");
  } finally { Object.defineProperty(process, "platform", descriptor); }
});

test("consumer waits for SIGTERM-resistant children before cleanup", { timeout: 10_000, skip: process.platform === "win32" }, async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "anvil-jev-consumer-")); t.after(() => rm(directory, { recursive: true, force: true }));
  const executable = join(directory, "anvil-child");
  await writeFile(executable, `#!${process.execPath}\nconst fs=require('node:fs');const input=process.argv[6];fs.writeFileSync('child.json',JSON.stringify({pid:process.pid,input,exists:fs.existsSync(input)}));process.on('SIGTERM',()=>fs.writeFileSync('term','seen'));if(process.argv.includes('overflow'))process.stdout.write('x'.repeat(70000));setInterval(()=>{},1000);`); await chmod(executable, 0o700);
  const direct = (timeout, executablePath = executable) => createJevConsumer({ enabled: true, origin, fields, executable: executablePath, cwd: directory, timeout }, new Set([origin]));
  const inspect = async () => { const child = JSON.parse(await readFile(join(directory, "child.json"), "utf8")); assert.equal(child.exists, true); assert.equal(existsSync(child.input), false); assert.throws(() => process.kill(child.pid, 0), { code: "ESRCH" }); assert.equal(await readFile(join(directory, "term"), "utf8"), "seen"); };
  assert.deepEqual(await evaluateJev(direct(500), projection), { outcome: "timeout" }); await inspect();
  await rm(join(directory, "child.json")); await rm(join(directory, "term")); const controller = new AbortController(); const cancelling = evaluateJev(direct(1_000), projection, controller.signal); while (!existsSync(join(directory, "child.json"))) await new Promise((resolve) => setTimeout(resolve, 5)); controller.abort(); assert.deepEqual(await cancelling, { outcome: "cancelled" }); await inspect();
  const overflow = join(directory, "anvil-overflow"); await writeFile(overflow, `#!${process.execPath}\nconst fs=require('node:fs');const input=process.argv[6];fs.writeFileSync('child.json',JSON.stringify({pid:process.pid,input,exists:fs.existsSync(input)}));process.on('SIGTERM',()=>fs.writeFileSync('term','seen'));process.stdout.write('x'.repeat(70000));setInterval(()=>{},1000);`); await chmod(overflow, 0o700);
  await rm(join(directory, "child.json")); await rm(join(directory, "term")); assert.deepEqual(await evaluateJev(direct(1_000, overflow), projection), { outcome: "provider_unavailable" }); await inspect();
});

test("consumer waits for an exited leader's SIGTERM-resistant descendant", { timeout: 10_000, skip: process.platform === "win32" }, async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "anvil-jev-group-")); t.after(() => rm(directory, { recursive: true, force: true }));
  const executable = join(directory, "anvil-parent"), child = join(directory, "child.json"), late = join(directory, "late");
  const descendant = `const fs=require('node:fs');const input=process.argv[1];fs.writeFileSync(${JSON.stringify(child)},JSON.stringify({pid:process.pid,input,exists:fs.existsSync(input)}));process.on('SIGTERM',()=>fs.writeFileSync(${JSON.stringify(join(directory, "term"))},'seen'));setTimeout(()=>fs.writeFileSync(${JSON.stringify(late)},'late'),1500);setInterval(()=>{},1000);`;
  await writeFile(executable, `#!${process.execPath}\nconst {spawn}=require('node:child_process');spawn(process.execPath,['-e',${JSON.stringify(descendant)},process.argv[6]],{stdio:'ignore'});setInterval(()=>{},1000);`); await chmod(executable, 0o700);
  const direct = (timeout) => createJevConsumer({ enabled: true, origin, fields, executable, cwd: directory, timeout }, new Set([origin]));
  const inspect = async () => { const retained = JSON.parse(await readFile(child, "utf8")); assert.equal(retained.exists, true); assert.equal(existsSync(retained.input), false); assert.throws(() => process.kill(retained.pid, 0), { code: "ESRCH" }); assert.equal(await readFile(join(directory, "term"), "utf8"), "seen"); assert.equal(existsSync(late), false); };
  assert.deepEqual(await evaluateJev(direct(1_000), projection), { outcome: "timeout" }); await new Promise((resolve) => setTimeout(resolve, 1_600)); await inspect();
  await rm(child); await rm(join(directory, "term")); const controller = new AbortController(); const cancelling = evaluateJev(direct(1_000), projection, controller.signal); while (!existsSync(child)) await new Promise((resolve) => setTimeout(resolve, 5)); controller.abort(); assert.deepEqual(await cancelling, { outcome: "cancelled" }); await new Promise((resolve) => setTimeout(resolve, 1_600)); await inspect();
});
