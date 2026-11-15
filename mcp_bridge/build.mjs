import { build } from "esbuild";
import { readFile, readdir, writeFile } from "node:fs/promises";

const outfile = "../anvil_serving/_node/mcp_proxy.mjs";
const built = await build({
  entryPoints: ["src/index.ts"],
  outfile,
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node20",
  sourcemap: false,
  legalComments: "inline",
  metafile: true,
  banner: {
    js: "#!/usr/bin/env node",
  },
});

// Some bundled dependency comments contain blank lines with spaces. Normalize
// them so the reviewed package asset passes the repository whitespace gate.
const bundled = await readFile(outfile, "utf8");
await writeFile(outfile, bundled.replace(/[ \t]+$/gm, ""), "utf8");

const packages = new Set();
for (const input of Object.keys(built.metafile.inputs)) {
  const parts = input.replaceAll("\\", "/").split("/");
  const index = parts.lastIndexOf("node_modules");
  if (index < 0) continue;
  packages.add(parts.slice(0, index + (parts[index + 1].startsWith("@") ? 3 : 2)).join("/"));
}
const notices = [await readFile("THIRD_PARTY_NOTICES.md", "utf8")];
for (const directory of [...packages].sort()) {
  const metadata = JSON.parse(await readFile(`${directory}/package.json`, "utf8"));
  const files = (await readdir(directory)).filter((name) => /^(licen[cs]e|copying|notice)(\..*)?$/i.test(name)).sort();
  if (files.length === 0) throw new Error(`Missing license text: ${metadata.name}`);
  notices.push(`\n---\n${metadata.name} ${metadata.version} (${metadata.license})\n`);
  for (const name of files) notices.push(await readFile(`${directory}/${name}`, "utf8"));
}
await writeFile("../anvil_serving/_node/THIRD_PARTY_NOTICES.txt", notices.join("\n").replace(/[ \t]+$/gm, ""), "utf8");
