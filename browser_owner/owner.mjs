import { createHash, randomUUID } from "node:crypto";
import { createJevConsumer, evaluateJev } from "./jev_consumer.mjs";

export class OwnerError extends Error { constructor(code) { super(code); this.code = code; } }
const fail = (code) => { throw new OwnerError(code); };
const defaultClock = () => performance.now();
const bytes = (value) => Buffer.byteLength(value, "utf8");
const clone = (value) => structuredClone(value);
const publicEntity = (entity) => ({ id: entity.id, role: entity.role, text: entity.text, nearby: entity.nearby, source: "dom", exists: entity.exists, in_viewport: entity.in_viewport, occluded: entity.occluded, enabled: entity.enabled, predicate_reasons: clone(entity.predicate_reasons) });

async function release(state, handle) {
  if (handle?.objectId) await state.cdp.send("Runtime.releaseObject", { objectId: handle.objectId }).catch(() => {});
}

async function dispose(record) {
  record.image = null;
  await Promise.allSettled([...record.handles.values()].map((handle) => release(record.state, handle)));
  record.handles.clear();
}

export async function createBrowserOwner({ launch, documentOrigins, subresourceOrigins = documentOrigins, limits = {}, clock = defaultClock, jev = { enabled: false } }) {
  if (typeof launch !== "function") fail("invalid_launcher");
  const names = new Set(["maxObservations", "maxEntities", "maxMetadata", "maxBytes", "maxPng", "maxPixels", "ttl", "timeout"]);
  if (!limits || typeof limits !== "object" || Array.isArray(limits) || Object.getPrototypeOf(limits) !== Object.prototype || typeof clock !== "function" || Object.keys(limits).some((name) => !names.has(name))) fail("invalid_limits");
  const limit = (name, fallback, maximum) => {
    const value = Object.hasOwn(limits, name) ? limits[name] : fallback;
    if (!Number.isInteger(value) || value <= 0 || value > maximum) fail("invalid_limits");
    return value;
  };
  const originSet = (origins) => {
    if (!Array.isArray(origins) && !(origins instanceof Set)) return null;
    return new Set(origins);
  };
  const documents = originSet(documentOrigins), subresources = originSet(subresourceOrigins);
  const policy = {
    documents, subresources,
    maxObservations: limit("maxObservations", 64, 64), maxEntities: limit("maxEntities", 64, 64), maxMetadata: limit("maxMetadata", 8192, 8192), maxBytes: limit("maxBytes", 256 * 1024 * 1024, 256 * 1024 * 1024), maxPng: limit("maxPng", 8 * 1024 * 1024, 8 * 1024 * 1024), maxPixels: limit("maxPixels", 8_000_000, 8_000_000), ttl: limit("ttl", 60 * 60_000, 60 * 60_000), timeout: limit("timeout", 10_000, 10_000),
  };
  const validOrigins = (origins) => origins instanceof Set && origins.size > 0 && [...origins].every((origin) => { try { const url = new URL(origin); return url.origin === origin && /^https?:$/.test(url.protocol); } catch { return false; } });
  if (!validOrigins(policy.documents) || !validOrigins(policy.subresources)) fail("invalid_origin_policy");
  let browser, context;
  try {
  browser = await launch();
  context = await browser.newContext({ serviceWorkers: "block", acceptDownloads: false });
  const state = { browser, context, page: null, cdp: null, policy, jev: createJevConsumer(jev, documents), clock, closed: false, browserClosed: false, session: null, sessionId: randomUUID(), generation: 1, queue: [], running: false, records: new Map(), bytes: 0, navigation: 0 };
  state.invalidate = () => { const records = [...state.records.values()]; state.records.clear(); state.bytes = 0; for (const record of records) void dispose(record); };
  await context.route("**/*", async (route) => {
    let url; try { url = new URL(route.request().url()); } catch { return route.abort(); }
    const request = route.request(), navigation = request.isNavigationRequest(), allowed = navigation ? policy.documents : policy.subresources;
    let frame;
    if (navigation) { try { frame = request.frame(); } catch { return route.abort(); } }
    if (!/^https?:$/.test(url.protocol) || !allowed.has(url.origin) || (navigation && state.page && frame !== state.page.mainFrame())) return route.abort();
    return route.continue();
  });
  const page = await context.newPage(); state.page = page; state.cdp = await context.newCDPSession(page);
  await state.cdp.send("Page.enable");
  await state.cdp.send("Page.addScriptToEvaluateOnNewDocument", { worldName: "anvil-owner-epoch", source: `(() => { let dom=0, viewport=0; const observer=new MutationObserver((records)=>{dom+=records.length;}); observer.observe(document,{subtree:true,childList:true,attributes:true,characterData:true}); addEventListener("resize",()=>{viewport+=1;},{passive:true}); addEventListener("scroll",()=>{viewport+=1;},{passive:true,capture:true}); Object.defineProperty(globalThis,"__anvilOwnerEpoch",{value:()=>{dom+=observer.takeRecords().length;return [dom,viewport];}}); })();` });
  page.on("framenavigated", (frame) => { if (frame === page.mainFrame()) { state.navigation += 1; state.invalidate(); } });
  page.on("popup", (popup) => { void popup.close().catch(() => {}); });
  page.on("download", (download) => { void download.cancel().catch(() => {}); });
  if (typeof context.routeWebSocket !== "function") fail("owner_failed");
  await context.routeWebSocket("**/*", (route) => route.close());
  page.on("close", () => { state.generation += 1; state.invalidate(); });
  return new Owner(state);
  } catch (error) {
    await context?.close().catch(() => {});
    await browser?.close().catch(() => {});
    if (error instanceof OwnerError) throw error;
    fail("owner_failed");
  }
}

