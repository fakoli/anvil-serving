import { lookup as dnsLookup } from "node:dns/promises";
import { request as httpsRequest } from "node:https";
import { BlockList, isIP } from "node:net";

const REQUEST_LIMIT = 64;
const CONCURRENCY_LIMIT = 4;
const RESPONSE_LIMIT = 2 * 1024 * 1024;
const TOTAL_LIMIT = 12 * 1024 * 1024;
const HEADER_LIMIT = 16 * 1024;
const HEADER_COUNT_LIMIT = 32;
const FETCH_TIMEOUT = 5_000;
const LIFETIME = 120_000;
const transports = new WeakSet();
const globalIpv6 = new BlockList();
const blockedIpv6 = new BlockList();
globalIpv6.addSubnet("2000::", 3, "ipv6");
for (const [network, prefix] of [["2001::", 23], ["2001:db8::", 32], ["2002::", 16], ["3fff::", 20], ["::ffff:0:0", 96]]) blockedIpv6.addSubnet(network, prefix, "ipv6");

export class LiveTransportError extends Error { constructor(code) { super(code); this.code = code; } }
const fail = (code) => { throw new LiveTransportError(code); };
const bytes = (value) => Buffer.byteLength(value, "utf8");
const hopByHop = new Set(["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"]);
const responseHeaderNames = new Set(["cache-control", "content-language", "content-type", "etag", "last-modified", "location", "vary"]);
const fixedHeaders = Object.freeze({ accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "accept-encoding": "identity", "accept-language": "en", "user-agent": "anvil-browser-owner/1" });

function plain(value) { return value && typeof value === "object" && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype; }
function exactUrl(value) {
  if (typeof value !== "string" || bytes(value) > 2048) return null;
  try { const parsed = new URL(value); return parsed.protocol === "https:" && !parsed.username && !parsed.password && parsed.href === value ? parsed : null; } catch { return null; }
}
function publicAddress(address) {
  const family = isIP(address);
  if (family === 4) {
    const parts = address.split(".").map(Number), [a, b, c] = parts;
    return !(a === 0 || a === 10 || a === 127 || a >= 224 || (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && ((b === 0 && c === 0) || (b === 0 && c === 2) || (b === 88 && c === 99) || b === 168)) || (a === 198 && (b === 18 || b === 19 || (b === 51 && c === 100))) || (a === 203 && b === 0 && c === 113));
  }
  if (family === 6) {
    return globalIpv6.check(address, "ipv6") && !blockedIpv6.check(address, "ipv6");
  }
  return false;
}
async function vetted(lookup, hostname) {
  const results = await lookup(hostname, { all: true, verbatim: true });
  if (!Array.isArray(results) || !results.length || results.some((result) => !result || !Number.isInteger(result.family) || result.family !== isIP(result.address) || !publicAddress(result.address))) fail("dns_not_public");
  return results;
}
function headerBytes(rawHeaders) { let total = 0; for (let index = 0; index < rawHeaders.length; index += 2) total += bytes(String(rawHeaders[index])) + bytes(String(rawHeaders[index + 1])) + 4; return total; }
function withinDeadline(promise, code) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const settle = (callback, value) => { if (!settled) { settled = true; clearTimeout(timer); callback(value); } };
    const timer = setTimeout(() => settle(reject, new LiveTransportError(code)), FETCH_TIMEOUT);
    Promise.resolve(promise).then((value) => settle(resolve, value), (error) => settle(reject, error instanceof LiveTransportError ? error : new LiveTransportError("dns_failed")));
  });
}

/**
 * Creates the only live route handler accepted by createBrowserOwner. The
 * response-byte cap is an admitted-payload cap; socket/TLS buffers are outside
 * that counter and deliberately not represented as a wire quota.
 */
