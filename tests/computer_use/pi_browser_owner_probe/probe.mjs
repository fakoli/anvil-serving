import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const pi = process.env.PI_BINARY || "pi";
const home = await mkdtemp(join(tmpdir(), "pi-browser-owner-"));
const child = spawn(pi, ["--mode", "rpc", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-context-files", "--extension", "./tests/computer_use/pi_browser_owner_probe/extension.ts", "--session-dir", join(home, "sessions")], { cwd: process.cwd(), env: { PATH: process.env.PATH || "", HOME: home, PI_CODING_AGENT_DIR: join(home, "agent"), PI_CODING_AGENT_SESSION_DIR: join(home, "sessions"), PI_OFFLINE: "1", CHROMIUM_EXECUTABLE: process.env.CHROMIUM_EXECUTABLE || "/usr/bin/google-chrome" }, stdio: ["pipe", "pipe", "pipe"] });
let stdout = "", stderr = "";
child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8"); child.stdout.on("data", (part) => { stdout += part; }); child.stderr.on("data", (part) => { stderr += part; });
const wait = async (match) => { const end = Date.now() + 12_000; while (!match()) { if (Date.now() > end) throw new Error(`timeout: ${stderr}`); await new Promise((resolve) => setTimeout(resolve, 20)); } };
try {
  await once(child, "spawn");
  await wait(() => stderr.includes("PI_BROWSER_OWNER_READY:"));
  const ready = JSON.parse(stderr.match(/PI_BROWSER_OWNER_READY:(\{.*\})/)?.[1] || "null");
  assert.deepEqual(ready.active, ["browser_capture", "browser_release", "browser_resolve"]);
  child.stdin.write('{"id":"state","type":"get_state"}\n');
  await wait(() => stdout.includes('"id":"state"'));
  child.stdin.end();
  await once(child, "close");
  assert.equal(child.exitCode, 0, stderr);
  assert.match(stderr, /PI_BROWSER_OWNER_CLOSED/);
  assert.doesNotMatch(stdout + stderr, /iVBOR|data:image\//);
  console.log(JSON.stringify({ status: "passed", ready, closed: true, limitation: "Pi tool rendering and provider ordering are not exercised by this lifecycle probe." }));
} finally {
  if (child.exitCode === null && !child.signalCode) { child.kill("SIGTERM"); await once(child, "close").catch(() => {}); }
  await rm(home, { recursive: true, force: true });
}
