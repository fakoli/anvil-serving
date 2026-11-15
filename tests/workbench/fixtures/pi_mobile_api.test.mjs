import assert from "node:assert/strict";
import test from "node:test";
import { registerHooks } from "node:module";
import { createMobilePiHandler, mobilePiAuthorized } from "./mobile-pi-api.ts";

const token = "synthetic-bridge-token";
const requestId = "12345678-1234-4234-9234-123456789abc";
const base = "http://127.0.0.1/api/mobile-pi";
function request(path, body, authorization = `Bearer ${token}`) {
  return new Request(base + path, { method: body === undefined ? "GET" : "POST",
    headers: { authorization, "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: typeof body === "string" ? body : JSON.stringify(body) }) });
}
function owner() {
  const calls = [];
  return { calls, async read(kind, req, id) {
    calls.push([kind, id, new URL(req.url).search]);
    return Response.json({ sessions: [{ id: "session-1" }], sessionId: id, context: [{ text: "native" }] });
  }, async command(id, command) {
    calls.push(["command", id, command]);
    return Response.json({ success: true, data: { accepted: true } });
  } };
}
test("bearer boundary fails closed even with native password disabled", async () => {
  for (const auth of [null, "", "Basic native-password", "Bearer wrong", `bearer ${token}`]) {
    assert.equal(mobilePiAuthorized(auth, token), false);
  }
  assert.equal(mobilePiAuthorized(`Bearer ${token}`, ""), false);
  assert.equal(mobilePiAuthorized(`Bearer ${token}`, token), true);
  const own = owner();
  const handler = createMobilePiHandler(own, () => token);
  assert.equal((await handler(request("/sessions", undefined, ""))).status, 404);
  assert.equal(own.calls.length, 0);
});
test("explicit reads preserve native payload and canonical bounded queries", async () => {
  const own = owner(); const handle = createMobilePiHandler(own, () => token);
  for (const path of ["/sessions?summary=1", "/sessions/session-1?force=1&tail=50&tree=summary&deferThinking=1&deferMedia=1",
    "/sessions/session-1/context?before=entry-1&tail=50", "/sessions/session-1/state", "/sessions/session-1/events"]) {
    assert.equal((await handle(request(path))).status, 200);
  }
  assert.equal(own.calls.length, 5);
  assert.equal(own.calls[0][2], "?summary=1");
  assert.match(own.calls[2][2], /deferThinking=1/);
  const response = await handle(request("/sessions/session-1"));
  assert.equal((await response.json()).context[0].text, "native");
});
test("unknown routes, query authority and unsafe ids never reach owner", async () => {
  const own = owner(); const handle = createMobilePiHandler(own, () => token);
  for (const path of ["/sessions/new", "/sessions/%2e%2e%2fsecret", "/sessions/a%5cb", `/sessions/${"a".repeat(161)}`,
    "/sessions/a/fork", "/sessions/a?cwd=/tmp", "/sessions?summary=0", "/sessions/a?tail=1000",
    "/sessions/a?tail=50&tail=50", "/sessions/a/context?before=../secret", "/models"]) {
    assert.ok((await handle(request(path))).status >= 400, path);
  }
  assert.equal(own.calls.length, 0);
});
test("only exact prompt and abort bodies dispatch, correlation is stripped", async () => {
  const own = owner(); const handle = createMobilePiHandler(own, () => token);
  assert.equal((await handle(request("/sessions/a/command", { type: "prompt", message: "hello", requestId }))).status, 200);
  assert.deepEqual(own.calls[0], ["command", "a", { type: "prompt", message: "hello" }]);
  assert.equal((await handle(request("/sessions/a/command", { type: "abort" }))).status, 200);
  for (const body of [{ type: "bash", command: "ls" }, { type: "prompt", message: "hi", cwd: "/tmp" },
    { type: "prompt", message: "hi", model: "other" }, { type: "prompt", message: [] },
    { type: "prompt", message: " " }, { type: "abort", message: "hi" }, { type: "prompt", message: "hi", requestId: "bad" },
    { type: "prompt", message: "é".repeat(16385) }, "invalid json", " ".repeat(65537)]) {
    assert.ok((await handle(request("/sessions/a/command", body))).status >= 400);
  }
  assert.equal(own.calls.length, 2);
});
test("same request id shares in-flight result; conflicts never dispatch twice", async () => {
  let release; let count = 0;
  const pending = new Promise(resolve => { release = resolve; });
  const handle = createMobilePiHandler({ ...owner(), async command() { count++; await pending; return Response.json({ success: true }); } }, () => token);
  const body = { type: "prompt", message: "hello", requestId };
  const a = handle(request("/sessions/a/command", body));
  const b = handle(request("/sessions/a/command", body));
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(count, 1);
  assert.equal((await handle(request("/sessions/a/command", { ...body, message: "other" }))).status, 409);
  release();
  assert.equal((await a).status, 200); assert.equal((await b).status, 200);
  assert.equal((await handle(request("/sessions/a/command", body))).status, 200);
  assert.equal(count, 1);
});
test("owner errors never expose secret or stack and are never retried", async () => {
  const own = owner(); let calls = 0;
  own.read = async () => { throw new Error("secret stack /private/path"); };
  own.command = async () => { calls++; return Response.json({ error: "secret stack /private/path" }, { status: 500 }); };
  const handle = createMobilePiHandler(own, () => token);
  assert.doesNotMatch(await (await handle(request("/sessions/a"))).text(), /secret|stack|private/);
  const body = { type: "prompt", message: "hello", requestId };
  const response = await handle(request("/sessions/a/command", body));
  assert.equal(response.status, 500);
  assert.doesNotMatch(await response.text(), /secret|stack|private/);
  await handle(request("/sessions/a/command", body)); assert.equal(calls, 1);
});

test("packaged route binds native read handlers, restores only an existing owner, and reuses the live wrapper", async () => {
  const calls = [];
  let live = null;
  let listener;
  const session = { isAlive: () => true, isStreaming: false, streamingMessage: null,
    onEvent(callback) { listener = callback; return () => {}; } };
  globalThis.__mobileSyntheticOwner = {
    async read(kind, req, params) { calls.push([kind, params ? (await params.params).id : null]); return Response.json({ native: kind }); },
    async command(req, params) { calls.push(["command", (await params.params).id, await req.json()]); return Response.json({ success: true }); },
    getRpcSession(id) { calls.push(["get", id]); return live; },
    async resolveSessionPath(id) { calls.push(["resolve", id]); return id === "missing" ? null : "/synthetic/session.jsonl"; },
    async startRpcSession(...args) { calls.push(["start", ...args]); live = session; return { session }; },
    createAgentEventStream(req, id, promise) {
      calls.push(["stream", id]);
      return new ReadableStream({ async start(controller) {
        const facade = await promise;
        assert.equal(facade.isAlive(), true);
        assert.equal(facade.isStreaming, false);
        assert.equal(facade.streamingMessage, null);
        const received = [];
        facade.onEvent(event => received.push(event));
        listener({ type: "prompt_error", errorMessage: "secret stack" });
        assert.deepEqual(received[0], { type: "prompt_error", errorMessage: "Agent request failed" });
        listener({ type: "extension_error", extensionPath: "/synthetic/private/secret-extension.ts",
          event: "secret hook", error: "synthetic-secret-key\nError stack at private/path", extensionName: "secret name" });
        assert.deepEqual(received[1], { type: "extension_error", error: "Extension request failed" });
        assert.doesNotMatch(JSON.stringify(received[1]), /secret|private|stack/);
        const nativeEvent = { type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "native text" } };
        listener(nativeEvent);
        assert.equal(received[2], nativeEvent);
        controller.enqueue(new TextEncoder().encode('data: {"type":"connected"}\n\n')); controller.close();
      } });
    },
  };
  const native = {
    "@/app/api/sessions/route": "sessions", "@/app/api/sessions/[id]/route": "detail",
    "@/app/api/sessions/[id]/context/route": "context", "@/app/api/sessions/[id]/state/route": "state",
  };
  const hooks = registerHooks({ resolve(specifier, context, next) {
    let source;
    if (Object.hasOwn(native, specifier)) source = `export const GET = (req,params) => globalThis.__mobileSyntheticOwner.read(${JSON.stringify(native[specifier])},req,params);`;
    if (specifier === "@/app/api/agent/[id]/route") source = "export const POST = (req,params) => globalThis.__mobileSyntheticOwner.command(req,params);";
    if (specifier === "@/lib/rpc-manager") source = "export const getRpcSession = id => globalThis.__mobileSyntheticOwner.getRpcSession(id); export const startRpcSession = (...args) => globalThis.__mobileSyntheticOwner.startRpcSession(...args);";
    if (specifier === "@/lib/session-reader") source = "export const resolveSessionPath = id => globalThis.__mobileSyntheticOwner.resolveSessionPath(id);";
    if (specifier === "@/lib/agent-event-stream") source = "export const createAgentEventStream = (...args) => globalThis.__mobileSyntheticOwner.createAgentEventStream(...args);";
    if (specifier === "@/lib/mobile-pi-api") return { url: new URL("./mobile-pi-api.ts", import.meta.url).href, shortCircuit: true };
    if (source) return { url: "data:text/javascript," + encodeURIComponent(source), shortCircuit: true };
    return next(specifier, context);
  } });
  const priorToken = process.env.PI_WEB_WORKBENCH_BRIDGE_TOKEN;
  process.env.PI_WEB_WORKBENCH_BRIDGE_TOKEN = token;
  try {
    const route = await import("./mobile-route.ts");
    for (const path of ["/sessions", "/sessions/a", "/sessions/a/context", "/sessions/a/state"]) assert.equal((await route.GET(request(path))).status, 200);
    assert.deepEqual(calls.slice(0, 4), [["sessions", null], ["detail", "a"], ["context", "a"], ["state", "a"]]);
    const first = await route.GET(request("/sessions/a/events"));
    assert.equal(first.headers.get("content-type"), "text/event-stream"); assert.match(await first.text(), /connected/);
    assert.deepEqual(calls.find(row => row[0] === "start"), ["start", "a", "/synthetic/session.jsonl", undefined]);
    await (await route.GET(request("/sessions/a/events"))).text();
    assert.equal(calls.filter(row => row[0] === "start").length, 1);
    live = null;
    assert.equal((await route.GET(request("/sessions/missing/events"))).status, 404);
    assert.equal(calls.filter(row => row[0] === "start").length, 1);
    await route.POST(request("/sessions/a/command", { type: "prompt", message: "hi", requestId }));
    assert.deepEqual(calls.at(-1), ["command", "a", { type: "prompt", message: "hi" }]);
  } finally {
    hooks.deregister(); delete globalThis.__mobileSyntheticOwner;
    if (priorToken === undefined) delete process.env.PI_WEB_WORKBENCH_BRIDGE_TOKEN; else process.env.PI_WEB_WORKBENCH_BRIDGE_TOKEN = priorToken;
  }
});
