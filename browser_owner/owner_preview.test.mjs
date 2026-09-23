import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { chmod, lstat, mkdir, mkdtemp, open, readFile, readdir, rm, symlink, utimes, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import test from "node:test";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";
import { OwnerError, createBrowserOwner } from "./owner.mjs";
import { previewNamespace } from "./owner_preview.mjs";

const executable = process.env.CHROMIUM_EXECUTABLE || "/usr/bin/google-chrome";
const error = (code) => (value) => value instanceof OwnerError && value.code === code;
const request = { schema: "widget-resolution/v1", request_id: "preview", target: { description: "preview", qualifiers: [] }, predicates: ["exists", "in_viewport", "occluded", "enabled"], scope: { kind: "document", root: "document" }, require_unique: true };
async function fixture() {
  const server = createServer((_request, response) => response.end("<!doctype html><button>Preview</button>"));
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  return { origin, close: () => new Promise((resolve) => server.close(resolve)) };
}
async function viewer(directory) {
  const file = join(directory, "viewer.mjs"), attest = join(directory, "attest.json");
  await writeFile(file, `#!${process.execPath}\nimport { createHash } from "node:crypto";import { lstat,readFile,writeFile } from "node:fs/promises";import { dirname } from "node:path";import { spawn } from "node:child_process";const path=process.argv.at(-1);if(process.env.PREVIEW_HANG){setInterval(()=>{},1000);await new Promise(()=>{})}if(process.env.PREVIEW_DELAY){await new Promise(resolve=>setTimeout(resolve,Number(process.env.PREVIEW_DELAY)))}if(process.env.PREVIEW_MARKER){spawn(process.execPath,["-e","setTimeout(()=>require('node:fs').writeFileSync(process.env.PREVIEW_MARKER,'late'),100)"],{stdio:"ignore"});process.exit(0)}const [data,stat,directory]=await Promise.all([readFile(path),lstat(path),lstat(dirname(path))]);await writeFile(process.env.PREVIEW_ATTEST,JSON.stringify({digest:createHash("sha256").update(data).digest("hex"),mode:stat.mode&0o777,directory:directory.mode&0o777}));`);
  await chmod(file, 0o700); return { file, attest };
}
function screenshotHook() {
  let image, page;
  return { image: () => image, page: () => page, prepare: async (browser) => { const context = browser.newContext.bind(browser); browser.newContext = async (...args) => { const value = await context(...args); const newPage = value.newPage.bind(value); value.newPage = async (...pageArgs) => { const output = await newPage(...pageArgs), screenshot = output.screenshot.bind(output); page = output; output.screenshot = async (...screenshotArgs) => { image = await screenshot(...screenshotArgs); return image; }; return output; }; return value; }; } };
}
async function owner(origin, preview, clock, prepare, ttl = 60_000) {
  return createBrowserOwner({ launch: async () => { const browser = await chromium.launch({ executablePath: executable, headless: true, args: ["--disable-gpu"] }); await prepare?.(browser); return browser; }, documentOrigins: [origin], subresourceOrigins: [origin], limits: { ttl, timeout: 5_000 }, preview, clock });
}

test("preview delivers one retained PNG to the fixed viewer with protected modes and no public path", { timeout: 15_000 }, async (t) => {
  const dir = await mkdtemp(join(tmpdir(), "owner-preview-")), site = await fixture(), fake = await viewer(dir), hook = screenshotHook(); const old = process.env.PREVIEW_ATTEST; process.env.PREVIEW_ATTEST = fake.attest;
  const core = await owner(site.origin, { viewer: [fake.file], runtimeRoot: dir }, undefined, hook.prepare); const session = core.session();
  t.after(async () => { if (old === undefined) delete process.env.PREVIEW_ATTEST; else process.env.PREVIEW_ATTEST = old; await core.close(); await site.close(); await rm(dir, { recursive: true, force: true }); });
  await session.navigate(`${site.origin}/`); const observation = await session.capture(request); const result = await session.preview(observation.observation_id);
  assert.deepEqual(result, { status: "shown" }); assert.equal(JSON.stringify(result).includes("/"), false);
  const proof = JSON.parse(await readFile(fake.attest, "utf8")); assert.equal(proof.digest, createHash("sha256").update(hook.image()).digest("hex"), "viewer did not receive the retained screenshot");
  assert.equal(proof.mode, 0o600); assert.equal(proof.directory, 0o700); assert.equal(existsSync(join(dir, previewNamespace)), true); assert.equal((await readdir(join(dir, previewNamespace))).length, 0);
});

test("preview refuses disabled, stale, released, expired, closed, and win32 before delivery", { timeout: 15_000 }, async (t) => {
  const dir = await mkdtemp(join(tmpdir(), "owner-preview-")), site = await fixture(), fake = await viewer(dir); let tick = 0;
  const disabled = await owner(site.origin, undefined); const disabledSession = disabled.session(); await disabledSession.navigate(`${site.origin}/`); const disabledObservation = await disabledSession.capture(request); await assert.rejects(disabledSession.preview(disabledObservation.observation_id), error("preview_disabled")); await disabled.close();
  const hook = screenshotHook(); const core = await owner(site.origin, { viewer: [fake.file], runtimeRoot: dir }, () => tick, hook.prepare, 100); const session = core.session();
  t.after(async () => { await core.close(); await site.close(); await rm(dir, { recursive: true, force: true }); });
  await session.navigate(`${site.origin}/`); const released = await session.capture(request); await session.release(released.observation_id); await assert.rejects(session.preview(released.observation_id), error("unknown_observation"));
  const expired = await session.capture({ ...request, request_id: "expired" }); tick = 101; await assert.rejects(session.preview(expired.observation_id), error("expired_observation")); tick = 0;
  const stale = await session.capture({ ...request, request_id: "stale" }); await hook.page().evaluate(() => document.body.setAttribute("data-stale", "1")); await assert.rejects(session.preview(stale.observation_id), error("stale_observation"));
  const supported = await session.capture({ ...request, request_id: "win" }); const descriptor = Object.getOwnPropertyDescriptor(process, "platform"); Object.defineProperty(process, "platform", { ...descriptor, value: "win32" }); try { await assert.rejects(session.preview(supported.observation_id), error("preview_unsupported")); assert.equal(existsSync(join(dir, previewNamespace)), false); } finally { Object.defineProperty(process, "platform", descriptor); }
  await core.close(); await assert.rejects(session.preview(supported.observation_id), error("owner_closed"));
});

test("preview cleans failed viewers and scavenges only expired regular owner files", { timeout: 15_000 }, async (t) => {
  const dir = await mkdtemp(join(tmpdir(), "owner-preview-")), site = await fixture(), fake = await viewer(dir); const root = join(dir, previewNamespace); await writeFile(join(dir, "outside"), "keep"); await mkdir(root, { recursive: true, mode: 0o700 }); const old = join(root, "preview-00000000-0000-0000-0000-000000000000.png"); await writeFile(old, "old"); await utimes(old, 1, 1); await symlink(join(dir, "outside"), join(root, "preview-11111111-1111-1111-1111-111111111111.png"));
  const prior = process.env.PREVIEW_HANG, oldAttest = process.env.PREVIEW_ATTEST; process.env.PREVIEW_HANG = "1"; process.env.PREVIEW_ATTEST = fake.attest; const core = await owner(site.origin, { viewer: [fake.file], runtimeRoot: dir, timeout: 25 }); const session = core.session();
  t.after(async () => { if (prior === undefined) delete process.env.PREVIEW_HANG; else process.env.PREVIEW_HANG = prior; if (oldAttest === undefined) delete process.env.PREVIEW_ATTEST; else process.env.PREVIEW_ATTEST = oldAttest; await core.close(); await site.close(); await rm(dir, { recursive: true, force: true }); });
  await session.navigate(`${site.origin}/`); const observation = await session.capture(request); await assert.rejects(session.preview(observation.observation_id), error("preview_unavailable"));
  assert.equal(existsSync(old), false); assert.equal(existsSync(join(dir, "outside")), true); assert.equal((await lstat(join(root, "preview-11111111-1111-1111-1111-111111111111.png"))).isSymbolicLink(), true);
  await rm(join(root, "preview-11111111-1111-1111-1111-111111111111.png")); await assert.rejects(session.preview(observation.observation_id), error("preview_timeout"));
});


test("preview freezes viewer argv, cleans partial writes, cancels setup, and kills viewer descendants", { timeout: 15_000 }, async (t) => {
  const dir = await mkdtemp(join(tmpdir(), "owner-preview-")), site = await fixture(), fake = await viewer(dir), marker = join(dir, "descendant");
  const oldAttest = process.env.PREVIEW_ATTEST, oldMarker = process.env.PREVIEW_MARKER; process.env.PREVIEW_ATTEST = fake.attest;
  t.after(async () => { if (oldAttest === undefined) delete process.env.PREVIEW_ATTEST; else process.env.PREVIEW_ATTEST = oldAttest; if (oldMarker === undefined) delete process.env.PREVIEW_MARKER; else process.env.PREVIEW_MARKER = oldMarker; await site.close(); await rm(dir, { recursive: true, force: true }); });
  const argv = [fake.file], frozen = await owner(site.origin, { viewer: argv, runtimeRoot: dir }); const frozenSession = frozen.session(); await frozenSession.navigate(`${site.origin}/`); const frozenObservation = await frozenSession.capture(request); argv[0] = "/bin/false"; await frozenSession.preview(frozenObservation.observation_id); await frozen.close(); assert.equal(existsSync(fake.attest), true, "mutated caller argv changed the selected viewer");
  const partial = await owner(site.origin, { viewer: [fake.file], runtimeRoot: dir }); const partialSession = partial.session(); await partialSession.navigate(`${site.origin}/`); const partialObservation = await partialSession.capture({ ...request, request_id: "partial" }); const probe = await open(join(dir, "prototype"), "w"), prototype = Object.getPrototypeOf(probe), write = prototype.writeFile; await probe.close(); let injected = false; prototype.writeFile = async function (...args) { if (!injected) { injected = true; throw new Error("EIO"); } return write.apply(this, args); };
  try { await assert.rejects(partialSession.preview(partialObservation.observation_id), error("preview_failed")); } finally { prototype.writeFile = write; await partial.close(); }
  assert.equal((await readdir(join(dir, previewNamespace))).filter((name) => name.endsWith(".png")).length, 0, "partial write leaked a preview file");
  let entered, release; const gate = new Promise((resolve) => { entered = resolve; }), unblock = new Promise((resolve) => { release = resolve; }); const paused = await owner(site.origin, { viewer: [fake.file], runtimeRoot: dir }); const pausedSession = paused.session(); await pausedSession.navigate(`${site.origin}/`); const pausedObservation = await pausedSession.capture({ ...request, request_id: "paused" }); const handle = await open(join(dir, "prototype-2"), "w"), proto = Object.getPrototypeOf(handle), original = proto.writeFile; await handle.close(); let pause = true; proto.writeFile = async function (...args) { if (pause) { pause = false; entered(); await unblock; } return original.apply(this, args); };
  try { const pending = pausedSession.preview(pausedObservation.observation_id); await gate; await paused.close(); release(); await assert.rejects(pending, error("owner_closed")); } finally { proto.writeFile = original; await paused.close(); }
  assert.equal(existsSync(fake.attest), true); process.env.PREVIEW_MARKER = marker; const descendants = await owner(site.origin, { viewer: [fake.file], runtimeRoot: dir }); const descendantSession = descendants.session(); await descendantSession.navigate(`${site.origin}/`); const descendantObservation = await descendantSession.capture({ ...request, request_id: "descendant" }); await descendantSession.preview(descendantObservation.observation_id); await new Promise((resolve) => setTimeout(resolve, 180)); await descendants.close(); assert.equal(existsSync(marker), false, "viewer descendant outlived preview cleanup");
});


test("missing viewer fails promptly and leaves no preview PNG", { timeout: 15_000 }, async (t) => {
  const dir = await mkdtemp(join(tmpdir(), "owner-preview-")), site = await fixture(), core = await owner(site.origin, { viewer: [join(dir, "missing-viewer")], runtimeRoot: dir, timeout: 25 }), session = core.session();
  t.after(async () => { await core.close(); await site.close(); await rm(dir, { recursive: true, force: true }); });
  await session.navigate(`${site.origin}/`); const observation = await session.capture(request);
  await assert.rejects(Promise.race([session.preview(observation.observation_id), new Promise((_, reject) => setTimeout(() => reject(new Error("preview_hung")), 500))]), error("preview_failed"));
  assert.equal((await readdir(join(dir, previewNamespace))).filter((name) => name.endsWith(".png")).length, 0);
  await Promise.race([core.close(), new Promise((_, reject) => setTimeout(() => reject(new Error("close_hung")), 500))]);
});


test("preview preserves expired_observation when a retained record expires during its viewer", { timeout: 15_000 }, async (t) => {
  const dir = await mkdtemp(join(tmpdir(), "owner-preview-")), site = await fixture(), fake = await viewer(dir); let tick = 0; const oldAttest = process.env.PREVIEW_ATTEST, oldDelay = process.env.PREVIEW_DELAY; process.env.PREVIEW_ATTEST = fake.attest; process.env.PREVIEW_DELAY = "50";
  const core = await owner(site.origin, { viewer: [fake.file], runtimeRoot: dir }, () => tick, undefined, 100), session = core.session();
  t.after(async () => { if (oldAttest === undefined) delete process.env.PREVIEW_ATTEST; else process.env.PREVIEW_ATTEST = oldAttest; if (oldDelay === undefined) delete process.env.PREVIEW_DELAY; else process.env.PREVIEW_DELAY = oldDelay; await core.close(); await site.close(); await rm(dir, { recursive: true, force: true }); });
  await session.navigate(`${site.origin}/`); const observation = await session.capture(request); const pending = session.preview(observation.observation_id); setTimeout(() => { tick = 101; }, 10); await assert.rejects(pending, error("expired_observation"));
});
