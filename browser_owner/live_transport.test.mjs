import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { createLiveBufferedTransport, LiveTransportError } from "./live_transport.mjs";

const publicLookup = async () => [{ address: "93.184.216.34", family: 4 }];
const documentUrl = "https://public.example/projects";
const error = (code) => (value) => value instanceof LiveTransportError && value.code === code;

function response({ status = 200, headers = {}, body = Buffer.from("ok"), deferred = false } = {}) {
  const value = new EventEmitter();
  value.statusCode = status;
  value.headers = Object.fromEntries(Object.entries(headers).map(([key, item]) => [key.toLowerCase(), item]));
  value.rawHeaders = Object.entries(headers).flatMap(([key, item]) => [key, String(item)]);
  value.destroyed = false;
  value.destroy = () => { value.destroyed = true; };
  value.start = () => { if (!value.destroyed) { value.emit("data", body); value.emit("end"); } };
  if (!deferred) queueMicrotask(value.start);
  return value;
}

function requester(plans, seen) {
  return (url, options, callback) => {
    const plan = plans.shift() || {};
    const request = new EventEmitter();
    request.destroyed = false;
    request.destroy = () => { request.destroyed = true; plan.response?.destroy(); };
    request.end = () => {
      seen.push({ url: String(url), options, request });
      queueMicrotask(() => {
        if (request.destroyed) return;
        const next = plan.response || response(plan);
        plan.response = next;
        callback(next);
      });
    };
    return request;
  };
}

function route({ url = documentUrl, method = "GET", navigation = true, body = null } = {}) {
  const result = { fulfilled: [], aborted: 0, continued: 0 };
  return {
    result,
    request: () => ({ url: () => url, method: () => method, postData: () => body, isNavigationRequest: () => navigation }),
    fulfill: async (value) => { result.fulfilled.push(value); },
    abort: async () => { result.aborted += 1; },
    continue: async () => { result.continued += 1; },
  };
}

async function transport(plans = []) {
  const seen = [];
  return { value: await createLiveBufferedTransport({ documentUrls: [documentUrl], lookup: publicLookup, request: requester(plans, seen) }), seen };
}

test("live transport validates an exact public HTTPS document policy before use", async () => {
  await assert.rejects(createLiveBufferedTransport({ documentUrls: ["http://public.example/projects"], lookup: publicLookup }), error("invalid_live_transport_policy"));
  for (const result of [{ address: "127.0.0.1", family: 4 }, { address: "100.64.0.1", family: 4 }, { address: "198.18.0.1", family: 4 }, { address: "203.0.113.1", family: 4 }, { address: "::ffff:127.0.0.1", family: 6 }, { address: "ff02::1", family: 6 }, { address: "2001::1", family: 6 }, { address: "2001:0000::1", family: 6 }, { address: "2001:db8::1", family: 6 }, { address: "2001:0db8::1", family: 6 }, { address: "2002::1", family: 6 }, { address: "2606:4700::1111", family: 4 }]) {
    await assert.rejects(createLiveBufferedTransport({ documentUrls: [documentUrl], lookup: async () => [result] }), error("dns_not_public"));
  }
  const v6 = await createLiveBufferedTransport({ documentUrls: [documentUrl], lookup: async () => [{ address: "2606:4700::1111", family: 6 }] }); await v6.close();
  await assert.rejects(createLiveBufferedTransport({ documentUrls: [documentUrl, "https://other.example/projects"], lookup: publicLookup }), error("invalid_live_transport_policy"));
});

test("live transport admits only fixed GET navigation and same-origin assets with fixed headers", async (t) => {
  const { value, seen } = await transport(); t.after(() => value.close());
  const document = route(); await value.handle(document);
  assert.equal(document.result.fulfilled.length, 1); assert.equal(document.result.continued, 0);
  assert.equal(seen[0].options.method, "GET"); assert.equal(seen[0].options.headers["accept-encoding"], "identity"); assert.equal(seen[0].options.headers.cookie, undefined); assert.equal(seen[0].options.headers.authorization, undefined); assert.equal(seen[0].options.headers.referer, undefined);
  const asset = route({ url: "https://public.example/assets/site.css", navigation: false }); await value.handle(asset); assert.equal(asset.result.fulfilled.length, 1);
  for (const refused of [route({ url: "https://public.example/other" }), route({ url: "https://other.example/projects", navigation: false }), route({ method: "POST" }), route({ body: "x" })]) {
    await value.handle(refused); assert.equal(refused.result.aborted, 1); assert.equal(refused.result.continued, 0);
  }
});

