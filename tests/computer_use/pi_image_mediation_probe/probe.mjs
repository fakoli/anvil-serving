import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { mkdtemp, mkdir, readdir, readFile, rm, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { startFakePrimary } from "./fake-primary.mjs";
import { startFakeVision } from "./fake-vision.mjs";
import fixture from "./fixtures.json" with { type: "json" };

const pi = process.env.PI_BINARY || "pi";
const TIMEOUT_MS = 7_000;
const forceWatchdogHang = process.argv.includes("--force-watchdog-hang");
const PROBE_TIMEOUT_MS = forceWatchdogHang ? 1_000 : 45_000;
const children = new Set();
const temporaryDirs = new Set();

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitFor(predicate, label) {
  const started = Date.now();
  while (!predicate()) {
    if (Date.now() - started >= TIMEOUT_MS) throw new Error(`${label} timed out`);
    await delay(10);
  }
}

async function createTemporaryDir(prefix) {
  const directory = await mkdtemp(join(tmpdir(), prefix));
  temporaryDirs.add(directory);
  return directory;
}

async function removeTemporaryDir(directory) {
  if (!directory) return;
  try {
    await rm(directory, { recursive: true, force: true });
  } finally {
    temporaryDirs.delete(directory);
  }
}

async function readSessionEntries(home) {
  const files = await readdir(join(home, "sessions"), { recursive: true });
  const session = files.find((file) => file.endsWith(".jsonl"));
  assert.ok(session, "Pi did not persist a JSONL session");
  const text = await readFile(join(home, "sessions", session), "utf8");
  return text.trim().split("\n").map((line) => JSON.parse(line));
}

async function stop(child) {
  if (!child) return;
  if (child.exitCode !== null || child.signalCode) {
    children.delete(child);
    return;
  }
  const closed = once(child, "close");
  child.kill("SIGTERM");
  try {
    await Promise.race([closed, delay(2_000).then(() => { throw new Error("Pi shutdown timed out"); })]);
  } catch {
    child.kill("SIGKILL");
    await closed;
  } finally {
    children.delete(child);
  }
}

async function startPi(home, primary, vision, imageCapable, extra = {}) {
  const ledgerDirectory = join(home, "ledger");
  await mkdir(ledgerDirectory, { mode: 0o700 });
  const { PI_FIXTURE_CORRUPT_LEDGER: corruptLedger, ...environment } = extra;
  if (corruptLedger === "1") await writeFile(join(ledgerDirectory, "mediation-inspection-ledger.json"), "not json");
  const child = spawn(pi, [
    "--mode", "rpc", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-context-files",
    "--tools", "fixture_image", "--extension", "./tests/computer_use/pi_image_mediation_probe/extension.ts",
    "--provider", "fixture-primary", "--model", "fixture-primary", "--session-dir", join(home, "sessions"),
  ], {
    cwd: process.cwd(),
    env: {
      PATH: process.env.PATH || "",
      HOME: home,
      PI_CODING_AGENT_DIR: join(home, "agent"),
      PI_CODING_AGENT_SESSION_DIR: join(home, "sessions"),
      PI_OFFLINE: "1",
      PI_FIXTURE_PRIMARY_URL: primary.baseUrl,
      PI_FIXTURE_VISION_URL: vision.baseUrl,
      PI_FIXTURE_IMAGE_CAPABLE: imageCapable,
      PI_FIXTURE_LEDGER_DIR: ledgerDirectory,
      PI_FIXTURE_OWNER_ID: "fixture-owner-1",
      PI_FIXTURE_LOGICAL_TURN: "fixture-turn-1",
      PI_FIXTURE_LEDGER_MODE: "create",
      ...environment,
    },
    stdio: ["pipe", "pipe", "pipe"],
  });
  await new Promise((resolve, reject) => {
    child.once("spawn", resolve);
    child.once("error", reject);
  });
  children.add(child);
  const events = [];
  let pending = "";
  let stderr = "";
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    pending += chunk;
    const lines = pending.split("\n");
    pending = lines.pop();
    for (const line of lines) if (line) events.push(JSON.parse(line));
  });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  return { child, events, stderr: () => stderr };
}

