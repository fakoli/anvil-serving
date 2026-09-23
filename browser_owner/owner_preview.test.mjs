import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { chmod, lstat, mkdir, mkdtemp, readFile, readdir, rm, symlink, utimes, writeFile } from "node:fs/promises";
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
  await writeFile(file, `#!${process.execPath}\nimport { createHash } from "node:crypto";import { lstat,readFile,writeFile } from "node:fs/promises";import { dirname } from "node:path";const path=process.argv.at(-1);if(process.env.PREVIEW_HANG){setInterval(()=>{},1000);await new Promise(()=>{})}const [data,stat,directory]=await Promise.all([readFile(path),lstat(path),lstat(dirname(path))]);await writeFile(process.env.PREVIEW_ATTEST,JSON.stringify({digest:createHash("sha256").update(data).digest("hex"),mode:stat.mode&0o777,directory:directory.mode&0o777}));`);
  await chmod(file, 0o700); return { file, attest };
}
function screenshotHook() {
  let image, page;
  return { image: () => image, page: () => page, prepare: async (browser) => { const context = browser.newContext.bind(browser); browser.newContext = async (...args) => { const value = await context(...args); const newPage = value.newPage.bind(value); value.newPage = async (...pageArgs) => { const output = await newPage(...pageArgs), screenshot = output.screenshot.bind(output); page = output; output.screenshot = async (...screenshotArgs) => { image = await screenshot(...screenshotArgs); return image; }; return output; }; return value; }; } };
}
async function owner(origin, preview, clock, prepare) {
  return createBrowserOwner({ launch: async () => { const browser = await chromium.launch({ executablePath: executable, headless: true, args: ["--disable-gpu"] }); await prepare?.(browser); return browser; }, documentOrigins: [origin], subresourceOrigins: [origin], limits: { ttl: 100, timeout: 5_000 }, preview, clock });
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
  const hook = screenshotHook(); const core = await owner(site.origin, { viewer: [fake.file], runtimeRoot: dir }, () => tick, hook.prepare); const session = core.session();
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
