import { createBrowserOwner, OwnerError } from "./owner.mjs";
import { createJevConsumer } from "./jev_consumer.mjs";
import { createLiveBufferedTransport } from "./live_transport.mjs";

const byteLength = (value) => Buffer.byteLength(value, "utf8");
const RESULT_LIMIT = 15 * 1024;
const maxRecords = 64;
const ownerCodes = new Set(["cancelled", "deadline_exceeded", "expired_observation", "invalid_capture_request", "invalid_jev_request", "navigation_not_permitted", "owner_closed", "owner_failed", "released", "revoked", "stale_observation", "unknown_entity", "unknown_observation", "unknown_scope"]);

export class LiveAdapterError extends Error { constructor(code) { super(code); this.code = code; } }
const fail = (code) => { throw new LiveAdapterError(code); };
const plain = (value) => value && typeof value === "object" && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;
const opaque = (value, expression) => typeof value === "string" && expression.test(value);
const refusal = (code) => ({ schema: "browser-owner-adapter/v1", status: "refused", code });

function pagesFor(value) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 2) fail("invalid_adapter_config");
  const pages = new Map(); let origin;
  for (const page of value) {
    if (!plain(page) || Object.keys(page).length !== 2 || !opaque(page.id, /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/) || typeof page.url !== "string" || byteLength(page.url) > 2048 || pages.has(page.id)) fail("invalid_adapter_config");
    let url; try { url = new URL(page.url); } catch { fail("invalid_adapter_config"); }
    if (url.protocol !== "https:" || url.username || url.password || url.hash || url.href !== page.url) fail("invalid_adapter_config");
    if (origin && origin !== url.origin) fail("invalid_adapter_config");
    origin = url.origin; pages.set(page.id, Object.freeze({ id: page.id, url: url.href }));
  }
  return { pages, origin };
}

function boundedClone(value) {
  let encoded, cloned;
  try { encoded = JSON.stringify(value); } catch { return null; }
  if (typeof encoded !== "string") return null;
  if (byteLength(encoded) > 16 * 1024) return null;
  try { cloned = structuredClone(value); } catch { return null; }
  return cloned;
}

function widgetRequest(value) {
  const allowed = new Set(["schema", "request_id", "target", "predicates", "scope", "require_unique", "entity_offset"]);
  if (!plain(value) || Object.keys(value).some((key) => !allowed.has(key)) || value.schema !== "widget-resolution/v1") return null;
  const target = value.target;
  if (typeof value.request_id !== "string" || !byteLength(value.request_id) || byteLength(value.request_id) > 64 || !plain(target) || Object.keys(target).some((key) => key !== "description" && key !== "qualifiers") || typeof target.description !== "string" || !byteLength(target.description) || byteLength(target.description) > 512 || !Array.isArray(target.qualifiers) || target.qualifiers.length > 8 || target.qualifiers.some((item) => typeof item !== "string" || !byteLength(item) || byteLength(item) > 128)) return null;
  const names = new Set(["exists", "in_viewport", "occluded", "enabled"]);
  if (!Array.isArray(value.predicates) || value.predicates.some((item) => typeof item !== "string" || !names.has(item)) || new Set(value.predicates).size !== value.predicates.length) return null;
  const scope = value.scope;
  if (!plain(scope) || Object.keys(scope).some((key) => key !== "kind" && key !== "root") || !["document", "subtree"].includes(scope.kind) || typeof scope.root !== "string" || (scope.kind === "document" && scope.root !== "document") || (scope.kind === "subtree" && !/^[0-9a-f-]{36}:e-[1-9][0-9]*$/.test(scope.root))) return null;
  const requireUnique = value.require_unique === undefined ? false : value.require_unique;
  const entityOffset = value.entity_offset === undefined ? 0 : value.entity_offset;
  if (typeof requireUnique !== "boolean" || !Number.isInteger(entityOffset) || entityOffset < 0 || entityOffset > 2047) return null;
  return { ...value, require_unique: requireUnique, entity_offset: entityOffset };
}

function requestFor(raw) {
  const value = boundedClone(raw);
  if (!plain(value) || Object.keys(value).some((key) => !["operation", "page_id", "request", "observation_id", "entity_id"].includes(key)) || typeof value.operation !== "string") return null;
  if (value.operation === "capture") {
    const request = widgetRequest(value.request);
    return Object.keys(value).length === 3 && opaque(value.page_id, /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/) && request ? { ...value, request } : null;
  }
  if (value.operation === "resolve") return Object.keys(value).length === 3 && opaque(value.observation_id, /^[0-9a-f-]{36}$/) && opaque(value.entity_id, /^e-[1-9][0-9]*$/) ? value : null;
  if (["release", "jev_resolve"].includes(value.operation)) return Object.keys(value).length === 2 && opaque(value.observation_id, /^[0-9a-f-]{36}$/) ? value : null;
  return null;
}

function safeCode(error) {
  if (error instanceof OwnerError && ownerCodes.has(error.code)) return error.code;
  return "adapter_failed";
}

/**
 * Creates the read-only, session-bound adapter. The optional second argument is
 * an internal harness seam: callers cannot place transport or launch details in
 * a request or page configuration.
 */