class Owner {
  #state;
  constructor(state) { this.#state = state; }
  session() { if (this.#state.session) fail("session_already_open"); const session = new Facade(this.#state); this.#state.session = session; return session; }
  async close() {
    const state = this.#state;
    if (!state.closed) { state.closed = true; state.generation += 1; state.invalidate(); state.current?.abort(); state.current?.reject(new OwnerError("owner_closed")); for (const item of state.queue.splice(0)) item.reject(new OwnerError("owner_closed")); await state.context.close().catch(() => {}); }
    if (!state.browserClosed) { state.browserClosed = true; await state.browser.close().catch(() => {}); }
  }
}

class Facade {
  #state;
  constructor(state) { this.#state = state; Object.freeze(this); }
  #enqueue(work, { signal, timeout = this.#state.policy.timeout } = {}) {
    if (this.#state.closed || this.#state.session !== this) return Promise.reject(new OwnerError("owner_closed"));
    if (signal?.aborted) return Promise.reject(new OwnerError("cancelled"));
    if (signal && typeof signal.addEventListener !== "function") return Promise.reject(new OwnerError("invalid_request"));
    const state = this.#state;
    if (state.queue.length >= 8) return Promise.reject(new OwnerError("queue_full"));
    const generation = state.generation;
    const pending = new Promise((resolve, reject) => {
      let settled = false, timer, item;
      const controller = new AbortController();
      const onAbort = () => {
        controller.abort();
        settle(reject, new OwnerError("cancelled"));
        if (state.current === item) void this.revoke();
        else state.queue.splice(state.queue.indexOf(item), 1);
      };
      const settle = (callback, value) => { if (!settled) { settled = true; clearTimeout(timer); signal?.removeEventListener("abort", onAbort); callback(value); } };
      item = { abort: () => controller.abort(), reject: (error) => settle(reject, error), run: async () => {
        if (settled) return;
        try { const result = await work(generation, controller.signal); if (generation !== state.generation || state.closed || settled) fail("revoked"); settle(resolve, result); } catch (error) { settle(reject, error instanceof OwnerError ? error : new OwnerError("owner_failed")); }
      }};
      timer = setTimeout(() => { controller.abort(); settle(reject, new OwnerError("deadline_exceeded")); void this.revoke(); }, timeout);
      signal?.addEventListener("abort", onAbort, { once: true });
      state.queue.push(item); this.#pump();
    });
    // Revoke is intentionally out of band; callers may attach their handler after it closes the context.
    pending.catch(() => {});
    return pending;
  }
  #pump() { const state = this.#state; if (state.running) return; const item = state.queue.shift(); if (!item) return; state.running = true; state.current = item; item.run().finally(() => { state.current = null; state.running = false; this.#pump(); }); }
  async revoke() { const state = this.#state; if (state.closed) return; state.closed = true; state.generation += 1; state.invalidate(); state.current?.abort(); state.current?.reject(new OwnerError("revoked")); for (const item of state.queue.splice(0)) item.reject(new OwnerError("revoked")); await state.context.close().catch(() => {}); }
  navigate(url, options = {}) {
    return this.#enqueue(async () => {
      if (!options || Object.keys(options).length) fail("invalid_navigation_request");
      let parsed; try { parsed = new URL(url); } catch { fail("navigation_not_permitted"); }
      if (!/^https?:$/.test(parsed.protocol) || !this.#state.policy.documents.has(parsed.origin)) fail("navigation_not_permitted");
      const before = this.#state.navigation;
      try { await this.#state.page.goto(parsed.href, { waitUntil: "load", timeout: this.#state.policy.timeout }); } catch { fail("navigation_not_permitted"); }
      let finalUrl; try { finalUrl = new URL(this.#state.page.url()); } catch { fail("navigation_not_permitted"); }
      if (this.#state.navigation <= before || !this.#state.policy.documents.has(finalUrl.origin)) fail("navigation_not_permitted");
      return clone({ url: finalUrl.href, navigation_epoch: this.#state.navigation });
    });
  }
  capture(request = {}, options = {}) {
    let retained;
    try { retained = validateRequest(request); } catch (error) { return Promise.reject(error); }
    return this.#enqueue((generation) => {
      if (!options || Object.keys(options).some((key) => key !== "signal")) fail("invalid_capture_request");
      return this.#capture(retained, generation);
    }, { signal: options?.signal });
  }
  async #capture(rawRequest, generation) {
    const request = validateRequest(rawRequest), state = this.#state;
    let handles = new Map(), root, rootTemporary = false;
    try {
      ({ handle: root, temporary: rootTemporary } = await this.#rootFor(request.scope));
      const start = await snapshot(state);
      if (start.width * start.height > state.policy.maxPixels) fail("screenshot_too_large");
      const inventory = await call(state, root, inventoryFor, [state.policy.maxEntities]);
      handles = await retainedHandles(state, root, inventory.entities.map((entity) => entity.node_index));
      const png = await state.page.screenshot({ type: "png", caret: "initial", timeout: state.policy.timeout });
      if (png.length > state.policy.maxPng) fail("screenshot_too_large");
      const end = await snapshot(state);
      if (generation !== state.generation || !sameEpoch(start, end)) fail("incoherent_capture");
      const observationId = randomUUID();
      const entities = inventory.entities.map((entity, index) => {
        const id = `e-${index + 1}`, handle = handles.get(String(index)); if (!handle) fail("incoherent_capture");
        handles.delete(String(index)); handles.set(id, handle); delete entity.node_index; return { id, ...entity };
      });
      const receipt = {
        schema: "widget-resolution/v1", request_id: request.request_id, session_id: state.sessionId, page_id: String(state.navigation), observation_id: observationId,
        target: clone(request.target), predicates: clone(request.predicates), scope: clone(request.scope), require_unique: request.require_unique,
        capture: { navigation_epoch: start.navigation, dom_epoch: start.dom, viewport_epoch: start.viewport },
        coverage: { complete: !inventory.omitted_count && !inventory.loading && !inventory.unsupported_regions.length && !inventory.untraversed_regions.length, omitted_count: inventory.omitted_count, loading: inventory.loading, unsupported_regions: inventory.unsupported_regions, untraversed_regions: inventory.untraversed_regions },
        entities: entities.map(publicEntity),
      };
      const metadata = bytes(JSON.stringify(receipt)); if (metadata > state.policy.maxMetadata) fail("metadata_too_large");
      const record = { id: observationId, origin: new URL(state.page.url()).origin, receipt, handles, image: png, digest: createHash("sha256").update(png).digest("hex"), bytes: png.length + metadata, epochs: end, lastRead: state.clock(), state };
      if (record.bytes > state.policy.maxBytes) fail("retention_exhausted");
      if (generation !== state.generation || state.closed) fail("revoked");
      while (state.records.size >= state.policy.maxObservations || state.bytes + record.bytes > state.policy.maxBytes) { const oldest = state.records.values().next().value; if (!oldest) fail("retention_exhausted"); state.records.delete(oldest.id); state.bytes -= oldest.bytes; await dispose(oldest); }
      if (generation !== state.generation || state.closed) fail("revoked");
      state.records.set(observationId, record); state.bytes += record.bytes; handles = new Map();
      return clone(receipt);
    } finally { await Promise.allSettled([...handles.values()].map((handle) => release(state, handle))); if (rootTemporary) await release(state, root); }
  }
  async #rootFor(scope) {
    if (scope.kind === "document") return { handle: await documentRoot(this.#state), temporary: true };
    const [observationId, entityId] = scope.root.split(":"), record = this.#state.records.get(observationId);
    if (!record) fail("unknown_scope");
    if (this.#expired(record)) { await this.#delete(record); fail("expired_observation"); }
    const handle = record.handles.get(entityId);
    if (!handle || !(await call(this.#state, handle, (node) => node.isConnected).catch(() => false))) fail("unknown_scope");
    if (!sameEpoch(record.epochs, await snapshot(this.#state))) fail("stale_observation");
    return { handle, temporary: false };
  }
  async release(observationId) { return this.#enqueue(async () => { if (typeof observationId !== "string") fail("unknown_observation"); const record = this.#state.records.get(observationId); if (!record) fail("unknown_observation"); await this.#delete(record); }); }
  resolve(observationId, entityId) { return this.#enqueue(() => this.#resolve(observationId, entityId)); }
  async #resolve(observationId, entityId) {
      if (typeof observationId !== "string" || typeof entityId !== "string") fail("unknown_observation");
      const record = this.#state.records.get(observationId); if (!record) fail("unknown_observation");
      if (this.#expired(record)) { await this.#delete(record); fail("expired_observation"); }
      const entity = record.receipt.entities.find((candidate) => candidate.id === entityId), handle = record.handles.get(entityId);
      if (!entity || !handle) fail("unknown_entity");
      if (!sameEpoch(record.epochs, await snapshot(this.#state))) fail("stale_observation");
      const facts = await call(this.#state, handle, predicateFacts).catch(() => null);
      if (!facts || !facts.exists || !sameEpoch(record.epochs, await snapshot(this.#state))) fail("stale_observation");
      record.lastRead = this.#state.clock(); return clone(publicEntity({ ...entity, ...facts }));
  }
  jevResolve(observationId, options = {}) {
    if (!options || Object.keys(options).some((key) => key !== "signal")) return Promise.reject(new OwnerError("invalid_jev_request"));
    return this.#enqueue((generation, signal) => this.#jevResolve(observationId, generation, signal), { signal: options.signal });
  }
  async #jevResolve(observationId, generation, signal) {
    const state = this.#state;
    if (!state.jev.enabled) return { outcome: "disabled" };
    if (typeof observationId !== "string") return { outcome: "unknown_observation" };
    const record = state.records.get(observationId);
    if (!record || record.id !== observationId || record.receipt.observation_id !== observationId) return { outcome: "unknown_observation" };
    if (this.#expired(record)) { await this.#delete(record); return { outcome: "expired_observation" }; }
    let origin; try { origin = new URL(state.page.url()).origin; } catch { return { outcome: "stale_observation" }; }
    if (record.origin !== state.jev.origin || origin !== state.jev.origin || !sameEpoch(record.epochs, await snapshot(state))) return { outcome: "export_denied" };
    const receipt = record.receipt;
    if (!receipt.entities.length) return { outcome: "empty_inventory" };
    const coverage = coverageFor(receipt.coverage);
    if (coverage.state !== "complete" && receipt.require_unique) return { outcome: "incomplete_coverage" };
    const projection = { schema: "browser-element-resolution-projection/v1", request_id: receipt.request_id, observation_id: receipt.observation_id, source: "dom", target: clone(receipt.target), scope: clone(receipt.scope), coverage, entities: receipt.entities.map(projectionEntity) };
    const result = await evaluateJev(state.jev, projection, signal);
    if (generation !== state.generation || state.closed || signal.aborted || result.outcome === "cancelled") return { outcome: "cancelled" };
    if (result.outcome !== "selection") return result;
    if (result.selection === "NO_MATCH_IN_CANDIDATES") return coverage.state === "complete" ? { outcome: "no_match_inconclusive" } : { outcome: "incomplete_coverage" };
    if (result.selection === "AMBIGUOUS") return { outcome: "ambiguous" };
    if (result.selection === "NEEDS_VISUAL_EVIDENCE") return { outcome: "needs_visual_evidence" };
    try {
      const entity = await this.#resolve(observationId, result.selection);
      return { outcome: "matched", advisory: true, selection: result.selection, entity };
    } catch (error) { if (error instanceof OwnerError) return { outcome: error.code }; throw error; }
  }
  #expired(record) { return this.#state.clock() - record.lastRead > this.#state.policy.ttl; }
  async #delete(record) { this.#state.records.delete(record.id); this.#state.bytes -= record.bytes; await dispose(record); }
}

function validateRequest(value) {
  const allowed = new Set(["schema", "request_id", "target", "predicates", "scope", "require_unique"]);
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).some((key) => !allowed.has(key)) || value.schema !== "widget-resolution/v1") fail("invalid_capture_request");
  const target = value.target;
  if (typeof value.request_id !== "string" || !bytes(value.request_id) || bytes(value.request_id) > 64 || !target || typeof target !== "object" || Array.isArray(target) || Object.keys(target).some((key) => key !== "description" && key !== "qualifiers") || typeof target.description !== "string" || !bytes(target.description) || bytes(target.description) > 512 || !Array.isArray(target.qualifiers) || target.qualifiers.length > 8 || target.qualifiers.some((item) => typeof item !== "string" || !bytes(item) || bytes(item) > 128)) fail("invalid_capture_request");
  const names = new Set(["exists", "in_viewport", "occluded", "enabled"]);
  if (!Array.isArray(value.predicates) || value.predicates.some((item) => typeof item !== "string" || !names.has(item)) || new Set(value.predicates).size !== value.predicates.length || typeof value.require_unique !== "boolean") fail("invalid_capture_request");
  const scope = value.scope;
  if (!scope || typeof scope !== "object" || Array.isArray(scope) || Object.keys(scope).some((key) => key !== "kind" && key !== "root") || !["document", "subtree"].includes(scope.kind) || typeof scope.root !== "string" || (scope.kind === "document" && scope.root !== "document") || (scope.kind === "subtree" && !/^[0-9a-f-]{36}:e-[1-9][0-9]*$/.test(scope.root))) fail("invalid_capture_request");
  return clone(value);
}

function inventoryFor(root, maxEntities) {
  const limit = 2048, textLimit = 256, entities = [], unsupported = new Set(), untraversed = new Set(); let visited = 0, omitted = 0;
  if (document.styleSheets.length) unsupported.add("cssom");
  const text = (node) => {
    const label = node.getAttribute("aria-label") || node.getAttribute("alt") || "";
    if (label) return { value: label.slice(0, textLimit), oversized: label.length > textLimit || new TextEncoder().encode(label).byteLength > textLimit };
    let value = "", seen = 0, oversized = false;
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    while (value.length < textLimit && seen < 256) {
      const part = walker.nextNode();
      if (!part) break;
      if (part.data.length > textLimit - value.length) oversized = true;
      value += part.data.slice(0, textLimit - value.length);
      seen += 1;
    }
    return { value: value.trim(), oversized: oversized || seen >= 256 || Boolean(walker.nextNode()) };
  };
  const facts = (node) => { const rect = node.getBoundingClientRect(), visible = Boolean(rect.width && rect.height && rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth), point = rect.width && rect.height ? document.elementFromPoint(rect.left + Math.min(1, rect.width / 2), rect.top + Math.min(1, rect.height / 2)) : null, interactive = /^(button|input|select|textarea|a)$/i.test(node.tagName) || ["button", "link", "checkbox", "combobox", "textbox"].includes(node.getAttribute("role")), disabled = node.matches(":disabled") || node.getAttribute("aria-disabled") === "true"; return { exists: true, in_viewport: visible, occluded: point ? !(node === point || node.contains(point)) : null, enabled: interactive ? !disabled : null, predicate_reasons: { exists: null, in_viewport: null, occluded: point ? null : "unknown", enabled: interactive ? null : "not_applicable" } }; };
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
  let node = root;
  while (node && visited < limit) {
    visited += 1; const tag = node.tagName.toLowerCase(); if (tag === "iframe") unsupported.add("frame"); if (tag === "canvas") unsupported.add("canvas"); if (node.shadowRoot) unsupported.add("shadow"); else if (tag.includes("-")) untraversed.add("closed_shadow_unknown");
    const role = node.getAttribute("role") || ({ h1: "heading", h2: "heading", h3: "heading", h4: "heading", h5: "heading", h6: "heading", main: "main", section: "region", img: "img", button: "button", input: node.type === "checkbox" ? "checkbox" : "textbox", select: "combobox", textarea: "textbox", a: "link" }[tag] || null);
    if (!role) {
      if (!["html", "head", "body", "script", "style", "meta", "link", "title"].includes(tag)) untraversed.add("unrepresented_dom");
      node = walker.nextNode();
      continue;
    }
    if (role.length > 128 || new TextEncoder().encode(role).byteLength > 128) { omitted += 1; untraversed.add("role_too_large"); node = walker.nextNode(); continue; }
    const label = text(node);
    if (label.oversized || new TextEncoder().encode(label.value).byteLength > textLimit || entities.length >= maxEntities) {
      omitted += 1;
      if (entities.length >= maxEntities) untraversed.add("entity_cap");
      if (label.oversized) untraversed.add("text_too_large");
      node = walker.nextNode();
      continue;
    }
    entities.push({ node_index: visited - 1, role, text: label.value, nearby: "", source: "dom", ...facts(node) });
    node = walker.nextNode();
  }
  if (node) { omitted += 1; untraversed.add("node_visit_cap"); }
  return { entities, omitted_count: omitted, loading: document.readyState !== "complete", unsupported_regions: [...unsupported].sort(), untraversed_regions: [...untraversed].sort() };
}

function predicateFacts(node) {
  const rect = node.getBoundingClientRect(), visible = Boolean(rect.width && rect.height && rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth), point = rect.width && rect.height ? document.elementFromPoint(rect.left + Math.min(1, rect.width / 2), rect.top + Math.min(1, rect.height / 2)) : null;
  const interactive = /^(button|input|select|textarea|a)$/i.test(node.tagName) || ["button", "link", "checkbox", "combobox", "textbox"].includes(node.getAttribute("role"));
  const disabled = node.matches(":disabled") || node.getAttribute("aria-disabled") === "true";
  return { exists: true, in_viewport: visible, occluded: point ? !(node === point || node.contains(point)) : null, enabled: interactive ? !disabled : null, predicate_reasons: { exists: null, in_viewport: null, occluded: point ? null : "unknown", enabled: interactive ? null : "not_applicable" } };
}

async function isolatedWorld(state) {
  const { frameTree } = await state.cdp.send("Page.getFrameTree");
  const { executionContextId } = await state.cdp.send("Page.createIsolatedWorld", { frameId: frameTree.frame.id, worldName: "anvil-owner-epoch" });
  return executionContextId;
}

async function documentRoot(state) {
  const result = await state.cdp.send("Runtime.evaluate", { contextId: await isolatedWorld(state), expression: "document.documentElement", returnByValue: false });
  if (!result.result.objectId) fail("incoherent_capture");
  return { objectId: result.result.objectId };
}

async function call(state, handle, fn, args = []) {
  const result = await state.cdp.send("Runtime.callFunctionOn", { objectId: handle.objectId, functionDeclaration: `function(...args) { return (${fn.toString()})(this, ...args); }`, arguments: args.map((value) => ({ value })), returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) fail("incoherent_capture");
  return result.result.value;
}

async function retainedHandles(state, root, indexes) {
  const result = await state.cdp.send("Runtime.callFunctionOn", { objectId: root.objectId, functionDeclaration: `function(wanted) { const found=[], wantedSet=new Set(wanted), walker=document.createTreeWalker(this, NodeFilter.SHOW_ELEMENT); for (let current=this,index=0; current && index<=2048; current=walker.nextNode(),index+=1) if (wantedSet.has(index)) found.push(current); return found; }`, arguments: [{ value: indexes }], returnByValue: false });
  const arrayId = result.result.objectId; if (!arrayId) fail("incoherent_capture");
  try {
    const properties = await state.cdp.send("Runtime.getProperties", { objectId: arrayId, ownProperties: true }), handles = new Map();
    for (const property of properties.result) if (/^(0|[1-9][0-9]*)$/.test(property.name) && property.value?.objectId) handles.set(property.name, { objectId: property.value.objectId });
    return handles;
  } finally { await state.cdp.send("Runtime.releaseObject", { objectId: arrayId }).catch(() => {}); }
}

async function snapshot(state) {
  const contextId = await isolatedWorld(state);
  const result = await state.cdp.send("Runtime.evaluate", { contextId, expression: `(() => { const epoch=globalThis.__anvilOwnerEpoch && globalThis.__anvilOwnerEpoch(); const out=[]; let count=0,visited=0,overflow=false,node=document.documentElement; const walker=document.createTreeWalker(document.documentElement,NodeFilter.SHOW_ELEMENT); while(node && visited<2048){ if(/^(input|textarea|select)$/i.test(node.tagName)){ if(count>=256){overflow=true;break;} const value=String(node.value); if(value.length>256 || new TextEncoder().encode(value).byteLength>256){overflow=true;break;} out.push(value);count+=1; } node=walker.nextNode();visited+=1; } if(node) overflow=true; return {epoch,width:innerWidth,height:innerHeight,scroll_x:scrollX,scroll_y:scrollY,values:out,overflow}; })()`, returnByValue: true });
  const values = result.result.value || {}, [dom, viewport] = values.epoch || []; if (!Number.isInteger(dom) || !Number.isInteger(viewport)) fail("incoherent_capture");
  if (values.overflow) fail("incoherent_capture");
  return { navigation: state.navigation, dom, viewport, width: values.width, height: values.height, scroll_x: values.scroll_x, scroll_y: values.scroll_y, values: values.values, overflow: values.overflow };
}

function sameEpoch(left, right) { return left.navigation === right.navigation && left.dom === right.dom && left.viewport === right.viewport && left.width === right.width && left.height === right.height && left.scroll_x === right.scroll_x && left.scroll_y === right.scroll_y && left.overflow === right.overflow && JSON.stringify(left.values) === JSON.stringify(right.values); }

function coverageFor(coverage) {
  if (coverage.complete) return { state: "complete", reason: null };
  const reason = [coverage.loading && "loading", coverage.omitted_count && `omitted:${coverage.omitted_count}`, ...coverage.unsupported_regions, ...coverage.untraversed_regions].filter(Boolean).join(",").slice(0, 256);
  return { state: "partial", reason: reason || "owner_incomplete" };
}

function projectionEntity(entity) {
  return { id: entity.id, role: entity.role, text: entity.text, nearby: entity.nearby, state: { exists: entity.exists, in_viewport: entity.in_viewport, occluded: entity.occluded, enabled: entity.enabled }, predicate_reasons: clone(entity.predicate_reasons) };
}