export async function createLiveBufferedTransport(policy) {
  if (!plain(policy) || Object.keys(policy).some((key) => !["documentUrls", "lookup", "request"].includes(key))) fail("invalid_live_transport_policy");
  const urls = Array.isArray(policy.documentUrls) ? policy.documentUrls.map(exactUrl) : [];
  if (!urls.length || urls.some((url) => !url)) fail("invalid_live_transport_policy");
  const origin = urls[0].origin;
  if (urls.some((url) => url.origin !== origin) || new Set(urls.map((url) => url.href)).size !== urls.length) fail("invalid_live_transport_policy");
  const lookup = policy.lookup || dnsLookup, request = policy.request || httpsRequest;
  if (typeof lookup !== "function" || typeof request !== "function") fail("invalid_live_transport_policy");
  await withinDeadline(vetted(lookup, urls[0].hostname), "dns_timeout");

  let closed = false, closer, requests = 0, active = 0, total = 0;
  const activeRequests = new Set();
  const deadline = setTimeout(() => { void close(); }, LIFETIME);
  const close = async ({ notify = true } = {}) => {
    if (closed) return;
    closed = true; clearTimeout(deadline);
    for (const entry of activeRequests) entry.cancel?.("transport_closed");
    activeRequests.clear();
    if (notify) await Promise.resolve(closer?.()).catch(() => {});
  };
  const transport = {
    kind: "live_buffered",
    matchesOwnerPolicy(documents, subresources) {
      return documents instanceof Set && subresources instanceof Set && documents.size === 1 && subresources.size === 1 && documents.has(origin) && subresources.has(origin);
    },
    async attachCloser(value) {
      if (typeof value !== "function") fail("invalid_live_transport_policy");
      closer = value;
      if (closed) {
        await Promise.resolve(closer()).catch(() => {});
        fail("transport_closed");
      }
    },
    async handle(route) {
      if (closed) return route.abort();
      let browserRequest, url;
      try { browserRequest = route.request(); url = new URL(browserRequest.url()); } catch { return route.abort(); }
      const navigation = Boolean(browserRequest.isNavigationRequest?.());
      if (browserRequest.method?.() !== "GET" || browserRequest.postData?.() || url.protocol !== "https:" || url.username || url.password || (navigation ? !urls.some((item) => item.href === url.href) : url.origin !== origin) || requests >= REQUEST_LIMIT || active >= CONCURRENCY_LIMIT) return route.abort();
      requests += 1; active += 1;
      try {
        const result = await bufferedGet(url);
        if (closed) return route.abort();
        await route.fulfill({ status: result.status, headers: result.headers, body: result.body });
      } catch { await route.abort().catch(() => {}); }
      finally { active -= 1; }
    },
    close,
  };
  transports.add(transport);
  return Object.freeze(transport);

  function bufferedGet(url) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const settle = (fn, value) => { if (!settled) { settled = true; clearTimeout(timer); activeRequests.delete(entry); fn(value); } };
      const rejectWith = (code) => settle(reject, new LiveTransportError(code));
      const entry = { request: null, response: null };
      entry.cancel = (code) => { entry.response?.destroy(); entry.request?.destroy(); rejectWith(code); };
      const timer = setTimeout(() => entry.cancel("fetch_timeout"), FETCH_TIMEOUT);
      const lookupOnce = (hostname, options, callback) => {
        withinDeadline(vetted(lookup, hostname), "dns_timeout").then((results) => {
          if (settled || closed) return;
          if (options?.all) callback(null, results);
          else callback(null, results[0].address, results[0].family);
        }, (error) => {
          if (!settled && !closed) {
            entry.cancel(error?.code === "dns_timeout" ? "dns_timeout" : "dns_failed");
            callback(error);
          }
        });
      };
      let outgoing;
      try {
        outgoing = request(url, { method: "GET", headers: fixedHeaders, agent: false, autoSelectFamily: false, servername: url.hostname, lookup: lookupOnce, maxHeaderSize: HEADER_LIMIT }, (response) => {
          entry.response = response;
          if (settled || closed) { response.destroy(); return; }
          const rawHeaders = response.rawHeaders || [];
          const headers = response.headers || {};
          if (closed || !Number.isInteger(response.statusCode) || response.statusCode < 200 || response.statusCode >= 300 || rawHeaders.length / 2 > HEADER_COUNT_LIMIT || headerBytes(rawHeaders) > HEADER_LIMIT || (headers["content-encoding"] && String(headers["content-encoding"]).toLowerCase() !== "identity")) { response.destroy(); return rejectWith("response_not_permitted"); }
          const declared = headers["content-length"];
          if (declared !== undefined && (!/^(0|[1-9][0-9]*)$/.test(String(declared)) || Number(declared) > RESPONSE_LIMIT || total + Number(declared) > TOTAL_LIMIT)) { response.destroy(); return rejectWith("body_limit"); }
          const chunks = []; let size = 0;
          response.on("data", (chunk) => {
            if (settled) return;
            const length = Buffer.isBuffer(chunk) ? chunk.length : Buffer.byteLength(chunk);
            if (closed || size + length > RESPONSE_LIMIT || total + length > TOTAL_LIMIT) { response.destroy(); return rejectWith("body_limit"); }
            total += length; size += length; chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
          });
          response.once("error", () => rejectWith("response_failed"));
          response.once("end", () => {
            if (settled) return;
            if (closed) return rejectWith("transport_closed");
            const headers = {};
            for (let index = 0; index < rawHeaders.length; index += 2) {
              const key = String(rawHeaders[index]).toLowerCase(), value = String(rawHeaders[index + 1]);
              if (responseHeaderNames.has(key) && !hopByHop.has(key) && key !== "location") headers[key] = value;
            }
            const body = Buffer.concat(chunks);
            headers["content-length"] = String(body.length);
            settle(resolve, { status: response.statusCode, headers, body });
          });
        });
      } catch { return rejectWith("request_failed"); }
      entry.request = outgoing; activeRequests.add(entry);
      outgoing.maxHeadersCount = HEADER_COUNT_LIMIT;
      outgoing.once("error", () => rejectWith("request_failed"));
      outgoing.end();
    });
  }
}

export function isLiveBufferedTransport(value) { return transports.has(value); }
