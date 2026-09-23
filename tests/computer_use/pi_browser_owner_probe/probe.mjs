import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:http";
import { readFileSync, realpathSync } from "node:fs";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { createFixtureObservationAdapter } from "../../../browser_owner/observation_adapter.mjs";

if (!process.argv.includes("--image-mode")) {
  for (const mode of ["0", "1"]) {
    const result = spawnSync(process.execPath, [process.argv[1], "--image-mode"], { cwd: process.cwd(), env: { ...process.env, PI_BROWSER_IMAGE_CAPABLE: mode }, encoding: "utf8", timeout: 20_000, killSignal: "SIGKILL" });
    assert.equal(result.error?.code, undefined, `image mode ${mode} timed out or failed to start: ${result.error?.message ?? "unknown error"}`);
    assert.equal(result.signal, null, `image mode ${mode} was terminated by ${result.signal}`);
    assert.equal(result.status, 0, result.stderr);
  }
  process.stdout.write("pi browser owner dispatch: ok (image modes 0,1)\n");
  process.exit(0);
}
const pi = process.env.PI_BINARY || "pi";
const version = spawnSync(pi, ["--version"], { encoding: "utf8" });
assert.equal(version.status, 0, version.stderr);
assert.equal(version.stdout.trim(), "0.85.1");
const selectedPi = pi.includes("/") ? pi : spawnSync("which", [pi], { encoding: "utf8" }).stdout.trim();
assert.ok(selectedPi, "Pi executable was not resolved");
const packageIdentity = JSON.parse(readFileSync(join(dirname(realpathSync(selectedPi)), "package.json"), "utf8"));
assert.deepEqual({ name: packageIdentity.name, version: packageIdentity.version }, { name: "@earendil-works/pi-coding-agent", version: "0.85.1" });
const home = await mkdtemp(join(tmpdir(), "pi-browser-owner-"));
const previewRoot = join(home, "preview-root"), previewViewer = join(home, "preview-viewer.mjs"), previewProof = join(home, "preview-proof.json"), neutralPreviewRoot = join(home, "neutral-preview-root"), neutralPreviewProof = join(home, "neutral-preview-proof.json");
await writeFile(previewViewer, `#!${process.execPath}\nimport { createHash } from "node:crypto";import { lstat,readFile,writeFile } from "node:fs/promises";const path=process.argv.at(-1),[image,stat]=await Promise.all([readFile(path),lstat(path)]);await writeFile(process.env.PI_BROWSER_FIXTURE_PREVIEW_PROOF,JSON.stringify({digest:createHash("sha256").update(image).digest("hex"),mode:stat.mode&0o777}));`); await chmod(previewViewer, 0o700);
const priorPreviewProof = process.env.PI_BROWSER_FIXTURE_PREVIEW_PROOF; process.env.PI_BROWSER_FIXTURE_PREVIEW_PROOF = neutralPreviewProof;
const neutralPreview = await createFixtureObservationAdapter({ piSessionId: "neutral-preview-session", preview: { viewer: [previewViewer], runtimeRoot: neutralPreviewRoot } });
try { await neutralPreview.execute({ operation: "capture" }); await neutralPreview.execute({ operation: "preview" }); } finally { await neutralPreview.close(); if (priorPreviewProof === undefined) delete process.env.PI_BROWSER_FIXTURE_PREVIEW_PROOF; else process.env.PI_BROWSER_FIXTURE_PREVIEW_PROOF = priorPreviewProof; }
const expectedPreviewDigest = JSON.parse(await readFile(neutralPreviewProof, "utf8")).digest;
const providerCaptures = [];
let sessionFile;
let providerSecondSnapshot;
const rawMedia = (value) => Array.isArray(value) ? value.some(rawMedia) : !value || typeof value !== "object" ? typeof value === "string" && /^data:image\//i.test(value) : value.type === "image" || value.type === "image_url" || typeof value.url === "string" && value.url.startsWith("data:image/") || Object.values(value).some(rawMedia);
const provider = createServer(async (request, response) => {
  const chunks = []; request.on("data", (chunk) => chunks.push(chunk)); request.on("end", async () => {
    const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8")); providerCaptures.push(parsed);
    if (providerCaptures.length === 2 && sessionFile) {
      const entries = (await readFile(sessionFile, "utf8")).trim().split("\n").map(JSON.parse);
      providerSecondSnapshot = entries.find((entry) => entry.type === "message" && entry.message?.role === "toolResult" && entry.message?.toolName === "browser_capture")?.message;
    }
    const messages = JSON.stringify(parsed.messages), tool = messages.includes('"role":"tool"'), negative = messages.includes("[browser-negative]");
    const toolCall = (!tool || negative && !messages.includes("browser-resolve-call")) && { index: 0, id: negative ? "browser-resolve-call" : "browser-capture-call", type: "function", function: { name: negative ? "browser_resolve" : "browser_capture", arguments: negative ? '{"observation_id":"00000000-0000-0000-0000-000000000000","entity_id":"e-1"}' : "{}" } };
    response.writeHead(200, { "content-type": "text/event-stream" });
    response.write(`data: ${JSON.stringify({ id: "fixture", object: "chat.completion.chunk", choices: [{ index: 0, delta: toolCall ? { role: "assistant", tool_calls: [toolCall] } : { role: "assistant", content: "fixture" }, finish_reason: null }] })}\n\n`);
    response.write(`data: ${JSON.stringify({ id: "fixture", object: "chat.completion.chunk", choices: [{ index: 0, delta: {}, finish_reason: toolCall ? "tool_calls" : "stop" }] })}\n\n`); response.end("data: [DONE]\n\n");
  });
});
await new Promise((resolve, reject) => provider.once("error", reject).listen(0, "127.0.0.1", resolve));
const child = spawn(pi, ["--mode", "rpc", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-context-files", "--extension", "./tests/computer_use/pi_browser_owner_probe/extension.ts", "--provider", "fixture-browser", "--model", "fixture-browser", "--session-dir", join(home, "sessions")], { cwd: process.cwd(), env: { PATH: process.env.PATH || "", HOME: home, PI_CODING_AGENT_DIR: join(home, "agent"), PI_CODING_AGENT_SESSION_DIR: join(home, "sessions"), PI_OFFLINE: "1", PI_BROWSER_FIXTURE_PROVIDER_URL: `http://127.0.0.1:${provider.address().port}/v1`, PI_BROWSER_IMAGE_CAPABLE: process.env.PI_BROWSER_IMAGE_CAPABLE || "1", PI_BROWSER_FIXTURE_PREVIEW_VIEWER: previewViewer, PI_BROWSER_FIXTURE_PREVIEW_ROOT: previewRoot, PI_BROWSER_FIXTURE_PREVIEW_PROOF: previewProof, PI_BROWSER_FIXTURE_REWRITE: process.env.PI_BROWSER_FIXTURE_REWRITE || "", CHROMIUM_EXECUTABLE: process.env.CHROMIUM_EXECUTABLE || "/usr/bin/google-chrome" }, stdio: ["pipe", "pipe", "pipe"] });
let stdout = "", stderr = "";
child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8"); child.stdout.on("data", (part) => { stdout += part; }); child.stderr.on("data", (part) => { stderr += part; });
const wait = async (match) => { const end = Date.now() + 12_000; while (!match()) { if (Date.now() > end) throw new Error(`timeout: ${stderr}`); await new Promise((resolve) => setTimeout(resolve, 20)); } };
const records = () => stdout.split("\n").filter(Boolean).map(JSON.parse);
const rpc = async (id, type, extra = {}) => { child.stdin.write(`${JSON.stringify({ id, type, ...extra })}\n`); await wait(() => records().some((event) => event.id === id && event.type === "response")); const response = records().find((event) => event.id === id && event.type === "response"); assert.equal(response.success, true, JSON.stringify(response)); return response; };
const logged = (name) => [...stderr.matchAll(new RegExp(`${name}:(\\{.*\\})`, "g"))].map((match) => JSON.parse(match[1]));
try {
  await once(child, "spawn");
  await wait(() => stderr.includes("PI_BROWSER_OWNER_READY:"));
  const ready = logged("PI_BROWSER_OWNER_READY")[0];
  assert.deepEqual(ready.active, ["browser_capture", "browser_release", "browser_resolve"]);
  const state = await rpc("state", "get_state");
  assert.equal(state?.data?.sessionId, ready.session_id);
  sessionFile = state.data.sessionFile;
  await rpc("agent-capture", "prompt", { message: "[browser-dispatch]" });
  await wait(() => records().some((event) => event.type === "agent_end") && providerCaptures.length === 2);
  assert.equal(providerCaptures.length, 2, "capture dispatch did not make exactly two provider requests");
  assert.equal(rawMedia(providerCaptures[0]) || rawMedia(providerCaptures[1]), false, "provider payload received media");
  const events = records();
  const captureCallIndex = events.findIndex((event) => event.type === "message_end" && event.message?.role === "assistant" && event.message?.content?.some((part) => part.type === "toolCall" && part.name === "browser_capture"));
  const captureStartIndex = events.findIndex((event) => event.type === "tool_execution_start" && event.toolName === "browser_capture");
  const captureResultIndex = events.findIndex((event) => event.type === "message_end" && event.message?.role === "toolResult" && event.message?.toolName === "browser_capture");
  const captureEndIndex = events.findIndex((event) => event.type === "tool_execution_end" && event.toolName === "browser_capture");
  const captureCall = events[captureCallIndex], captureStart = events[captureStartIndex], captureEnd = events[captureEndIndex], captureResult = events[captureResultIndex];
  assert.ok(captureCall && captureStart && captureEnd && captureResult, "Pi did not persist browser capture call/result events");
  assert.ok(captureCallIndex < captureStartIndex && captureStartIndex < captureEndIndex && captureEndIndex < captureResultIndex, "Pi capture RPC event order changed");
  assert.equal(captureResult.message.content.length, 1, "capture result must have exactly one content part");
  assert.equal(captureResult.message.content[0].type, "text", "capture result must be text-only");
  assert.ok(Buffer.byteLength(captureResult.message.content[0].text, "utf8") <= 8192, "capture receipt exceeds the adapter bound");
  const receipt = JSON.parse(captureResult.message.content[0].text);
  assert.equal(receipt.schema, "browser-owner-adapter/v1"); assert.equal(receipt.status, "ok"); assert.match(receipt.result.observation_id, /^[0-9a-f-]{36}$/);
  const captureCallId = captureCall.message.content.find((part) => part.type === "toolCall" && part.name === "browser_capture").id;
  assert.equal(captureStart.toolCallId, captureCallId, "capture start must bind the assistant tool call");
  assert.equal(captureEnd.toolCallId, captureCallId, "capture end must bind the assistant tool call");
  assert.equal(captureResult.message.toolCallId, captureCallId, "capture result must bind the assistant tool call");
  assert.equal(receipt.binding.pi_session_id, ready.session_id, "actual-agent receipt must bind the active Pi session");
  assert.match(receipt.binding.owner_session_id, /^[0-9a-f-]{36}$/, "actual-agent receipt must carry an owner session binding");
  assert.equal(providerSecondSnapshot?.toolCallId, captureCallId, "request #2 was not preceded by the persisted capture result");
  assert.equal(providerSecondSnapshot?.content?.length, 1); assert.equal(providerSecondSnapshot.content[0].type, "text"); assert.equal(providerSecondSnapshot.content[0].text, captureResult.message.content[0].text);
  const captureWire = providerCaptures[1].messages.find((message) => message.role === "tool" && message.tool_call_id === captureCallId);
  assert.ok(captureWire, "request #2 omitted the capture tool result"); assert.equal(captureWire.content, captureResult.message.content[0].text, "request #2 receipt was rewritten");
  const persisted = (await readFile(state.data.sessionFile, "utf8")).trim().split("\n").map(JSON.parse);
  const persistedResultIndex = persisted.findIndex((entry) => entry.type === "message" && entry.message?.role === "toolResult" && entry.message?.toolName === "browser_capture");
  const persistedResult = persisted[persistedResultIndex];
  assert.equal(persistedResult?.message?.content?.[0]?.text, captureResult.message.content[0].text, "Pi did not persist the exact bounded capture receipt");
  assert.deepEqual(JSON.parse(persistedResult.message.content[0].text).binding, receipt.binding, "persisted capture binding changed");
  assert.deepEqual(JSON.parse(captureWire.content).binding, receipt.binding, "wire capture binding changed");
  const hookResultIndex = persisted.findIndex((entry) => entry.type === "custom" && entry.customType === "anvil-browser-dispatch/v1" && entry.data?.hook === "tool_result" && entry.data.tool_call_id === captureCallId);
  const hookEndIndex = persisted.findIndex((entry) => entry.type === "custom" && entry.customType === "anvil-browser-dispatch/v1" && entry.data?.hook === "tool_execution_end" && entry.data.tool_call_id === captureCallId);
  const nextContextIndex = persisted.findIndex((entry, index) => index > persistedResultIndex && entry.type === "custom" && entry.customType === "anvil-browser-dispatch/v1" && entry.data?.hook === "context");
  const nextProviderIndex = persisted.findIndex((entry, index) => index > nextContextIndex && entry.type === "custom" && entry.customType === "anvil-browser-dispatch/v1" && entry.data?.hook === "before_provider_request");
  assert.ok(hookResultIndex < hookEndIndex && hookEndIndex < persistedResultIndex && persistedResultIndex < nextContextIndex && nextContextIndex < nextProviderIndex, "merged JSONL capture/provider order changed");
  await rpc("preview-current", "prompt", { message: "/browser_fixture_preview" });
  await wait(() => logged("PI_BROWSER_OWNER_PREVIEW").length === 1);
  const preview = logged("PI_BROWSER_OWNER_PREVIEW")[0]; assert.deepEqual(preview, { schema: "browser-owner-adapter/v1", status: "ok", operation: "preview", binding: receipt.binding, result: { status: "shown" } });
  const previewAttestation = JSON.parse(await readFile(previewProof, "utf8")); assert.equal(previewAttestation.digest, expectedPreviewDigest, "viewer did not receive the fixture screenshot"); assert.equal(previewAttestation.mode, 0o600);
  const previewHistory = await readFile(state.data.sessionFile, "utf8"); assert.doesNotMatch(previewHistory, /iVBOR|data:image|private_path|file:/); assert.equal(previewHistory.includes(previewRoot), false, "preview root leaked to Pi history");
  assert.equal(providerCaptures.length, 2, "human preview made a provider request"); assert.doesNotMatch(JSON.stringify(preview), /iVBOR|data:image|file:|private_path/); assert.equal((stdout + stderr).includes(previewRoot), false, "preview root leaked to Pi logs");
  await rpc("agent-negative", "prompt", { message: "[browser-negative]" });
  await wait(() => events.length < records().length && providerCaptures.length === 4);
  const negativeCall = records().find((event) => event.type === "message_end" && event.message?.role === "assistant" && event.message?.content?.some((part) => part.type === "toolCall" && part.name === "browser_resolve"));
  const negativeResult = records().find((event) => event.type === "message_end" && event.message?.role === "toolResult" && event.message?.toolName === "browser_resolve");
  const negativeCallId = negativeCall.message.content.find((part) => part.type === "toolCall" && part.name === "browser_resolve").id;
  assert.equal(negativeResult.message.toolCallId, negativeCallId, "refusal must bind the assistant tool call");
  assert.equal(negativeResult.message.content.length, 1, "refusal must have exactly one content part");
  assert.equal(negativeResult.message.content[0].type, "text", "refusal must be text-only");
  assert.ok(Buffer.byteLength(negativeResult.message.content[0].text, "utf8") <= 8192, "refusal exceeds the adapter bound");
  assert.deepEqual(JSON.parse(negativeResult.message.content[0].text), { schema: "browser-owner-adapter/v1", status: "refused", code: "unknown_observation" });
  const negativeWire = providerCaptures[3].messages.find((message) => message.role === "tool" && message.tool_call_id === negativeCallId);
  assert.ok(negativeWire); assert.equal(negativeWire.content, negativeResult.message.content[0].text, "request #4 refusal was rewritten");
  const hooks = (await readFile(state.data.sessionFile, "utf8")).trim().split("\n").map(JSON.parse).filter((entry) => entry.type === "custom" && entry.customType === "anvil-browser-dispatch/v1").map((entry) => entry.data);
  assert.deepEqual(hooks.map((entry) => entry.hook), ["context", "before_provider_request", "tool_result", "tool_execution_end", "context", "before_provider_request", "context", "before_provider_request", "tool_result", "tool_execution_end", "context", "before_provider_request"]);
  assert.deepEqual(hooks.map((entry) => entry.sequence), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
  assert.deepEqual(hooks.filter((entry) => ["tool_result", "tool_execution_end"].includes(entry.hook)).map((entry) => ({ hook: entry.hook, tool_call_id: entry.tool_call_id, tool_name: entry.tool_name })), [{ hook: "tool_result", tool_call_id: captureCallId, tool_name: "browser_capture" }, { hook: "tool_execution_end", tool_call_id: captureCallId, tool_name: "browser_capture" }, { hook: "tool_result", tool_call_id: negativeCallId, tool_name: "browser_resolve" }, { hook: "tool_execution_end", tool_call_id: negativeCallId, tool_name: "browser_resolve" }]);
  assert.equal(
    providerCaptures[1].messages.filter((message) => message.role === "tool" && message.tool_call_id === captureCallId).length,
    1,
    "request #2 must contain exactly one capture tool result",
  );
  assert.equal(
    providerCaptures[3].messages.filter((message) => message.role === "tool" && message.tool_call_id === negativeCallId).length,
    1,
    "request #4 must contain exactly one resolve tool result",
  );
  assert.doesNotMatch(captureWire.content, /iVBOR|data:image|private_path|\/(?:data|home)\//);
  assert.doesNotMatch(negativeWire.content, /iVBOR|data:image|private_path|\/(?:data|home)\//);
  await rpc("proof", "prompt", { message: "/browser_fixture_proof" });
  await wait(() => stderr.includes("PI_BROWSER_OWNER_CALLBACKS:"));
  const callbacks = logged("PI_BROWSER_OWNER_CALLBACKS")[0];
  assert.equal(callbacks.capture.status, "ok");
  assert.equal(callbacks.capture.binding.pi_session_id, ready.session_id);
  assert.equal(callbacks.capture.hasImage, false);
  assert.deepEqual(callbacks.resolve, { status: "ok", enabled: false });
  assert.equal(callbacks.release, "ok");
  assert.equal(callbacks.stale, "unknown_observation");
  assert.equal(callbacks.cancelled, "cancelled");
  assert.equal(callbacks.widened, "invalid_request");
  await rpc("preview-released", "prompt", { message: "/browser_fixture_preview" }); await wait(() => logged("PI_BROWSER_OWNER_PREVIEW").length === 2);
  assert.deepEqual(logged("PI_BROWSER_OWNER_PREVIEW")[1], { schema: "browser-owner-adapter/v1", status: "refused", code: "unknown_observation" }); assert.equal(providerCaptures.length, 4, "refused preview made a provider request");
  const neutral = await createFixtureObservationAdapter({ piSessionId: "neutral-session" });
  try {
    const neutralCapture = await neutral.execute({ operation: "capture" }), neutralDisabled = neutralCapture.result.entities.find((entity) => entity.text === "Disabled capture");
    const neutralResolve = await neutral.execute({ operation: "resolve", args: { observation_id: neutralCapture.result.observation_id, entity_id: neutralDisabled.id } });
    const neutralRelease = await neutral.execute({ operation: "release", args: { observation_id: neutralCapture.result.observation_id } });
    const neutralStale = await neutral.execute({ operation: "resolve", args: { observation_id: neutralCapture.result.observation_id, entity_id: neutralDisabled.id } });
    const cancelledController = new AbortController(); cancelledController.abort(); const neutralCancelled = await neutral.execute({ operation: "capture" }, { signal: cancelledController.signal });
    assert.deepEqual(callbacks.capture.entities, neutralCapture.result.entities.map((entity) => ({ role: entity.role, text: entity.text, enabled: entity.enabled })));
    assert.equal(neutralResolve.result.enabled, callbacks.resolve.enabled); assert.equal(neutralRelease.status, callbacks.release); assert.equal(neutralStale.code, callbacks.stale); assert.equal(neutralCancelled.code, callbacks.cancelled);
  } finally { await neutral.close(); }
  await rpc("live-start", "prompt", { message: "/browser_fixture_proof live" });
  await wait(() => logged("PI_BROWSER_OWNER_CALLBACKS").length === 2);
  const liveStart = logged("PI_BROWSER_OWNER_CALLBACKS")[1]; assert.equal(liveStart.live, true);
  await rpc("new", "new_session");
  await wait(() => logged("PI_BROWSER_OWNER_READY").length === 2 && logged("PI_BROWSER_OWNER_CLOSED").some((entry) => entry.reason === "new"));
  const newReady = logged("PI_BROWSER_OWNER_READY")[1], newState = await rpc("state-new", "get_state");
  assert.equal(newReady.reason, "new"); assert.notEqual(newReady.session_id, ready.session_id); assert.equal(newState.data.sessionId, newReady.session_id);
  await rpc("preview-new", "prompt", { message: "/browser_fixture_preview" }); await wait(() => logged("PI_BROWSER_OWNER_PREVIEW").length === 3); assert.equal(logged("PI_BROWSER_OWNER_PREVIEW")[2].code, "unknown_observation");
  await rpc("proof-new", "prompt", { message: `/browser_fixture_proof ${liveStart.capture.observation_id}` });
  await wait(() => logged("PI_BROWSER_OWNER_CALLBACKS").length === 3);
  const afterNew = logged("PI_BROWSER_OWNER_CALLBACKS")[2];
  assert.equal(afterNew.prior, "unknown_observation"); assert.equal(afterNew.capture.binding.pi_session_id, newReady.session_id); assert.notEqual(afterNew.capture.binding.owner_session_id, liveStart.capture.binding.owner_session_id);
  await rpc("live-new", "prompt", { message: "/browser_fixture_proof live" });
  await wait(() => logged("PI_BROWSER_OWNER_CALLBACKS").length === 4);
  const liveNew = logged("PI_BROWSER_OWNER_CALLBACKS")[3]; assert.equal(liveNew.live, true);
  await rpc("seed", "prompt", { message: "synthetic fork seed" });
  await wait(() => records().some((event) => event.type === "agent_end"));
  await rpc("clone", "clone");
  await wait(() => logged("PI_BROWSER_OWNER_READY").length === 3 && logged("PI_BROWSER_OWNER_CLOSED").some((entry) => entry.reason === "fork"));
  const forkReady = logged("PI_BROWSER_OWNER_READY")[2], forkState = await rpc("state-fork", "get_state");
  assert.equal(forkReady.reason, "fork"); assert.notEqual(forkReady.session_id, newReady.session_id); assert.equal(forkState.data.sessionId, forkReady.session_id);
  await rpc("preview-fork", "prompt", { message: "/browser_fixture_preview" }); await wait(() => logged("PI_BROWSER_OWNER_PREVIEW").length === 4); assert.equal(logged("PI_BROWSER_OWNER_PREVIEW")[3].code, "unknown_observation");
  await rpc("proof-fork", "prompt", { message: `/browser_fixture_proof ${liveNew.capture.observation_id}` });
  await wait(() => logged("PI_BROWSER_OWNER_CALLBACKS").length === 5);
  const afterFork = logged("PI_BROWSER_OWNER_CALLBACKS")[4];
  assert.equal(afterFork.prior, "unknown_observation"); assert.equal(afterFork.capture.binding.pi_session_id, forkReady.session_id); assert.notEqual(afterFork.capture.binding.owner_session_id, liveNew.capture.binding.owner_session_id);
  await rpc("live-fork", "prompt", { message: "/browser_fixture_proof live" });
  await wait(() => logged("PI_BROWSER_OWNER_CALLBACKS").length === 6);
  const liveFork = logged("PI_BROWSER_OWNER_CALLBACKS")[5]; assert.equal(liveFork.live, true);
  await rpc("reload", "prompt", { message: "/browser_fixture_reload" });
  await wait(() => logged("PI_BROWSER_OWNER_READY").length === 4 && logged("PI_BROWSER_OWNER_CLOSED").some((entry) => entry.reason === "reload"));
  const reloadReady = logged("PI_BROWSER_OWNER_READY")[3]; assert.equal(reloadReady.reason, "reload"); assert.equal(reloadReady.session_id, forkReady.session_id);
  await rpc("preview-reload", "prompt", { message: "/browser_fixture_preview" }); await wait(() => logged("PI_BROWSER_OWNER_PREVIEW").length === 5); assert.equal(logged("PI_BROWSER_OWNER_PREVIEW")[4].code, "unknown_observation");
  await rpc("proof-reload", "prompt", { message: `/browser_fixture_proof ${liveFork.capture.observation_id}` });
  await wait(() => logged("PI_BROWSER_OWNER_CALLBACKS").length === 7);
  const afterReload = logged("PI_BROWSER_OWNER_CALLBACKS")[6];
  assert.equal(afterReload.prior, "unknown_observation"); assert.equal(afterReload.capture.binding.pi_session_id, reloadReady.session_id); assert.notEqual(afterReload.capture.binding.owner_session_id, liveFork.capture.binding.owner_session_id);
  const lifecycle = [...stderr.matchAll(/PI_BROWSER_OWNER_(CLOSED|READY):(\{[^\n]+\})/g)].map((match) => ({ kind: match[1], data: JSON.parse(match[2]), index: match.index }));
  for (const reason of ["new", "fork", "reload"]) {
    const closed = lifecycle.find((event) => event.kind === "CLOSED" && event.data.reason === reason), started = lifecycle.find((event) => event.kind === "READY" && event.data.reason === reason);
    assert.ok(closed && started && closed.index < started.index, `${reason} closed after start`);
  }
  child.stdin.end();
  await once(child, "close");
  assert.equal(child.exitCode, 0, stderr);
  const finalLifecycle = [...stderr.matchAll(/PI_BROWSER_OWNER_(CLOSED|READY):(\{[^\n]+\})/g)].map((match) => ({ kind: match[1], data: JSON.parse(match[2]), index: match.index }));
  const quit = finalLifecycle.filter((event) => event.kind === "CLOSED" && event.data.reason === "quit").at(-1), finalStart = finalLifecycle.filter((event) => event.kind === "READY").at(-1);
  assert.ok(quit && finalStart && quit.index > finalStart.index, "final quit did not close the current owner");
  assert.match(state.data.sessionFile, /\.jsonl$/);
  assert.doesNotMatch(stdout + stderr, /iVBOR|data:image\//);
  console.log(JSON.stringify({ status: "passed", package: packageIdentity.name, version: packageIdentity.version, ready, callbacks, lifecycle: { new: newReady.session_id, fork: forkReady.session_id, reload: reloadReady.session_id }, closed: true, limitation: "Synthetic loopback agent dispatch and provider ordering are proven. TUI rendering, human preview, vision mediation, injected-media guard, and real or cloud inference remain unproven." }));
} finally {
  if (child.exitCode === null && !child.signalCode) { child.kill("SIGTERM"); await once(child, "close").catch(() => {}); }
  await new Promise((resolve) => provider.close(resolve));
  await rm(home, { recursive: true, force: true });
}
