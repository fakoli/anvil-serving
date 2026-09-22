import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { startFakePrimary } from "./fake-primary.mjs";
import { startFakeVision } from "./fake-vision.mjs";
import fixture from "./fixtures.json" with { type: "json" };

const pi = process.env.PI_BINARY || "pi";
async function negative(name, extra, prompt) {
  const p = await startFakePrimary();
  const v = await startFakeVision();
  const isolatedHome = await mkdtemp(join(tmpdir(), `pi-image-${name}-`));
  try {
    const child = spawn(pi, ["--mode", "rpc", "--no-skills", "--no-prompt-templates", "--no-context-files", "--extension", "./tests/computer_use/pi_image_mediation_probe/extension.ts", "--provider", "fixture-primary", "--model", "fixture-primary"], { cwd: process.cwd(), env: { PATH: process.env.PATH, HOME: isolatedHome, PI_FIXTURE_PRIMARY_URL: p.baseUrl, PI_FIXTURE_VISION_URL: v.baseUrl, PI_FIXTURE_IMAGE_CAPABLE: "0", ...extra }, stdio: ["pipe", "ignore", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.stdin.end(`${JSON.stringify(prompt)}\n`);
    await once(child, "close");
    assert.match(stderr, new RegExp(`PI_IMAGE_MEDIATION_ERROR:${name}`));
    assert.equal(p.captures.length, 0);
  } finally {
    await Promise.all([p.close(), v.close()]);
    await rm(isolatedHome, { recursive: true, force: true });
  }
}
const primary = await startFakePrimary();
const vision = await startFakeVision();
const home = await mkdtemp(join(tmpdir(), "pi-image-proof-"));
let child;

try {
  child = spawn(pi, ["--mode", "rpc", "--no-skills", "--no-prompt-templates", "--no-context-files", "--tools", "fixture_image", "--extension", "./tests/computer_use/pi_image_mediation_probe/extension.ts", "--provider", "fixture-primary", "--model", "fixture-primary"], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH, HOME: home, PI_FIXTURE_PRIMARY_URL: primary.baseUrl, PI_FIXTURE_VISION_URL: vision.baseUrl, PI_FIXTURE_IMAGE_CAPABLE: process.env.PI_FIXTURE_IMAGE_CAPABLE || "1" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  const events = [];
  let pending = "";
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    pending += chunk;
    const lines = pending.split("\n");
    pending = lines.pop();
    for (const line of lines) if (line) events.push(JSON.parse(line));
  });
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  child.stdin.write('{"id":"state","type":"get_state"}\n');
  child.stdin.write(`${JSON.stringify({ id: "attachment", type: "prompt", message: `[fixture-tool] ${fixture.question}`, images: [{ type: "image", ...fixture.image }] })}\n`);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Pi did not settle")), 5_000);
    const ready = () => events.some((event) => event.type === "agent_end");
    const poll = () => ready() ? (clearTimeout(timer), resolve()) : setTimeout(poll, 5);
    poll();
  });
  child.stdin.write('{"id":"replay","type":"prompt","message":"replay the image"}\n');
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Pi replay did not settle")), 5_000);
    const ready = () => events.filter((event) => event.type === "agent_end").length === 2;
    const poll = () => ready() ? (clearTimeout(timer), resolve()) : setTimeout(poll, 5);
    poll();
  });
  assert.match(stderr, /PI_FIXTURE_TOOLS/);
  assert.match(stderr, /PI_IMAGE_MEDIATION_BINDING:2/);
  assert.ok(primary.captures[0].parsed.tools?.some((tool) => tool.function?.name === "fixture_image"));
  assert.equal(primary.captures.length, 3, "Pi did not dispatch the replayed history request");
  assert.ok(events.some((event) => event.id === "state" && event.type === "response"));
  assert.equal(vision.captures.length, 1);
  assert.deepEqual(vision.captures[0].associations, [{ question: `[fixture-tool] ${fixture.question}`, image: `data:image/png;base64,${fixture.image.data}` }]);
  for (const capture of primary.captures) assert.doesNotMatch(JSON.stringify(capture.parsed.messages), /"type":"image/);
  await negative("unsupported_image", {}, { type: "prompt", message: fixture.question, images: [{ type: "image", mimeType: "image/jpeg", data: fixture.image.data }] });
  await negative("guard_exception", { PI_FIXTURE_GUARD_THROW: "1" }, { type: "prompt", message: "guard failure" });
  process.stdout.write("pi image interception: ok\n");
} finally {
  if (child && !child.killed) {
    const exited = once(child, "close");
    child.kill();
    await exited;
  }
  await Promise.all([primary.close(), vision.close()]);
  await rm(home, { recursive: true, force: true });
}
