import { createHash, randomUUID } from "node:crypto";
import { chmod, lstat, mkdir, open, readdir, rm } from "node:fs/promises";
import { basename, isAbsolute, join } from "node:path";
import { spawn } from "node:child_process";

const namespace = "anvil-owner-preview-v1";
const fileName = /^preview-[0-9a-f-]{36}\.png$/;
const fail = (code) => { const error = new Error(code); error.code = code; throw error; };

export function createOwnerPreview(policy, { ttl }) {
  if (policy === undefined || policy === false) return null;
  if (!policy || typeof policy !== "object" || Array.isArray(policy) || Object.getPrototypeOf(policy) !== Object.prototype || Object.keys(policy).some((key) => !["viewer", "runtimeRoot", "timeout"].includes(key))) fail("invalid_preview_policy");
  const { viewer, runtimeRoot, timeout = 5_000 } = policy;
  if (!Array.isArray(viewer) || !viewer.length || viewer.some((part) => typeof part !== "string" || !part.length || part.includes("\0")) || !isAbsolute(viewer[0]) || typeof runtimeRoot !== "string" || !isAbsolute(runtimeRoot) || runtimeRoot.includes("\0") || !Number.isInteger(timeout) || timeout < 1 || timeout > 10_000) fail("invalid_preview_policy");
  const directory = join(runtimeRoot, namespace);
  const ready = async () => {
    await mkdir(directory, { recursive: true, mode: 0o700 }).catch(() => fail("preview_unavailable"));
    const info = await lstat(directory).catch(() => null);
    if (!info || !info.isDirectory() || info.isSymbolicLink()) fail("preview_unavailable");
    await chmod(directory, 0o700).catch(() => fail("preview_unavailable"));
    await scavenge(directory, ttl);
  };
  return Object.freeze({
    async show(record) {
      if (process.platform === "win32") fail("preview_unsupported");
      await ready();
      if (!record.image || createHash("sha256").update(record.image).digest("hex") !== record.digest) fail("preview_invalid");
      const path = join(directory, `preview-${randomUUID()}.png`);
      const handle = await open(path, "wx", 0o600).catch(() => fail("preview_unavailable"));
      try { await handle.writeFile(record.image); await chmod(path, 0o600); } finally { await handle.close().catch(() => {}); }
      let child, timer, complete = false;
      const cleanup = async () => {
        if (complete) return; complete = true; clearTimeout(timer); child?.kill("SIGKILL"); await rm(path, { force: true }).catch(() => {});
      };
      record.previewCleanup = cleanup;
      try {
        await new Promise((resolve, reject) => {
          try { child = spawn(viewer[0], [...viewer.slice(1), path], { shell: false, stdio: "ignore", windowsHide: true }); } catch { reject(new Error("preview_failed")); return; }
          timer = setTimeout(() => { reject(new Error("preview_timeout")); child.kill("SIGKILL"); }, timeout);
          child.once("error", () => reject(new Error("preview_failed")));
          child.once("exit", (code, signal) => code === 0 && !signal ? resolve() : reject(new Error("preview_failed")));
        });
      } catch (error) { fail(error.message === "preview_timeout" ? "preview_timeout" : "preview_failed");
      } finally { await cleanup(); if (record.previewCleanup === cleanup) delete record.previewCleanup; }
      return Object.freeze({ status: "shown" });
    },
    async close(record) { await record.previewCleanup?.(); },
  });
}

async function scavenge(directory, ttl) {
  const entries = await readdir(directory, { withFileTypes: true }).catch(() => fail("preview_unavailable"));
  const cutoff = Date.now() - Math.min(ttl, 60_000);
  for (const entry of entries) {
    if (!fileName.test(entry.name)) continue;
    if (entry.isSymbolicLink() || !entry.isFile()) fail("preview_unavailable");
    const path = join(directory, entry.name), info = await lstat(path).catch(() => null);
    if (!info || info.isSymbolicLink() || !info.isFile()) fail("preview_unavailable");
    if (info.mtimeMs < cutoff) await rm(path, { force: true }).catch(() => {});
  }
}

export const previewNamespace = namespace;
