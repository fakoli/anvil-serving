// Shared rendering is deliberately text-only; owner messages are never HTML.
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = String(value);
    else if (key.startsWith("on"))
      node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (["value", "checked", "disabled", "hidden"].includes(key))
      node[key] = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity))
    if (child !== null && child !== undefined)
      node.append(
        child instanceof Node ? child : document.createTextNode(String(child)),
      );
  return node;
}
export const list = (value) => (Array.isArray(value) ? value : []);
export const words = (value) =>
  String(value ?? "Not reported")
    .replaceAll("_", " ")
    .replaceAll("-", " ");
export const show = (value) =>
  value === null || value === undefined
    ? "Not reported"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
export function badge(value) {
  const key = String(value ?? "unknown").toLowerCase();
  const tone = [
    "ready",
    "healthy",
    "fresh",
    "complete",
    "succeeded",
    "success",
    "verified",
    "observed-running",
    "true",
    "passed",
    "admitted",
  ].includes(key)
    ? "success"
    : /fail|error|denied|unavailable|mismatch/.test(key) ||
        key === "manual_recovery_required"
      ? "danger"
      : /partial|stale|unknown|pending|reconcil|unresolved|drain|attention/.test(
            key,
          )
        ? "warning"
        : "";
  return el("span", { class: `badge ${tone}`, text: words(key) });
}
export const button = (label, onClick, className = "", disabled = false) =>
  el("button", {
    type: "button",
    class: className,
    onClick,
    disabled,
    text: label,
  });
export function heading(title, description, actions = []) {
  return el(
    "div",
    { class: "page-heading" },
    el(
      "div",
      {},
      el("h1", { text: title }),
      description ? el("p", { text: description }) : null,
    ),
    el("div", { class: "heading-actions" }, actions),
  );
}
export const section = (title, accessory) =>
  el("div", { class: "section-heading" }, el("h2", { text: title }), accessory);
export const notice = (message, tone = "", role = "status") =>
  el("div", { class: `notice ${tone}`, role, text: message });
export const empty = (message) => el("div", { class: "empty", text: message });
export function kv(pairs) {
  const node = el("dl", { class: "key-values" });
  for (const [key, value] of pairs)
    node.append(
      el("dt", { text: key }),
      el("dd", {}, value instanceof Node ? value : show(value)),
    );
  return node;
}
export function field(label, control, help) {
  if (!control.id) control.id = `field-${crypto.randomUUID()}`;
  return el(
    "div",
    { class: "field" },
    el("label", { for: control.id, text: label }),
    control,
    help ? el("span", { class: "meta", text: help }) : null,
  );
}
export function select(options, value, onChange, attrs = {}) {
  const node = el("select", { ...attrs, onChange });
  for (const item of options) {
    const [key, label] = Array.isArray(item) ? item : [item, words(item)];
    node.append(el("option", { value: key, text: label }));
  }
  node.value = value;
  return node;
}
export function formatTime(value, zone = "America/Los_Angeles") {
  if (!value) return "Not reported";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  if (!Number.isFinite(+date)) return "Invalid timestamp";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: zone,
    timeZoneName: "short",
  }).format(date);
}
export function timestamp(value, zone) {
  return el("time", {
    datetime: typeof value === "string" ? value : undefined,
    title: typeof value === "string" ? value : undefined,
    text: formatTime(value, zone),
  });
}
export function metric(value) {
  if (!value || typeof value !== "object") return "Not reported";
  if (value.status !== "fresh")
    return `${words(value.status || "unknown")}${value.last_known_value === null || value.last_known_value === undefined ? "" : ` · last known ${reading(value.last_known_value, value.unit)}`}${value.reason ? ` · ${value.reason}` : ""}`;
  return typeof value.value === "number" && Number.isFinite(value.value)
    ? reading(value.value, value.unit)
    : "Not reported";
}
function reading(value, unit) {
  if (unit === "ratio") return `${number(value * 100)}%`;
  if (unit === "bytes" && value >= 1024) {
    const exponent = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
    return `${number(value / 1024 ** exponent)} ${["bytes", "KiB", "MiB", "GiB", "TiB"][exponent]}`;
  }
  return `${number(value)} ${unit || ""}`.trim();
}
export function number(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value)
    : "Not reported";
}
export function safeLink(value, label) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const url = new URL(value, location.href);
    if (
      !["https:", "http:"].includes(url.protocol) ||
      url.username ||
      url.password
    )
      return null;
    return el("a", {
      href: url.href,
      target: "_blank",
      rel: "noopener noreferrer",
      text: label,
    });
  } catch {
    return null;
  }
}
export const route = (...parts) =>
  "#/" + parts.map((part) => encodeURIComponent(part)).join("/");
export function copy(label, value) {
  const output = el("span", { class: "meta" });
  return el(
    "div",
    { class: "small-stack" },
    el("code", { text: show(value) }),
    el(
      "div",
      { class: "actions" },
      button(
        `Copy ${label}`,
        async () => {
          try {
            await navigator.clipboard.writeText(String(value));
            output.textContent = "Copied";
          } catch {
            output.textContent = "Copy unavailable; select the value above.";
          }
        },
        "copy-button",
      ),
      output,
    ),
  );
}
export function jsonDetails(value, title = "Metadata") {
  return el(
    "details",
    {},
    el("summary", { text: title }),
    el("pre", { class: "evidence-json", text: JSON.stringify(value, null, 2) }),
  );
}
export function table(headers, rows, caption) {
  return el(
    "div",
    { class: "table-wrap" },
    el(
      "table",
      {},
      caption ? el("caption", { text: caption }) : null,
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          headers.map((text) => el("th", { scope: "col", text })),
        ),
      ),
      el(
        "tbody",
        {},
        rows.map((row) =>
          el(
            "tr",
            {},
            row.map((value, index) =>
              el(
                "td",
                { "data-label": headers[index] },
                value instanceof Node ? value : show(value),
              ),
            ),
          ),
        ),
      ),
    ),
  );
}