function bindings(stderr) {
  return [...stderr.matchAll(/PI_IMAGE_MEDIATION_BINDING:(\{.*\})/g)].map((match) => JSON.parse(match[1]));
}

function freshReceiptReader(directory) {
  const program = [
    'import fs from "node:fs";',
    'import path from "node:path";',
    'const directory = process.argv[1];',
    'const pending = path.join(directory, "fixture-observation-receipts.pending");',
    'try { if (fs.existsSync(pending)) throw new Error("budget_state_unavailable"); console.log("accepted"); }',
    'catch (error) { console.log(error.message); }',
  ].join("\n");
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", program, directory], { cwd: process.cwd(), encoding: "utf8", timeout: TIMEOUT_MS });
  assert.equal(result.status, 0, `fresh receipt reader failed: ${result.stderr}`);
  assert.equal(result.stdout.trim(), "budget_state_unavailable", "fresh receipt reader accepted a fenced receipt");
}

async function success(imageCapable, extra = {}, inspectionStatus = "observed") {
  const primary = await startFakePrimary();
  const vision = await startFakeVision();
  const home = await createTemporaryDir(`pi-image-${imageCapable}-`);
  let runner;
  try {
    runner = await startPi(home, primary, vision, imageCapable, extra);
    if (forceWatchdogHang) await new Promise(() => {});
    runner.child.stdin.write('{"id":"state","type":"get_state"}\n');
    runner.child.stdin.write(`${JSON.stringify({
      id: "attachment", type: "prompt", message: fixture.attachmentQuestion,
      images: [{ type: "image", ...fixture.image }],
    })}\n`);
    await waitFor(() => runner.events.some((event) => event.type === "agent_end"), "Pi attachment run")
      .catch((error) => { throw new Error(`${error.message}: ${runner.stderr()}`); });
    runner.child.stdin.write('{"id":"replay","type":"prompt","message":"replay the image"}\n');
    await waitFor(() => runner.events.filter((event) => event.type === "agent_end").length === 2, "Pi replay run");

    assert.match(runner.stderr(), /PI_FIXTURE_TOOLS/);
    assert.ok(runner.events.some((event) => event.id === "state" && event.type === "response"));
    assert.equal(primary.captures.length, 3, `Pi did not dispatch attachment, tool, and replay requests: ${runner.stderr()}`);
    assert.ok(primary.captures[0].parsed.tools?.some((tool) => tool.function?.name === "fixture_image"));
    for (const capture of primary.captures) assert.equal(capture.rawMedia, false, "raw image reached fake primary");
    const envelopes = primary.captures.flatMap((capture) => capture.parsed.messages.flatMap((message) =>
      (Array.isArray(message.content) ? message.content : [{ type: "text", text: message.content }]).flatMap((part) => {
        const offset = part.type === "text" && typeof part.text === "string" ? part.text.indexOf("{\"schema\":\"observation-mediation/v1\"") : -1;
        return offset < 0 ? [] : [JSON.parse(part.text.slice(offset))];
      })));
    assert.ok(envelopes.length >= 2, "primary did not receive mediation envelopes");
    for (const envelope of envelopes) {
      assert.deepEqual(Object.keys(envelope).sort(), ["error", "facts", "inspection_request_id", "inspection_status", "mediation_id", "observation_id", "question_id", "reason", "schema", "source_ref", "status"].sort());
      assert.equal(envelope.schema, "observation-mediation/v1");
      assert.equal(envelope.status, "inspected");
      assert.equal(envelope.inspection_status, inspectionStatus);
      assert.equal(envelope.facts.length, inspectionStatus === "observed" ? 1 : 0);
    }
    assert.equal(vision.captures.length, 2, "identical source bytes must not merge distinct origins");
    assert.deepEqual(vision.captures.flatMap((capture) => capture.associations), [
      { question: fixture.attachmentQuestion, image: `data:image/png;base64,${fixture.image.data}` },
      { question: fixture.toolQuestion, image: `data:image/png;base64,${fixture.image.data}` },
    ]);
    const seen = bindings(runner.stderr());
    assert.equal(seen.length, 2, "replay must reuse its originating source bindings");
    const entries = await readSessionEntries(home);
    const header = entries.find((entry) => entry.type === "session");
    const entryIds = new Set(entries.filter((entry) => entry.type === "message").map((entry) => entry.id));
    const source = /^pi:([^:]+):entry:([a-f0-9]{8}):image:(\d+)$/;
    for (const binding of seen) {
      const match = binding.source.match(source);
      assert.ok(match, `invalid durable source ${binding.source}`);
      assert.equal(match[1], header?.id);
      assert.equal(entryIds.has(match[2]), true, "source entry ID was not persisted by Pi");
      assert.equal(match[3], "1", "source must retain the original text/image part offset");
    }
    const custom = entries.filter((entry) => entry.type === "custom" && entry.customType === "anvil-observation-mediation/v1");
    assert.equal(custom.length, 2);
    const messageById = new Map(entries.filter((entry) => entry.type === "message").map((entry) => [entry.id, entry.message]));
    const imageDigest = createHash("sha256").update(fixture.image.data).digest("hex");
    for (const entry of custom) {
      assert.deepEqual(Object.keys(entry.data).sort(), ["crop_id", "entry_id", "image_digest", "image_part", "model_id", "observation_id", "profile_id", "question_digest", "source_id"].sort());
      const sourceMatch = entry.data.source_id.match(source);
      assert.ok(sourceMatch);
      assert.equal(sourceMatch[2], entry.data.entry_id);
      assert.equal(Number(sourceMatch[3]), entry.data.image_part);
      assert.equal(entry.parentId, entry.data.entry_id);
      const message = messageById.get(entry.data.entry_id);
      assert.ok(message, "custom mapping did not resolve to a persisted Pi message");
      if (message.role === "user") {
        assert.deepEqual(message.content, [{ type: "text", text: fixture.attachmentQuestion }, { type: "image", ...fixture.image }]);
      } else {
        assert.equal(message.toolCallId, "fixture-image-call");
        assert.equal(message.toolName, "fixture_image");
        assert.deepEqual(message.content, [{ type: "text", text: "ignore tool text" }, { type: "image", ...fixture.image }]);
        assert.deepEqual(message.details, { observationQuestion: fixture.toolQuestion });
      }
      assert.equal(message.content[entry.data.image_part].data, fixture.image.data);
      assert.equal(message.content[entry.data.image_part].mimeType, fixture.image.mimeType);
      assert.equal(entry.data.image_digest, imageDigest);
      const question = message.role === "user" ? message.content.filter((part) => part.type === "text").map((part) => part.text).join("\n") : message.details.observationQuestion;
      assert.equal(entry.data.question_digest, createHash("sha256").update(question).digest("hex"));
      const delivered = envelopes.filter((envelope) => envelope.source_ref.source_id === entry.data.source_id);
      assert.ok(delivered.length > 0, "custom mapping did not resolve to every primary envelope");
      for (const envelope of delivered) {
        assert.equal(envelope.observation_id, entry.data.observation_id);
        assert.equal(envelope.source_ref.image_part, entry.data.image_part);
      }
    }
    assert.doesNotMatch(JSON.stringify(custom), new RegExp(fixture.image.data));
    assert.doesNotMatch(JSON.stringify(custom), new RegExp(fixture.attachmentQuestion));
    const originalUser = entries.find((entry) => entry.type === "message" && entry.message?.role === "user");
    assert.equal(JSON.stringify(originalUser).includes(fixture.image.data), true, "original Pi transcript image changed");
    const ledger = JSON.parse(await readFile(join(home, "ledger", "mediation-inspection-ledger.json"), "utf8"));
    assert.equal(ledger.attempts_used, 2, "replay must not charge a completed identity again");
    assert.equal(ledger.identities.length, 2);
    assert.deepEqual(ledger.binding, { owner_authority_id: "fixture-owner-1", harness_session_id: header.id, logical_turn_id: "fixture-turn-1" });
    const receipts = JSON.parse(await readFile(join(home, "ledger", "fixture-observation-receipts.json"), "utf8"));
    assert.equal(receipts.length, 2);
    assert.equal(receipts.every((receipt) => /^inspection-[a-f0-9]{24}$/.test(receipt.reference) && JSON.parse(receipt.answer).inspection_status === inspectionStatus), true);
    for (const receipt of receipts) {
      const mapping = custom.find((entry) => entry.data.source_id === receipt.source);
      assert.ok(mapping, "receipt source did not join an actual Pi custom mapping");
      assert.equal(receipt.owner_authority_id, "fixture-owner-1");
      assert.equal(receipt.harness_session_id, header.id);
      assert.equal(receipt.logical_turn_id, "fixture-turn-1");
      assert.equal(receipt.entry_id, mapping.data.entry_id);
      assert.equal(receipt.part, mapping.data.image_part);
      assert.equal(receipt.observation, mapping.data.observation_id);
      assert.equal(receipt.question_digest, mapping.data.question_digest);
      assert.equal(receipt.image_digest, mapping.data.image_digest);
      assert.equal(receipt.crop, mapping.data.crop_id);
      assert.equal(receipt.model, mapping.data.model_id);
      assert.equal(receipt.profile, mapping.data.profile_id);
      const answer = JSON.parse(receipt.answer);
      const delivered = envelopes.filter((envelope) => envelope.source_ref.source_id === receipt.source);
      assert.ok(delivered.length > 0);
      for (const envelope of delivered) assert.deepEqual({ inspection_status: envelope.inspection_status, facts: envelope.facts, reason: envelope.reason }, answer);
      const identity = ledger.identities.find((value) => value.reference === receipt.reference);
      assert.ok(identity, "receipt reference did not join a ledger identity");
      assert.equal(identity.status, "completed");
      assert.deepEqual(identity.binding, { observation_id: receipt.observation, source_entry_id: receipt.entry_id, source_part: `image-${receipt.part}`, question_digest: receipt.question_digest, crop_id: receipt.crop, model_id: receipt.model, profile_id: receipt.profile });
    }
    assert.doesNotMatch(JSON.stringify(vision.captures), new RegExp(fixture.untrustedToolText));
  } finally {
    await stop(runner?.child);
    await Promise.all([primary.close(), vision.close()]);
    await removeTemporaryDir(home);
  }
}

