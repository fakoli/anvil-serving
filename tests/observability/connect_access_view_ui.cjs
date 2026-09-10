"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const file = process.argv[2];
if (!file) throw new Error("expected the Access view module path");
let source = fs.readFileSync(file, "utf8");
source = source.replace(
  'import { button, el, field, heading, kv, notice, timestamp } from "./common.js";',
  `const el = (tag, attrs = {}, ...children) => ({ tag, attrs, children: children.flat(), value: attrs.value });
   const button = (label, onClick, className = "", disabled = false) => ({ tag: "button", label, onClick, className, disabled });
   const field = (label, control) => ({ tag: "field", label, control });
   const heading = (title, description) => ({ tag: "heading", title, description });
   const kv = (items) => ({ tag: "kv", items });
   const notice = (message) => ({ tag: "notice", message });
   const timestamp = (value) => ({ tag: "time", value });`,
);
source = source.replace(
  'import { mutateConnectAccess, readConnectAccess } from "./connect_access_api.js";',
  "const mutateConnectAccess = async () => { globalThis.mutations += 1; }; const readConnectAccess = async () => globalThis.inventory;",
);
source = source.replaceAll("export ", "");
const context = vm.createContext({
  globalThis: null,
  mutations: 0,
  inventory: null,
  crypto: { randomUUID: () => "request" },
  window: { confirm: () => true },
  Set,
});
context.globalThis = context;
vm.runInContext(source, context, { filename: file, timeout: 2000 });

function collect(node, result = []) {
  if (!node || typeof node !== "object") return result;
  if (node.tag === "button") result.push(node);
  for (const child of node.children || []) collect(child, result);
  return result;
}
const ctx = { signal: undefined, zone: "UTC", announce() {}, refresh() {} };

(async () => {
  context.inventory = {
    schema: "anvil-connect.access/v1",
    kind: "users",
    items: [{ id: "human:" + "a".repeat(64), generation: "1", resources: [] }],
    next_cursor: null,
    current_principal: "human:" + "b".repeat(64),
    current_session: "current",
  };
  let tree = await context.connectAccessView(ctx);
  let buttons = collect(tree);
  for (const label of ["Save resources", "Disable access"])
    assert.equal(buttons.find((item) => item.label === label)?.disabled, true, `${label} must be disabled for malformed users`);
  await buttons.find((item) => item.label === "Save resources").onClick();
  assert.equal(context.mutations, 0, "malformed user must not invoke mutation");

  buttons.find((item) => item.label === "Connect sessions" || item.attrs?.text === "Connect sessions").attrs.onClick();
  context.inventory = {
    schema: "anvil-connect.access/v1",
    kind: "sessions",
    items: [{ id: "session-fixture", type: "browser", principal: "human:" + "a".repeat(64), resource: "observatory", status: "authorized" }],
    next_cursor: null,
    current_principal: "human:" + "b".repeat(64),
    current_session: "current",
  };
  tree = await context.connectAccessView(ctx);
  buttons = collect(tree);
  assert.equal(buttons.find((item) => item.label === "Disconnect")?.disabled, true, "malformed session must be mutation-disabled");
  await buttons.find((item) => item.label === "Disconnect").onClick();
  assert.equal(context.mutations, 0, "malformed session must not invoke mutation");
})().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
