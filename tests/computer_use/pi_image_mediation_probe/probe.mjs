import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
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
    await waitFor(() => runner.events.some((event) => event.type === "agent_end"), "Pi attachment run");
    runner.child.stdin.write('{"id":"replay","type":"prompt","message":"replay the image"}\n');
    await waitFor(() => runner.events.filter((event) => event.type === "agent_end").length === 2, "Pi replay run");

    assert.match(runner.stderr(), /PI_FIXTURE_TOOLS/);
    assert.ok(runner.events.some((event) => event.id === "state" && event.type === "response"));
    assert.equal(primary.captures.length, 3, "Pi did not dispatch attachment, tool, and replay requests");
    assert.ok(primary.captures[0].parsed.tools?.some((tool) => tool.function?.name === "fixture_image"));
    for (const capture of primary.captures) assert.equal(capture.rawMedia, false, "raw image reached fake primary");
    assert.equal(vision.captures.length, 2, "identical source bytes must not merge distinct origins");
    assert.deepEqual(vision.captures.flatMap((capture) => capture.associations), [
      { question: fixture.attachmentQuestion, image: `data:image/png;base64,${fixture.image.data}` },
      { question: fixture.toolQuestion, image: `data:image/png;base64,${fixture.image.data}` },
    ]);
    const seen = bindings(runner.stderr());
    assert.equal(seen.length, 2, "replay must reuse its originating source bindings");
    assert.match(seen[0].source, /^user:\d+:\d+:0$/);
    assert.deepEqual(seen[0].question, fixture.attachmentQuestion);
    assert.equal(seen[1].source, "tool:fixture-image-call:0");
    assert.deepEqual(seen[1].question, fixture.toolQuestion);
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
    await negative("unsupported_image", {}, {
      type: "prompt", message: fixture.attachmentQuestion,
      images: [{ type: "image", mimeType: "image/jpeg", data: fixture.image.data }],
    }, imageCapable);
    await negative("guard_exception", { PI_FIXTURE_GUARD_THROW: "1" }, {
      type: "prompt", message: fixture.attachmentQuestion,
      images: [{ type: "image", ...fixture.image }],
    }, imageCapable);
    await negative("vision_failed", { PI_FIXTURE_VISION_URL: "http://127.0.0.1:1/v1" }, {
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