async function negative(name, extra, prompt, imageCapable = "1", expectedVision = 0) {
  const primary = await startFakePrimary();
  const vision = await startFakeVision();
  const home = await createTemporaryDir(`pi-image-${name}-`);
  let runner;
  try {
    runner = await startPi(home, primary, vision, imageCapable, extra);
    runner.child.stdin.write(`${JSON.stringify(prompt)}\n`);
    await waitFor(() => runner.stderr().includes(`PI_IMAGE_MEDIATION_ERROR:${name}`), `Pi ${name} rejection`);
    await waitFor(
      () => runner.events.some((event) => event.type === "agent_end") || runner.child.exitCode !== null || runner.child.signalCode,
      `Pi ${name} settlement`,
    );
    await stop(runner.child);
    assert.match(runner.stderr(), new RegExp(`PI_IMAGE_MEDIATION_ERROR:${name}`), JSON.stringify(runner.events));
    assert.equal(primary.captures.length, 0, `${name} leaked a primary request`);
    assert.equal(vision.captures.length, expectedVision, `${name} dispatched an unexpected vision request`);
    if (name === "endpoint_unavailable" || name === "invalid_response" || name === "deadline_exceeded" || name === "cancelled" || extra.PI_FIXTURE_APPEND_THROW === "1") {
      const ledger = JSON.parse(await readFile(join(home, "ledger", "mediation-inspection-ledger.json"), "utf8"));
      assert.equal(ledger.attempts_used, 1, "failed vision call did not retain its charge");
      assert.equal(ledger.in_flight, null);
      assert.equal(ledger.identities[0].status, name === "cancelled" ? "cancelled" : "failed");
    }
  } finally {
    await stop(runner?.child);
    await Promise.all([primary.close(), vision.close()]);
    await removeTemporaryDir(home);
  }
}

