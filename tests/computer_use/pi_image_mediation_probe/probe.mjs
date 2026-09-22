import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, mkdir, readdir, readFile, rm } from "node:fs/promises";
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
      ...extra,
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

async function success(imageCapable) {
  const primary = await startFakePrimary();
  const vision = await startFakeVision();
  const home = await createTemporaryDir(`pi-image-${imageCapable}-`);
  let runner;
  try {
    runner = await startPi(home, primary, vision, imageCapable);
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
      Array.isArray(message.content) ? message.content : []).filter((part) => part.type === "text" && part.text.startsWith("{\"schema\":\"observation-mediation/v1\"")).map((part) => JSON.parse(part.text)));
    assert.ok(envelopes.length >= 2, "primary did not receive mediation envelopes");
    for (const envelope of envelopes) {
      assert.deepEqual(Object.keys(envelope).sort(), ["error", "facts", "inspection_request_id", "inspection_status", "mediation_id", "observation_id", "question_id", "reason", "schema", "source_ref", "status"].sort());
      assert.equal(envelope.schema, "observation-mediation/v1");
      assert.equal(envelope.status, "inspected");
      assert.equal(envelope.inspection_status, "observed");
      assert.equal(envelope.facts.length, 1);
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
    assert.deepEqual(seen[0].question, fixture.attachmentQuestion);
    assert.deepEqual(seen[1].question, fixture.toolQuestion);
    const custom = entries.filter((entry) => entry.type === "custom" && entry.customType === "anvil-observation-mediation/v1");
    assert.equal(custom.length, 2);
    assert.doesNotMatch(JSON.stringify(custom), new RegExp(fixture.image.data));
    assert.doesNotMatch(JSON.stringify(custom), new RegExp(fixture.attachmentQuestion));
    const originalUser = entries.find((entry) => entry.type === "message" && entry.message?.role === "user");
    assert.equal(JSON.stringify(originalUser).includes(fixture.image.data), true, "original Pi transcript image changed");
    const ledger = JSON.parse(await readFile(join(home, "ledger", "mediation-inspection-ledger.json"), "utf8"));
    assert.equal(ledger.attempts_used, 2, "replay must not charge a completed identity again");
    assert.equal(ledger.identities.length, 2);
    const receipts = JSON.parse(await readFile(join(home, "ledger", "fixture-observation-receipts.json"), "utf8"));
    assert.equal(receipts.length, 2);
    assert.equal(receipts.every((receipt) => receipt.answer === vision.response && /^inspection-[a-f0-9]{24}$/.test(receipt.reference)), true);
    assert.doesNotMatch(JSON.stringify(vision.captures), new RegExp(fixture.untrustedToolText));
  } finally {
    await stop(runner?.child);
    await Promise.all([primary.close(), vision.close()]);
    await removeTemporaryDir(home);
  }
}

async function negative(name, extra, prompt, imageCapable = "1") {
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
    if (name === "endpoint_unavailable") {
      const ledger = JSON.parse(await readFile(join(home, "ledger", "mediation-inspection-ledger.json"), "utf8"));
      assert.equal(ledger.attempts_used, 1, "failed vision call did not retain its charge");
      assert.equal(ledger.in_flight, null);
      assert.equal(ledger.identities[0].status, "failed");
    }
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
    }, imageCapable);
  }
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
