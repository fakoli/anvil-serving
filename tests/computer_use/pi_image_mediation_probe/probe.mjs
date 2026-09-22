import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { startFakePrimary } from "./fake-primary.mjs";
import { startFakeVision } from "./fake-vision.mjs";
import fixture from "./fixtures.json" with { type: "json" };

const pi = "/data/apps/devtools/pi-0.85.1/pi";
const primary = await startFakePrimary();
const vision = await startFakeVision();
const home = await mkdtemp(join(tmpdir(), "pi-image-proof-"));

try {
  const child = spawn(pi, ["--mode", "rpc", "--no-skills", "--no-prompt-templates", "--no-context-files", "--tools", "fixture_image", "--extension", "./tests/computer_use/pi_image_mediation_probe/extension.ts", "--provider", "fixture-primary", "--model", "fixture-primary"], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH, HOME: home, PI_FIXTURE_PRIMARY_URL: primary.baseUrl, PI_FIXTURE_VISION_URL: vision.baseUrl, PI_FIXTURE_IMAGE_CAPABLE: "1" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  const events = [];
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (chunk) => chunk.trim().split("\n").filter(Boolean).forEach((line) => events.push(JSON.parse(line))));
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
  assert.match(stderr, /PI_FIXTURE_TOOLS/);
  assert.ok(primary.captures[0].parsed.tools?.some((tool) => tool.function?.name === "fixture_image"));
  assert.equal(primary.captures.length, 2, "Pi emitted an assistant reply after the real tool result without a second primary request");
  assert.ok(events.some((event) => event.id === "state" && event.type === "response"));
  assert.equal(vision.captures.length, 2);
  assert.deepEqual(vision.captures[1].associations, [{ question: `[fixture-tool] ${fixture.question}`, image: `data:image/png;base64,${fixture.image.data}` }]);
  for (const capture of primary.captures) assert.doesNotMatch(JSON.stringify(capture.parsed.messages), /"type":"image/);
  child.kill();
  process.stdout.write("pi image interception: ok\n");
} finally {
  await Promise.all([primary.close(), vision.close()]);
  await rm(home, { recursive: true, force: true });
}