async function receiptRefusal(name, mutate, extra = {}, imageCapable = "1") {
  const primary = await startFakePrimary(), vision = await startFakeVision(), home = await createTemporaryDir(`pi-image-${name}-`);
  let runner;
  try {
    runner = await startPi(home, primary, vision, imageCapable, extra);
    runner.child.stdin.write(`${JSON.stringify({ id: "first", type: "prompt", message: fixture.attachmentQuestion, images: [{ type: "image", ...fixture.image }] })}\n`);
    await waitFor(() => runner.events.some((event) => event.type === "agent_end"), `${name} initial run`);
    const visionBefore = vision.captures.length, primaryBefore = primary.captures.length;
    await mutate(join(home, "ledger", "fixture-observation-receipts.json"));
    runner.child.stdin.write('{"id":"replay","type":"prompt","message":"replay the image"}\n');
    await waitFor(() => runner.stderr().includes(`PI_IMAGE_MEDIATION_ERROR:${name}`), `${name} replay rejection`);
    await waitFor(() => runner.events.filter((event) => event.type === "agent_end").length >= 2 || runner.child.exitCode !== null || runner.child.signalCode, `${name} replay settlement`);
    assert.equal(vision.captures.length, visionBefore, `${name} reinferred after receipt failure`);
    assert.equal(primary.captures.length, primaryBefore, `${name} dispatched primary after receipt failure`);
  } finally {
    await stop(runner?.child);
    await Promise.all([primary.close(), vision.close()]);
    await removeTemporaryDir(home);
  }
}

