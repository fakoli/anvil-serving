import {
  el,
  list,
  heading,
  section,
  field,
  select,
  notice,
  kv,
  badge,
  jsonDetails,
  timestamp,
  copy,
} from "./common.js";
export const pages = [
  "overview",
  "workstations",
  "serves",
  "workloads",
  "logs",
  "configuration",
  "experiments",
  "operations",
  "settings",
];
export function loadPreferences() {
  const defaults = {
    landing: "overview",
    density: "comfortable",
    zone: "America/Los_Angeles",
    range: "1h",
  };
  try {
    const value = JSON.parse(
      localStorage.getItem("anvil-observatory-display") || "{}",
    );
    for (const [key, allowed] of Object.entries({
      landing: pages,
      density: ["comfortable", "compact"],
      zone: ["America/Los_Angeles", "UTC"],
      range: ["15m", "1h", "6h", "24h", "7d"],
    }))
      if (allowed.includes(value[key])) defaults[key] = value[key];
  } catch {
    /* Storage is optional; it contains display preferences only. */
  }
  return defaults;
}
export function settingsView(ctx) {
  const prefs = ctx.preferences;
  const change = (key, value) => {
    prefs[key] = value;
    try {
      localStorage.setItem("anvil-observatory-display", JSON.stringify(prefs));
    } catch {}
    document.body.classList.toggle(
      "density-compact",
      prefs.density === "compact",
    );
    ctx.announce("Display preference saved.");
  };
  const data = ctx.settings || {};
  const integrations = Array.isArray(data.integrations)
    ? data.integrations
    : data.integrations
      ? [
          {
            id: data.integrations.source || "metrics",
            label: "Metrics integration",
            ...data.integrations,
          },
        ]
      : [];
  return el(
    "div",
    {},
    heading(
      "Settings",
      "Display preferences, declared integrations, and the access granted to this session.",
    ),
    el(
      "div",
      { class: "grid two" },
      el(
        "section",
        { class: "panel stack" },
        el("h2", { text: "Display" }),
        field(
          "Default landing page",
          select(pages, prefs.landing, (e) =>
            change("landing", e.target.value),
          ),
        ),
        field(
          "Table density",
          select(["comfortable", "compact"], prefs.density, (e) =>
            change("density", e.target.value),
          ),
        ),
        field(
          "Time zone",
          select(
            [
              ["America/Los_Angeles", "Pacific · America/Los_Angeles"],
              ["UTC", "UTC · evidence view"],
            ],
            prefs.zone,
            (e) => change("zone", e.target.value),
          ),
        ),
        field(
          "Preferred historical range",
          select(["15m", "1h", "6h", "24h", "7d"], prefs.range, (e) =>
            change("range", e.target.value),
          ),
        ),
        el("p", {
          class: "meta",
          text: "Only these harmless display preferences are stored on this device.",
        }),
      ),
      el(
        "section",
        { class: "panel stack" },
        el("h2", { text: "Access" }),
        kv([
          ["Identity", ctx.session.identity],
          ["Role", ctx.session.role],
          ["Mode", ctx.session.operate ? "Operate" : "View only"],
          ["Session expires", timestamp(ctx.session.expires_at, prefs.zone)],
        ]),
        notice(
          ctx.session.operate
            ? "Actions still require current resource and owner authorization."
            : data.access_reason ||
                "Operate mode is unavailable for this session. View access does not grant lifecycle authority.",
        ),
      ),
    ),
    section("Integrations"),
    el(
      "div",
      { class: "grid two" },
      integrations.length
        ? integrations.map((item) =>
            el(
              "section",
              { class: "panel stack" },
              el("h3", { text: item.label || item.id }),
              badge(item.status),
              kv([
                ["Declared identity", item.id],
                ["Expected version", item.expected_version],
                ["Observed version", item.observed_version],
                ["Observed", timestamp(item.observed_at, prefs.zone)],
                ["Detail", item.reason],
              ]),
            ),
          )
        : notice(
            "Integration health has not been reported. Declared configuration alone does not prove connectivity.",
          ),
    ),
    section("About"),
    el(
      "div",
      { class: "panel" },
      kv([
        ["Frontend build", copy("build identity", ctx.session.build)],
        ["Application revision", data.build || data.revision],
        ["Schema versions", data.schema_versions || data.schemas],
      ]),
      jsonDetails(data.permitted_catalog || {}, "Permitted action catalog"),
    ),
  );
}