export async function createLiveObservationAdapter(config, dependencies = {}) {
  if (!plain(config) || Object.keys(config).some((key) => !["launch", "piSessionId", "pages", "jev"].includes(key)) || typeof config.launch !== "function" || !opaque(config.piSessionId, /^[A-Za-z0-9-]{1,128}$/)) fail("invalid_adapter_config");
  if (!plain(dependencies) || Object.keys(dependencies).some((key) => key !== "transportFactory") || (dependencies.transportFactory !== undefined && typeof dependencies.transportFactory !== "function")) fail("invalid_adapter_config");
  const launch = config.launch, piSessionId = config.piSessionId, jev = config.jev === undefined ? { enabled: false } : config.jev;
  if (jev === null) fail("invalid_adapter_config");
  const { pages, origin } = pagesFor(config.pages);
  try { createJevConsumer(jev, new Set([origin])); } catch { fail("invalid_adapter_config"); }
  const transportFactory = dependencies.transportFactory || createLiveBufferedTransport;
  let transport, owner;
  try {
    transport = await transportFactory({ documentUrls: [...pages.values()].map((page) => page.url) });
    owner = await createBrowserOwner({ launch, documentOrigins: [origin], subresourceOrigins: [origin], jev, transport });
  } catch (error) {
    await owner?.close().catch(() => {});
    await transport?.close?.().catch(() => {});
    if (error instanceof LiveAdapterError) throw error;
    fail("adapter_start_failed");
  }
  const session = owner.session(), records = new Map();
  let closed = false, active = false, generation = 0, currentPageId, ownerSessionId, closePromise;

  const finish = (operation, pageId, result) => {
    const value = { schema: "browser-owner-adapter/v1", status: "ok", operation, binding: { pi_session_id: piSessionId, owner_session_id: ownerSessionId }, page_id: pageId, result };
    if (byteLength(JSON.stringify(value)) > RESULT_LIMIT) return null;
    return value;
  };
  const forget = (observationId) => records.delete(observationId);
  const remember = (receipt, pageId) => {
    records.set(receipt.observation_id, { pageId, entities: new Set(receipt.entities.map((entity) => entity.id)) });
    while (records.size > maxRecords) records.delete(records.keys().next().value);
  };
  const close = () => {
    if (closePromise) return closePromise;
    closed = true; generation += 1; records.clear(); currentPageId = undefined;
    closePromise = owner.close().catch(() => {});
    return closePromise;
  };
  const execute = async (raw, options = {}) => {
    const validSignal = (signal) => signal && typeof signal === "object" && typeof signal.aborted === "boolean" && typeof signal.addEventListener === "function" && typeof signal.removeEventListener === "function";
    if (!plain(options) || Object.keys(options).some((key) => key !== "signal") || (options.signal !== undefined && !validSignal(options.signal))) return refusal("invalid_request");
    const request = requestFor(raw);
    if (!request) return refusal("invalid_request");
    if (closed) return refusal("adapter_closed");
    if (active) return refusal("busy");
    if (options.signal?.aborted) return refusal("cancelled");
    active = true;
    const localGeneration = generation;
    const onAbort = () => { void close(); };
    options.signal?.addEventListener("abort", onAbort, { once: true });
    try {
      let result, pageId;
      if (request.operation === "capture") {
        const page = pages.get(request.page_id);
        if (!page) return refusal("unknown_page");
        if (request.request.scope.kind === "subtree") {
          const [observationId, entityId] = request.request.scope.root.split(":");
          const record = records.get(observationId);
          if (!record || record.pageId !== page.id || !record.entities.has(entityId)) return refusal("unknown_scope");
        }
        if (currentPageId !== page.id) {
          records.clear(); currentPageId = undefined;
          await session.navigate(page.url);
          if (closed || localGeneration !== generation || options.signal?.aborted) return refusal("cancelled");
          currentPageId = page.id;
        }
        result = await session.capture(request.request, { signal: options.signal });
        if (closed || localGeneration !== generation || options.signal?.aborted) { await session.release(result.observation_id).catch(() => {}); return refusal("cancelled"); }
        ownerSessionId = result.session_id; remember(result, page.id); pageId = page.id;
      } else {
        const record = records.get(request.observation_id);
        if (!record) return refusal("unknown_observation");
        pageId = record.pageId;
        if (request.operation === "resolve") {
          if (!record.entities.has(request.entity_id)) return refusal("unknown_entity");
          result = await session.resolve(request.observation_id, request.entity_id);
        } else if (request.operation === "release") {
          await session.release(request.observation_id); forget(request.observation_id);
          result = { observation_id: request.observation_id, released: true };
        } else result = await session.jevResolve(request.observation_id, { signal: options.signal });
        if (closed || localGeneration !== generation || options.signal?.aborted) return refusal("cancelled");
      }
      const value = finish(request.operation, pageId, result);
      if (value) return value;
      if (request.operation === "capture") { forget(result.observation_id); await session.release(result.observation_id).catch(() => {}); }
      return refusal("result_too_large");
    } catch (error) {
      if (options.signal?.aborted || closed || localGeneration !== generation) return refusal("cancelled");
      if (error instanceof OwnerError && ["expired_observation", "stale_observation", "unknown_observation"].includes(error.code) && request.observation_id) forget(request.observation_id);
      return refusal(safeCode(error));
    } finally {
      options.signal?.removeEventListener("abort", onAbort);
      if (options.signal?.aborted || closed) await close();
      active = false;
    }
  };
  return Object.freeze({ execute, close });
}
