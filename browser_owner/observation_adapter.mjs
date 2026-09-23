import { createServer } from "node:http";
import { chromium } from "playwright";
import { OwnerError, createBrowserOwner } from "./owner.mjs";

const LIMITS = Object.freeze({ maxObservations: 4, maxEntities: 16, maxMetadata: 4096, maxBytes: 1024 * 1024, maxPng: 1024 * 1024, maxPixels: 1_000_000, ttl: 60_000, timeout: 5_000 });
const HTML = '<!doctype html><main aria-label="Synthetic browser fixture"><button>Capture report</button><button disabled>Disabled capture</button><section role="status">Read-only status region</section></main>';
const bytes = (value) => Buffer.byteLength(value, "utf8");
const plain = (value) => value && typeof value === "object" && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;
const opaque = (value, expression) => typeof value === "string" && expression.test(value);

function refusal(code) { return { schema: "browser-owner-adapter/v1", status: "refused", code }; }
function bounded(operation, result, binding) {
  const value = { schema: "browser-owner-adapter/v1", status: "ok", operation, binding, result };
  return bytes(JSON.stringify(value)) <= 8192 ? value : refusal("result_too_large");
}

async function fixtureServer() {
  const server = createServer((request, response) => {
    if (request.url !== "/fixture") { response.writeHead(404); return response.end(); }
    response.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
    response.end(HTML);
  });
  await new Promise((resolve, reject) => server.once("error", reject).listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return { url: `http://127.0.0.1:${port}/fixture`, close: () => new Promise((resolve) => server.close(resolve)) };
}

/** A fixture-only, source-owned boundary. It never exposes navigation or image bytes. */
export async function createFixtureObservationAdapter({ piSessionId = "fixture-session", preview } = {}) {
  if (!opaque(piSessionId, /^[A-Za-z0-9-]{1,128}$/)) throw new Error("invalid_session_binding");
  const fixture = await fixtureServer();
  let owner;
  try {
    const origin = new URL(fixture.url).origin;
    owner = await createBrowserOwner({
      launch: () => chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || "/usr/bin/google-chrome", headless: true, args: ["--disable-gpu"] }),
      documentOrigins: [origin], subresourceOrigins: [origin], limits: LIMITS, jev: { enabled: false }, preview,
    });
    const session = owner.session();
    await session.navigate(fixture.url);
    let sequence = 0, closed = false;
    let active;
    const execute = async (request, { signal } = {}) => {
      if (closed) return refusal("owner_closed");
      if (!plain(request) || !["capture", "resolve", "release", "preview"].includes(request.operation)) return refusal("invalid_request");
      try {
        if (request.operation === "capture") {
          if (Object.keys(request).length !== 1) return refusal("invalid_request");
          const result = await session.capture({ schema: "widget-resolution/v1", request_id: `fixture-${++sequence}`, target: { description: "synthetic report", qualifiers: [] }, predicates: ["exists", "in_viewport", "occluded", "enabled"], scope: { kind: "document", root: "document" }, require_unique: true }, { signal });
          const binding = Object.freeze({ pi_session_id: piSessionId, owner_session_id: result.session_id });
          active = Object.freeze({ observation_id: result.observation_id, binding });
          return bounded("capture", result, binding);
        }
        if (request.operation === "preview") {
          if (Object.keys(request).length !== 1 || !active || active.binding.pi_session_id !== piSessionId) return refusal("unknown_observation");
          return bounded("preview", await session.preview(active.observation_id), active.binding);
        }
        if (!plain(request.args) || Object.keys(request).length !== 2) return refusal("invalid_request");
        if (request.operation === "resolve") {
          if (Object.keys(request.args).length !== 2 || !opaque(request.args.observation_id, /^[0-9a-f-]{36}$/) || !opaque(request.args.entity_id, /^e-[1-9][0-9]*$/)) return refusal("invalid_request");
          return bounded("resolve", await session.resolve(request.args.observation_id, request.args.entity_id), active?.binding);
        }
        if (Object.keys(request.args).length !== 1 || !opaque(request.args.observation_id, /^[0-9a-f-]{36}$/)) return refusal("invalid_request");
        await session.release(request.args.observation_id);
        const binding = active?.binding; if (active?.observation_id === request.args.observation_id) active = undefined;
        return bounded("release", { observation_id: request.args.observation_id, released: true }, binding);
      } catch (error) { return refusal(error instanceof OwnerError ? error.code : "owner_failed"); }
    };
    return Object.freeze({ execute, async close() { if (closed) return; closed = true; await owner.close(); await fixture.close(); } });
  } catch (error) { await owner?.close().catch(() => {}); await fixture.close(); throw error; }
}