test("live transport re-vets DNS for every connection and rejects rebinding before response", async (t) => {
  let lookups = 0;
  const lookup = async () => [lookups++ === 0 ? { address: "93.184.216.34", family: 4 } : { address: "127.0.0.1", family: 4 }];
  const request = (url, options, callback) => {
    const outgoing = new EventEmitter(); outgoing.destroy = () => {};
    outgoing.end = () => options.lookup(url.hostname, { all: true }, (error, results) => {
      if (error) return outgoing.emit("error", error);
      assert.deepEqual(results, [{ address: "93.184.216.34", family: 4 }]); callback(response());
    });
    return outgoing;
  };
  const value = await createLiveBufferedTransport({ documentUrls: [documentUrl], lookup, request }); t.after(() => value.close());
  const current = route(); await value.handle(current);
  assert.equal(lookups, 2); assert.equal(current.result.fulfilled.length, 0); assert.equal(current.result.aborted, 1);
});

test("live transport refuses redirects, encodings, oversized bodies, and aggregate exhaustion before fulfill", async (t) => {
  const plans = [
    { status: 302 },
    { headers: { "content-encoding": "gzip" } },
    { headers: { "content-length": String(2 * 1024 * 1024 + 1) } },
    ...Array.from({ length: 6 }, () => ({ body: Buffer.alloc(2 * 1024 * 1024) })),
    { body: Buffer.from("x") },
  ];
  const { value } = await transport(plans); t.after(() => value.close());
  for (let index = 0; index < 3; index += 1) { const current = route(); await value.handle(current); assert.equal(current.result.fulfilled.length, 0); assert.equal(current.result.aborted, 1); }
  for (let index = 0; index < 6; index += 1) { const current = route(); await value.handle(current); assert.equal(current.result.fulfilled.length, 1); }
  const exhausted = route(); await value.handle(exhausted); assert.equal(exhausted.result.fulfilled.length, 0); assert.equal(exhausted.result.aborted, 1);
});

test("live transport caps response headers and its sixty-four admitted requests", async (t) => {
  const headerOverflow = route(); const manyHeaders = Object.fromEntries(Array.from({ length: 33 }, (_, index) => [`x-${index}`, "v"]));
  const { value } = await transport([{ headers: manyHeaders }, { headers: { "x-large": "x".repeat(16 * 1024 + 1) } }]); t.after(() => value.close());
  await value.handle(headerOverflow); assert.equal(headerOverflow.result.aborted, 1);
  const oversized = route(); await value.handle(oversized); assert.equal(oversized.result.aborted, 1);
  for (let index = 0; index < 62; index += 1) { const current = route(); await value.handle(current); assert.equal(current.result.fulfilled.length, 1); }
  const capped = route(); await value.handle(capped); assert.equal(capped.result.aborted, 1);
});

test("interleaved raw chunks reserve aggregate capacity before either response is retained", async (t) => {
  const fill = [...Array.from({ length: 5 }, () => ({ body: Buffer.alloc(2 * 1024 * 1024) })), { body: Buffer.alloc(1024 * 1024) }];
  const first = response({ deferred: true }), second = response({ deferred: true }); const { value } = await transport([...fill, { response: first }, { response: second }]); t.after(() => value.close());
  for (let index = 0; index < fill.length; index += 1) { const current = route(); await value.handle(current); assert.equal(current.result.fulfilled.length, 1); }
  const left = route(), right = route(); const leftWork = value.handle(left), rightWork = value.handle(right);
  await new Promise((resolve) => setImmediate(resolve));
  first.emit("data", Buffer.alloc(1024 * 1024)); first.emit("end"); second.emit("data", Buffer.alloc(1024 * 1024)); second.emit("end");
  await Promise.all([leftWork, rightWork]);
  assert.equal(left.result.fulfilled.length, 1); assert.equal(right.result.fulfilled.length, 0); assert.equal(right.result.aborted, 1);
});

test("close destroys pending requests, invokes the owner closer, and cannot late-fulfill", async () => {
  const pending = response({ deferred: true }); const { value } = await transport([{ response: pending }]);
  let closed = 0; value.attachCloser(async () => { closed += 1; });
  const current = route(); const work = value.handle(current);
  await new Promise((resolve) => setImmediate(resolve));
  await value.close(); pending.start(); await work;
  assert.equal(closed, 1); assert.equal(current.result.fulfilled.length, 0); assert.equal(current.result.aborted, 1);
});

test("a transport closed before owner attachment cannot become ready", async () => {
  const { value } = await transport(); await value.close(); let cleaned = 0;
  await assert.rejects(value.attachCloser(async () => { cleaned += 1; }), error("transport_closed"));
  assert.equal(cleaned, 1);
});

test("live transport caps concurrent attempts and retains no route.continue fallback", async () => {
  const pending = Array.from({ length: 4 }, () => response({ deferred: true })); const { value } = await transport(pending.map((response) => ({ response })));
  const active = pending.map(() => route()); const work = active.map((current) => value.handle(current));
  await new Promise((resolve) => setImmediate(resolve));
  const overflow = route(); await value.handle(overflow); assert.equal(overflow.result.aborted, 1); assert.equal(overflow.result.continued, 0);
  await value.close(); await Promise.all(work); for (const current of active) assert.equal(current.result.fulfilled.length, 0);
});
