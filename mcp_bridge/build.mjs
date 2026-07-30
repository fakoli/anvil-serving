import { build } from "esbuild";
import { readFile, writeFile } from "node:fs/promises";

const outfile = "../anvil_serving/_node/mcp_proxy.mjs";
await build({
  entryPoints: ["src/index.ts"],
  outfile,
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node20",
  sourcemap: false,
  legalComments: "inline",
  banner: {
    js: "#!/usr/bin/env node",
  },
});

// Some bundled dependency comments contain blank lines with spaces. Normalize
// them so the reviewed package asset passes the repository whitespace gate.
const bundled = await readFile(outfile, "utf8");
await writeFile(outfile, bundled.replace(/[ \t]+$/gm, ""), "utf8");
