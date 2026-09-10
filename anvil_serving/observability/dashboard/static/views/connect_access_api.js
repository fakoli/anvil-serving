import { APIError } from "./api.js";

const SCHEMA = "anvil-connect.access/v1";
const MAX_RESPONSE_BYTES = 1024 * 1024;
const OPAQUE = /^[A-Za-z0-9._~-]{1,256}$/;
const IDENTITY = /^(?:human:[0-9a-f]{64}|[A-Za-z0-9._~-]{1,256})$/;
let endpoint = null;
let csrf = null;

const safeError = (status) => {
  if (status === 401)
    return new APIError("access-unauthenticated", "An Anvil Connect access session is required.", status);
  if (status === 403)
    return new APIError("access-forbidden", "This Anvil Connect identity is not authorized to manage access.", status);
  if (status === 409)
    return new APIError("access-conflict", "Access changed before this request completed. Reload and review the current inventory.", status);
  if (status === 429)
    return new APIError("access-rate-limited", "Too many access requests. Wait, then refresh the inventory.", status);
  return new APIError("access-unavailable", "The Anvil Connect access service is unavailable.", status);
};

export function configureConnectAccess(session) {
  endpoint = null;
  csrf = null;
  if (session?.authentication_mode !== "connect" || typeof session.connect_access_path !== "string") return false;
  let candidate;
  try {
    candidate = new URL(session.connect_access_path, window.location.origin);
  } catch {
    return false;
  }
  if (
    candidate.origin !== window.location.origin ||
    candidate.search ||
    candidate.hash ||
    !candidate.pathname.endsWith("/_anvil-connect/access")
  )
    return false;
  endpoint = candidate;
  return true;
}

function configuredEndpoint() {
  if (!endpoint) throw new APIError("access-unavailable", "Anvil Connect access management is unavailable.");
  return new URL(endpoint);
}

async function boundedText(response) {
  const declared = response.headers.get("content-length");
  if (declared !== null && (!/^(?:0|[1-9][0-9]{0,6})$/.test(declared) || Number(declared) > MAX_RESPONSE_BYTES))
    throw safeError(response.status);
  if (!response.body) return "";
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      if (!(next.value instanceof Uint8Array) || next.value.byteLength > MAX_RESPONSE_BYTES - size) {
        await reader.cancel();
        throw safeError(response.status);
      }
      chunks.push(next.value);
      size += next.value.byteLength;
    }
  } finally {
    reader.releaseLock();
  }
  const joined = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    joined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(joined);
  } catch {
    throw safeError(response.status);
  }
}

async function call(method, body, { kind, cursor, signal } = {}) {
  const target = configuredEndpoint();
  if (method === "GET") {
    target.searchParams.set("kind", kind);
    target.searchParams.set("limit", "50");
    if (cursor) target.searchParams.set("cursor", cursor);
  }
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = setTimeout(abort, 10000);
  try {
    const headers = { Accept: "application/json" };
    if (method === "POST") {
      if (typeof csrf !== "string" || !csrf) throw new APIError("access-csrf", "Reload access inventory before making a change.");
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = csrf;
    }
    const response = await fetch(target, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      signal: controller.signal,
    });
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) throw safeError(response.status);
    const raw = await boundedText(response);
    let value;
    try {
      value = JSON.parse(raw);
    } catch {
      throw safeError(response.status);
    }
    if (!response.ok) throw safeError(response.status);
    return value;
  } catch (error) {
    if (error instanceof APIError) throw error;
    if (signal?.aborted) throw new DOMException("Request replaced", "AbortError");
    throw safeError(0);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

export async function readConnectAccess(kind, cursor, signal) {
  if (!/^(users|sessions)$/.test(kind) || (cursor !== null && typeof cursor !== "string"))
    throw new APIError("access-invalid", "Invalid access inventory request.");
  const value = await call("GET", undefined, { kind, cursor, signal });
  if (
    !value ||
    value.schema !== SCHEMA ||
    value.kind !== kind ||
    !Array.isArray(value.items) || value.items.length > 50 ||
    (value.next_cursor !== null && (typeof value.next_cursor !== "string" || !OPAQUE.test(value.next_cursor))) ||
    typeof value.csrf !== "string" || !OPAQUE.test(value.csrf) ||
    typeof value.current_principal !== "string" || !IDENTITY.test(value.current_principal) ||
    typeof value.current_session !== "string" || !OPAQUE.test(value.current_session)
  )
    throw new APIError("access-invalid", "The Anvil Connect access response was invalid.");
  csrf = value.csrf;
  return value;
}

export async function mutateConnectAccess(body, signal) {
  if (!body || typeof body !== "object" || typeof body.action !== "string")
    throw new APIError("access-invalid", "Invalid access change.");
  const value = await call("POST", { ...body, csrf }, { signal });
  if (value?.schema !== SCHEMA || value.applied !== true || value.request_id !== body.request_id)
    throw new APIError("access-invalid", "The Anvil Connect access response was invalid.");
  return value;
}