async function receiptFence(phase) {
  const primary = await startFakePrimary(), vision = await startFakeVision(), home = await createTemporaryDir(`pi-image-receipt-fence-${phase}-`);
  let runner;
  try {
    runner = await startPi(home, primary, vision, "1", { PI_FIXTURE_RECEIPT_FAIL_PHASE: phase });
    runner.child.stdin.write(`${JSON.stringify({ id: "first", type: "prompt", message: fixture.attachmentQuestion, images: [{ type: "image", ...fixture.image }] })}\n`);
    await waitFor(() => runner.stderr().includes("PI_IMAGE_MEDIATION_ERROR:budget_state_unavailable"), `receipt ${phase} initial refusal`);
    await waitFor(() => runner.events.some((event) => event.type === "agent_end") || runner.child.exitCode !== null || runner.child.signalCode, `receipt ${phase} initial settlement`);
    const visions = vision.captures.length;
    runner.child.stdin.write('{"id":"replay","type":"prompt","message":"replay the image"}\n');
    await waitFor(() => runner.stderr().match(/PI_IMAGE_MEDIATION_ERROR:budget_state_unavailable/g)?.length >= 2, `receipt ${phase} replay refusal`);
    await waitFor(() => runner.events.filter((event) => event.type === "agent_end").length >= 2 || runner.child.exitCode !== null || runner.child.signalCode, `receipt ${phase} replay settlement`);
    assert.equal(visions, 1);
    assert.equal(vision.captures.length, 1, "fenced receipt authorized reinference");
    assert.equal(primary.captures.length, 0, "fenced receipt authorized primary dispatch");
    assert.match(await readFile(join(home, "ledger", "fixture-observation-receipts.pending"), "utf8"), /^[a-f0-9]{64}\n$/);
    freshReceiptReader(join(home, "ledger"));
    const ledger = JSON.parse(await readFile(join(home, "ledger", "mediation-inspection-ledger.json"), "utf8"));
    assert.equal(ledger.in_flight, null);
    assert.equal(ledger.identities[0].status, "completed");
  } finally {
    await stop(runner?.child);
    await Promise.all([primary.close(), vision.close()]);
    await removeTemporaryDir(home);
  }
}

