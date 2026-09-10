// One same-origin facade. Browser state never contains an upstream credential.
const assetBase = new URL("../", import.meta.url);
export const apiBase = new URL("api/observatory/v1/", assetBase);
export const workbenchBase = new URL("api/workbench/v1/", assetBase);
let session = null;
export const getSession = () => session;
export const setSession = (value) => {
  const changed = session?.csrf_token !== value?.csrf_token;
  session = value;
  if (changed) window.dispatchEvent(new Event("observatory-session-changed"));
};
export class APIError extends Error {
  constructor(code, message, status = 0) {
    super(message);
    this.code = code;
    this.status = status;
  }
}
export async function request(
  path,
  { method = "GET", body, signal, timeout = 10000, base = apiBase } = {},
) {
  if (
    !/^[a-z][a-zA-Z0-9/._%-]*(?:\?[^#]*)?$/.test(path) ||
    path
      .split("?")[0]
      .split("/")
      .some((part) => [".", ".."].includes(part))
  )
    throw new APIError("invalid-route", "Invalid Observatory route.");
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = setTimeout(abort, timeout);
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && session?.csrf_token)
    headers["X-CSRF-Token"] = session.csrf_token;
  try {
    const response = await fetch(new URL(path, base), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      signal: controller.signal,
    });
    if (!response.headers.get("content-type")?.includes("application/json"))
      throw new APIError(
        "invalid-response",
        "The service returned an invalid response.",
        response.status,
      );
    const raw = await response.text();
    if (raw.length > 8 * 1024 * 1024)
      throw new APIError(
        "invalid-response",
        "The service response exceeded the display limit.",
        response.status,
      );
    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      throw new APIError(
        "invalid-response",
        "The service returned an invalid response.",
        response.status,
      );
    }
    if (!response.ok || payload?.ok !== true) {
      if (response.status === 401) {
        session = null;
        window.dispatchEvent(
          new CustomEvent("observatory-session-expired", {
            detail: { code: payload?.error?.code || "unauthenticated" },
          }),
        );
      }
      // Fixed facade messages only, bounded defensively even for compromised sources.
      const message =
        typeof payload?.error?.message === "string" &&
        payload.error.message.length <= 500
          ? payload.error.message
          : "The request could not be completed.";
      throw new APIError(
        payload?.error?.code || "request-failed",
        message,
        response.status,
      );
    }
    return payload.data;
  } catch (error) {
    if (error instanceof APIError) throw error;
    if (signal?.aborted)
      throw new DOMException("Request replaced", "AbortError");
    throw new APIError(
      "transport-unavailable",
      method === "GET"
        ? "Source unavailable. The last response cannot establish current state."
        : "Delivery could not be confirmed. Check Operations before taking another action.",
    );
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}
export async function workbenchRequest(path, options = {}) {
  return request(path, { ...options, base: workbenchBase });
}
export function query(path, values) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values))
    if (value !== "" && value !== null && value !== undefined)
      params.set(key, String(value));
  return path + (params.size ? "?" + params : "");
}
