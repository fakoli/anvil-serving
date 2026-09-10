import { button, el, field, heading, kv, notice, timestamp } from "./common.js";
import { mutateConnectAccess, readConnectAccess } from "./connect_access_api.js";

let selectedKind = "users";
let cursor = null;

const decimal = (value) => typeof value === "string" && /^[0-9]{1,20}$/.test(value);
const human = (value) => typeof value === "string" && /^human:[0-9a-f]{64}$/.test(value);
const sessionId = (value) => typeof value === "string" && /^[A-Za-z0-9._~-]{1,256}$/.test(value);
const resource = (value) => typeof value === "string" && /^[a-z][a-z0-9-]{0,62}$/.test(value);
const resourceList = (value) =>
  Array.isArray(value) &&
  value.length >= 1 && value.length <= 64 &&
  value.every(resource) &&
  new Set(value).size === value.length;
const text = (value, fallback = "Unavailable") =>
  typeof value === "string" && value ? value : fallback;

function resourcesEditor(item, ctx) {
  item = item && typeof item === "object" && !Array.isArray(item) ? item : {};
  const valid = human(item.id) && decimal(item.generation) && typeof item.disabled === "boolean" && resourceList(item.resources);
  const resources = valid ? item.resources : [];
  const input = el("textarea", {
    rows: "3",
    value: resources.join("\n"),
    "aria-label": `Resources for ${text(item.id)}`,
    disabled: !valid,
  });
  const submit = async (disabled) => {
    const next = input.value
      .split(/[,\n]/)
      .map((value) => value.trim())
      .filter(Boolean);
    if (!valid) {
      ctx.announce("This user record cannot be changed. Refresh the inventory.");
      return;
    }
    if (!resourceList(next)) {
      ctx.announce("Enter at least one declared browser resource ID; wildcard grants are not supported.");
      return;
    }
    const target = item.id;
    const action = disabled ? "Disable access" : item.disabled ? "Enable access" : "Save access";
    if (!window.confirm(`${action} for ${target}?`)) return;
    try {
      await mutateConnectAccess({
        action: "human-update",
        request_id: crypto.randomUUID(),
        expected_generation: item.generation,
        principal: item.id,
        disabled,
        resources: next,
      }, ctx.signal);
      ctx.announce(`${action} applied. Reloading access inventory.`);
      cursor = null;
      ctx.refresh();
    } catch (error) {
      ctx.announce(error.message);
    }
  };
  return el(
    "section",
    { class: "panel stack" },
    el("h3", { text: text(item.id) }),
    kv([
      ["Generation", text(item.generation)],
      ["Administrator", item.administrator === true ? "Yes" : "No"],
      ["Access", item.disabled === true ? "Disabled" : "Enabled"],
    ]),
    field("Resources", input),
    el(
      "div",
      { class: "actions" },
      button("Save resources", () => submit(item.disabled), "quiet-button", !valid),
      item.disabled === true
        ? button("Enable access", () => submit(false), "primary", !valid)
        : button("Disable access", () => submit(true), "danger", !valid),
    ),
  );
}

function sessionCard(item, access, ctx) {
  item = item && typeof item === "object" && !Array.isArray(item) ? item : {};
  const id = text(item.id, "Unavailable");
  const type = item.type === "browser" || item.type === "terminal" ? item.type : "unknown";
  const valid = type !== "unknown" && decimal(item.generation) && sessionId(item.id);
  const currentBrowser = type === "browser" && item.id === access.current_session;
  const disconnect = async () => {
    if (!valid) {
      ctx.announce("This session record cannot be changed. Refresh the inventory.");
      return;
    }
    const warning = currentBrowser
      ? " Disconnecting this browser session also revokes terminal sessions it approved."
      : "";
    if (!window.confirm(`Disconnect ${id}?${warning}`)) return;
    try {
      await mutateConnectAccess({
        action: "session-revoke",
        request_id: crypto.randomUUID(),
        expected_generation: item.generation,
        session_type: type,
        session_id: item.id,
      }, ctx.signal);
      ctx.announce("Session access revoked. Reloading access inventory.");
      cursor = null;
      ctx.refresh();
    } catch (error) {
      ctx.announce(error.message);
    }
  };
  return el(
    "section",
    { class: "panel stack" },
    el("h3", { text: id }),
    kv([
      ["Type", type],
      ["Principal", text(item.principal)],
      ["Resource", text(item.resource)],
      ["Authorization status", text(item.status)],
      ["Issued", timestamp(item.issued_at, ctx.zone)],
      ["Expires", timestamp(item.expires_at, ctx.zone)],
      ["Generation", text(item.generation)],
      ["Source session", text(item.source_session, "None")],
    ]),
    button("Disconnect", disconnect, "danger", !valid),
  );
}

function selector(ctx) {
  const choose = (kind) => {
    selectedKind = kind;
    cursor = null;
    ctx.refresh();
  };
  return el(
    "div",
    { class: "actions", role: "tablist", "aria-label": "Access inventory" },
    el("button", {
      type: "button",
      class: selectedKind === "users" ? "primary" : "quiet-button",
      onClick: () => choose("users"),
      "aria-selected": String(selectedKind === "users"),
      text: "Users",
    }),
    el("button", {
      type: "button",
      class: selectedKind === "sessions" ? "primary" : "quiet-button",
      onClick: () => choose("sessions"),
      "aria-selected": String(selectedKind === "sessions"),
      text: "Connect sessions",
    }),
    button("Refresh access inventory", () => ctx.refresh(), "quiet-button"),
  );
}

export async function connectAccessView(ctx) {
  let access;
  try {
    access = await readConnectAccess(selectedKind, cursor, ctx.signal);
  } catch (error) {
    return el(
      "div",
      {},
      heading("Access", "Manage existing Anvil Connect access without reading fleet telemetry."),
      selector(ctx),
      notice(error.message, error.code === "access-forbidden" ? "warning" : "danger", "alert"),
    );
  }
  const cards = selectedKind === "users"
    ? access.items.map((item) => resourcesEditor(item, ctx))
    : access.items.map((item) => sessionCard(item, access, ctx));
  return el(
    "div",
    {},
    heading("Access", "Manage existing Anvil Connect access and revoke authorized sessions."),
    selector(ctx),
    selectedKind === "users"
      ? notice("Disable access revokes Connect authorization. It does not delete an identity-provider account.")
      : notice("Authorization status and expiry describe issued access. They do not claim that a device is online."),
    selectedKind === "users"
      ? notice("Creating identity-provider accounts or invitations is not available here.")
      : null,
    el("div", { class: "grid two" }, ...(cards.length ? cards : [notice("No authorized records were returned.")])),
    access.next_cursor
      ? button("Load next page", () => {
          cursor = access.next_cursor;
          ctx.refresh();
        }, "quiet-button")
      : null,
  );
}