async function toolRefusal(extra, expectedCode) {
  const primary = await startFakePrimary(), vision = await startFakeVision(), home = await createTemporaryDir(`pi-image-${expectedCode}-`);
  let runner;
  try {
    runner = await startPi(home, primary, vision, "1", extra);
    runner.child.stdin.write(`${JSON.stringify({ id: "tool", type: "prompt", message: fixture.attachmentQuestion, images: [{ type: "image", ...fixture.image }] })}\n`);
    await waitFor(() => runner.stderr().includes(`PI_IMAGE_MEDIATION_ERROR:${expectedCode}`), `${expectedCode} tool refusal`);
    await waitFor(() => runner.events.some((event) => event.type === "agent_end") || runner.child.exitCode !== null || runner.child.signalCode, `${expectedCode} tool settlement`);
    assert.equal(vision.captures.length, 1, `${expectedCode} admitted the tool image`);
    assert.equal(primary.captures.length, 1, `${expectedCode} dispatched after the tool mismatch`);
  } finally {
    await stop(runner?.child);
    await Promise.all([primary.close(), vision.close()]);
    await removeTemporaryDir(home);
  }
}

async function main() {
  const version = spawnSync(pi, ["--version"], { encoding: "utf8", timeout: TIMEOUT_MS, killSignal: "SIGKILL" });
  assert.equal(version.status, 0, `Pi unavailable: ${version.error?.message || version.stderr}`);
  await success("1");
  await success("0");
  await success("1", { PI_FIXTURE_VISION_MODE: "inconclusive" }, "inconclusive");
  await success("1", { PI_FIXTURE_VISION_MODE: "unsupported" }, "unsupported");
  for (const imageCapable of ["1", "0"]) {
    await negative("budget_state_unavailable", { PI_FIXTURE_OWNER_ID: "" }, {
      type: "prompt", message: fixture.attachmentQuestion,
      images: [{ type: "image", ...fixture.image }],
    }, imageCapable);
    await negative("budget_state_unavailable", { PI_FIXTURE_LEDGER_MODE: "reopen" }, {
      type: "prompt", message: fixture.attachmentQuestion,
      images: [{ type: "image", ...fixture.image }],
    }, imageCapable);
    await negative("unsupported_image", {}, {
      type: "prompt", message: fixture.attachmentQuestion,
      images: [{ type: "image", mimeType: "image/jpeg", data: fixture.image.data }],
    }, imageCapable);
    await negative("guard_exception", { PI_FIXTURE_GUARD_THROW: "1" }, {
      type: "prompt", message: fixture.attachmentQuestion,
      images: [{ type: "image", ...fixture.image }],
    }, imageCapable);
    await negative("endpoint_unavailable", { PI_FIXTURE_VISION_URL: "http://127.0.0.1:1/v1" }, {
      type: "prompt", message: fixture.attachmentQuestion,
      images: [{ type: "image", ...fixture.image }],
    }, imageCapable);
    await negative("raw_media_guard", { PI_FIXTURE_INJECT_RAW_PROVIDER: "1" }, {
      type: "prompt", message: fixture.attachmentQuestion,
      images: [{ type: "image", ...fixture.image }],
    }, imageCapable, 1);
  }
  await negative("question_required", {}, {
    type: "prompt", message: "Compare both images", images: [{ type: "image", ...fixture.image }, { type: "image", ...fixture.image }],
  });
  await negative("unsupported_history", { PI_FIXTURE_ALIGNMENT_MISMATCH: "1" }, {
    type: "prompt", message: fixture.attachmentQuestion, images: [{ type: "image", ...fixture.image }],
  });
  await negative("budget_state_unavailable", { PI_FIXTURE_LEDGER_MODE: "reopen", PI_FIXTURE_CORRUPT_LEDGER: "1" }, {
    type: "prompt", message: fixture.attachmentQuestion, images: [{ type: "image", ...fixture.image }],
  });
  await negative("guard_exception", { PI_FIXTURE_APPEND_THROW: "1" }, {
    type: "prompt", message: fixture.attachmentQuestion, images: [{ type: "image", ...fixture.image }],
  });
  for (const imageCapable of ["1", "0"]) for (const mode of ["extra_field", "malformed_fact", "truncated", "media", "reason_media"]) {
    await negative("invalid_response", { PI_FIXTURE_VISION_MODE: mode }, {
      type: "prompt", message: fixture.attachmentQuestion, images: [{ type: "image", ...fixture.image }],
    }, imageCapable, 1);
  }
  await negative("deadline_exceeded", { PI_FIXTURE_VISION_MODE: "hang", PI_FIXTURE_PER_CALL_MS: "25" }, {
    type: "prompt", message: fixture.attachmentQuestion, images: [{ type: "image", ...fixture.image }],
  }, "1", 1);
  await negative("deadline_exceeded", { PI_FIXTURE_MAPPING_DELAY_MS: "35", PI_FIXTURE_PER_CALL_MS: "25" }, {
    type: "prompt", message: fixture.attachmentQuestion, images: [{ type: "image", ...fixture.image }],
  }, "1", 0);
  await negative("deadline_exceeded", { PI_FIXTURE_VISION_MODE: "body_hang", PI_FIXTURE_PER_CALL_MS: "25" }, {
    type: "prompt", message: fixture.attachmentQuestion, images: [{ type: "image", ...fixture.image }],
  }, "1", 1);
  await negative("cancelled", { PI_FIXTURE_CANCEL_BEFORE_VISION: "1" }, {
    type: "prompt", message: fixture.attachmentQuestion, images: [{ type: "image", ...fixture.image }],
  }, "1", 0);
  for (const imageCapable of ["1", "0"]) await receiptRefusal("budget_state_unavailable", (target) => unlink(target), {}, imageCapable);
  for (const imageCapable of ["1", "0"]) await receiptRefusal("budget_state_unavailable", async (target) => {
    const contents = await readFile(target, "utf8");
    await writeFile(target, contents.replace("fixture-owner-1", "wrong-owner"));
  }, {}, imageCapable);
  for (const imageCapable of ["1", "0"]) await receiptRefusal("budget_state_unavailable", async (target) => {
    const receipts = JSON.parse(await readFile(target, "utf8"));
    receipts[0].answer = JSON.stringify({ inspection_status: "observed", facts: [], reason: "cached observed reason" });
    await writeFile(target, JSON.stringify(receipts));
  }, {}, imageCapable);
  for (const imageCapable of ["1", "0"]) await receiptRefusal("budget_state_unavailable", async (target) => {
    const receipts = JSON.parse(await readFile(target, "utf8"));
    receipts[0].answer = JSON.stringify({ inspection_status: "inconclusive", facts: [], reason: "data:image/png;base64,forbidden" });
    await writeFile(target, JSON.stringify(receipts));
  }, {}, imageCapable);
  await receiptRefusal("budget_state_unavailable", async (target) => {
    const ledgerTarget = join(target, "..", "mediation-inspection-ledger.json");
    const ledger = JSON.parse(await readFile(ledgerTarget, "utf8"));
    ledger.identities[0].binding.question_digest = "0".repeat(64);
    await writeFile(ledgerTarget, JSON.stringify(ledger));
  });
  for (const phase of ["pending_directory_sync", "before_replace", "receipt_directory_sync", "pending_remove"]) await receiptFence(phase);
  await toolRefusal({ PI_FIXTURE_TOOL_IDENTITY_MISMATCH: "1" }, "unsupported_history");
  await toolRefusal({ PI_FIXTURE_QUESTION_DETAIL_MISMATCH: "1" }, "unsupported_history");
  await toolRefusal({ PI_FIXTURE_MAX_ATTEMPTS: "1" }, "budget_exhausted");
  process.stdout.write(`pi image interception: ok (${version.stdout.trim()}; seam=context+before_provider_request)\n`);
}

async function terminateForWatchdog() {
  process.stderr.write("Pi probe watchdog timed out\n");
  for (const child of children) {
    if (child.exitCode === null && !child.signalCode) child.kill("SIGKILL");
  }
  children.clear();
  await Promise.race([
    Promise.allSettled([...temporaryDirs].map(removeTemporaryDir)),
    delay(250),
  ]);
  process.exit(1);
}

const watchdog = setTimeout(() => { void terminateForWatchdog(); }, PROBE_TIMEOUT_MS);
try {
  await main();
} finally {
  await Promise.all([...children].map(stop));
  await Promise.all([...temporaryDirs].map(removeTemporaryDir));
  clearTimeout(watchdog);
}
