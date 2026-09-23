import { createHash, randomUUID } from "node:crypto";
import { chmod, lstat, mkdir, open, readdir, rm } from "node:fs/promises";
import { isAbsolute, join } from "node:path";
import { spawn } from "node:child_process";

const namespace = "anvil-owner-preview-v1";
const fileName = /^preview-[0-9a-f-]{36}\.png$/;
const fail = (code) => { const error = new Error(code); error.code = code; throw error; };

export function createOwnerPreview(policy, { ttl }) {
  if (policy === undefined || policy === false) return null;
  if (!policy || typeof policy !== "object" || Array.isArray(policy) || Object.getPrototypeOf(policy) !== Object.prototype || Object.keys(policy).some((key) => !["viewer", "runtimeRoot", "timeout"].includes(key))) fail("invalid_preview_policy");
  const { viewer, runtimeRoot, timeout = 5_000 } = policy;
  if (!Array.isArray(viewer) || !viewer.length || viewer.some((part) => typeof part !== "string" || !part.length || part.includes("\0")) || !isAbsolute(viewer[0]) || typeof runtimeRoot !== "string" || !isAbsolute(runtimeRoot) || runtimeRoot.includes("\0") || !Number.isInteger(timeout) || timeout < 1 || timeout > 10_000) fail("invalid_preview_policy");
  const argv = Object.freeze([...viewer]), directory = join(runtimeRoot, namespace);
  const ready = async () => {
    await mkdir(directory, { recursive: true, mode: 0o700 }).catch(() => fail("preview_unavailable"));
    const info = await lstat(directory).catch(() => null);
    if (!info || !info.isDirectory() || info.isSymbolicLink()) fail("preview_unavailable");
    await chmod(directory, 0o700).catch(() => fail("preview_unavailable"));
    await scavenge(directory, Math.max(ttl, timeout));
  };
  return Object.freeze({
    async show(record, validate) {
      if (process.platform === "win32") fail("preview_unsupported");
      await validate(); await ready(); await validate();
      const path = join(directory, `preview-${randomUUID()}.png`);
      let child, childExit, handle, timer, complete = false, cancelled = false;
      const kill = () => { if (child?.pid) process.kill(-child.pid, "SIGKILL"); };
      const cleanup = async () => {
        if (complete) return; complete = true; cancelled = true; clearTimeout(timer); try { kill(); } catch {} await childExit?.catch(() => {}); await handle?.close().catch(() => {}); await rm(path, { force: true }).catch(() => {});
      };
      try {
        handle = await open(path, "wx", 0o600).catch(() => fail("preview_unavailable"));
        record.previewCleanup = cleanup;
        await validate(); if (cancelled) fail("owner_closed");
        await handle.writeFile(record.image); await chmod(path, 0o600); await handle.close(); handle = undefined;
        if (cancelled) fail("owner_closed"); await validate(); if (cancelled) fail("owner_closed");
        await new Promise((resolve, reject) => {
          try { child = spawn(argv[0], [...argv.slice(1), path], { shell: false, stdio: "ignore", windowsHide: true, detached: true }); } catch { reject(new Error("preview_failed")); return; }
          let done; childExit = new Promise((resolveExit) => { done = resolveExit; });
          child.once("exit", (code, signal) => { done(); code === 0 && !signal ? resolve() : reject(new Error("preview_failed")); });
          timer = setTimeout(() => { reject(new Error("preview_timeout")); try { kill(); } catch {} }, timeout);
          child.once("error", () => { done(); reject(new Error("preview_failed")); });
        });
        await validate(); if (cancelled) fail("owner_closed");
      } catch (error) { fail(cancelled ? "owner_closed" : (error?.code || (error?.message === "preview_timeout" ? "preview_timeout" : "preview_failed")));
      } finally { await cleanup(); if (record.previewCleanup === cleanup) delete record.previewCleanup; }
      return Object.freeze({ status: "shown" });
    },
    async close(record) { await record.previewCleanup?.(); },
  });
}

async function scavenge(directory, age) {
  const entries = await readdir(directory, { withFileTypes: true }).catch(() => fail("preview_unavailable"));
  const cutoff = Date.now() - age;
  for (const entry of entries) {
    if (!fileName.test(entry.name)) continue;
    if (entry.isSymbolicLink() || !entry.isFile()) fail("preview_unavailable");
    const path = join(directory, entry.name), info = await lstat(path).catch(() => null);
    if (!info || info.isSymbolicLink() || !info.isFile()) fail("preview_unavailable");
    if (info.mtimeMs < cutoff) await rm(path, { force: true }).catch(() => {});
  }
}

export const previewNamespace = namespace;
