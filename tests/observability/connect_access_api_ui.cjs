"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const file = process.argv[2];
if (!file) throw new Error("expected the Access API module path");
let source = fs.readFileSync(file, "utf8");
source = source.replace(
  'import { APIError } from "./api.js";',
  "class APIError extends Error { constructor(code, message, status = 0) { super(message); this.code = code; this.status = status; } }",
);
source = source.replaceAll("export ", "");

let pending = [];
const context = vm.createContext({
  URL,
  Uint8Array,
  TextDecoder,
  AbortController,
  DOMException,
  setTimeout,
  clearTimeout,
  window: { location: new URL("https://dash.example.test/observatory/") },
  fetch: async (_url, options) => {
    const next = pending.shift();
    assert.ok(next, "unexpected Access request");
    next.options = options;
    return next.response;
  },
});
vm.runInContext(source, context, { filename: file, timeout: 2000 });

const encode = (value) => new TextEncoder().encode(JSON.stringify(value));
function streamed(chunks, { status = 200, length = null } = {}) {
  let offset = 0;
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get(name) {
        if (name.toLowerCase() === "content-type") return "application/json";
        if (name.toLowerCase() === "content-length") return length;
        return null;
      },
    },
    body: {
      getReader() {
        return {
          async read() {
            if (offset >= chunks.length) return { done: true };
            return { done: false, value: chunks[offset++] };
          },
          async cancel() {},
          releaseLock() {},
        };
      },
    },
  };
}
function access(kind, changes = {}) {
  return {
    schema: "anvil-connect.access/v1",
    kind,
    items: [],
    next_cursor: null,
    csrf: "c".repeat(32),
    current_principal: "operator-fixture",
    current_session: "browser-fixture",
    ...changes,
  };
}

(async () => {
  assert.equal(
    context.configureConnectAccess({
      authentication_mode: "connect",
      connect_access_path: "/observatory/_anvil-connect/access",
    }),
    true,
  );
  pending.push({ response: streamed([new Uint8Array(1024 * 1024), new Uint8Array(1)]) });
  await assert.rejects(
    () => context.readConnectAccess("users", null),
    (error) => error.code === "access-unavailable",
  );
  pending.push({ response: streamed([encode(access("users", { next_cursor: "bad\n" }))]) });
  await assert.rejects(
    () => context.readConnectAccess("users", null),
    (error) => error.code === "access-invalid",
  );
  pending.push({ response: streamed([encode(access("users", { csrf: "bad\n" }))]) });
  await assert.rejects(
    () => context.readConnectAccess("users", null),
    (error) => error.code === "access-invalid",
  );
  pending.push({ response: streamed([encode(access("users", { items: Array.from({ length: 51 }, () => ({})) }))]) });
  await assert.rejects(
    () => context.readConnectAccess("users", null),
    (error) => error.code === "access-invalid",
  );
  const inventory = { response: streamed([encode(access("users"))]) };
  pending.push(inventory);
  await context.readConnectAccess("users", null);
  const mutation = {
    response: streamed([encode({
      schema: "anvil-connect.access/v1",
      applied: true,
      request_id: "request-fixture",
    })]),
  };
  pending.push(mutation);
  await context.mutateConnectAccess({ action: "human-update", request_id: "request-fixture" });
  assert.equal(mutation.options.headers["X-CSRF-Token"], "c".repeat(32));
  assert.equal(JSON.parse(mutation.options.body).csrf, "c".repeat(32));
  assert.equal(mutation.options.redirect, "error");
  assert.equal(mutation.options.credentials, "same-origin");
  assert.equal(pending.length, 0);
})().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
