#!/usr/bin/env node

import { createHash } from "node:crypto";
import { lstat, readFile, writeFile } from "node:fs/promises";

const [, , mode, proofPath, readyPath, imagePath] = process.argv;

if (!(["attest", "block"].includes(mode)) || !proofPath || !readyPath || !imagePath) {
  process.exitCode = 2;
} else {
  if (readyPath !== "-") await writeFile(readyPath, "ready", { flag: "wx" });
  if (mode === "block") {
    // The owner must terminate this fixture. Keeping the event loop live makes
    // viewer admission observable without relying on a scheduler-sensitive delay.
    await new Promise((resolve) => setTimeout(resolve, 60_000));
  } else {
    const [image, stat] = await Promise.all([readFile(imagePath), lstat(imagePath)]);
    await writeFile(proofPath, JSON.stringify({
      digest: createHash("sha256").update(image).digest("hex"),
      mode: stat.mode & 0o777,
    }));
  }
}
