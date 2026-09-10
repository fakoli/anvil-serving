import {
  el,
  button,
  field,
  select,
  heading,
  notice,
  route,
  list,
} from "./views/common.js";
import {
  request,
  workbenchRequest,
  setSession,
  getSession,
} from "./views/api.js";
import {
  overviewView,
  workstationsView,
  servesView,
} from "./views/resources.js";
import { logsView } from "./views/logs.js";
import { workloadsView } from "./views/workload_view.js";
import {
  configurationView,
  clearDrafts,
  hasDirtyDraft,
} from "./views/configuration.js";
import { experimentsView } from "./views/experiments.js";
import {
  operationsView,
  openOperation,
  closeDialog,
} from "./views/operations.js";
import { loadPreferences, pages } from "./views/settings.js";
import { connectAccessView } from "./views/connect_access.js";
import { configureConnectAccess } from "./views/connect_access_api.js";
import { workbenchView } from "./views/workbench.js";
import { playgroundView } from "./views/playground.js";
import { modelsView } from "./views/models.js";
import { observabilityView } from "./views/observability.js";
import { computeView } from "./views/compute.js";
import { documentationView } from "./views/documentation.js";
import { workbenchSettingsView } from "./views/settings.js";
import { projectWorkView } from "./views/project_work.js";
const main = document.getElementById("main"),
  scope = document.getElementById("scope-bar");
const preferences = loadPreferences();
if (preferences.landing === "overview") preferences.landing = "workbench";
let fleet = null,
  settings = null,
  currentController = null,
  generation = 0,
  refreshTimer = null,
  lastRoute = "",
  authenticationMode = "legacy",
  preferencesLoaded = false,
  catalog = null,
  operations = null,
  fixtureMode = false;
let host = "",
  serve = "",
  range = preferences.range;
const titles = {
  workbench: "Workbench",
  playground: "Playground",
  models: "Models & recipes",
  work: "Anvil work",
  observability: "Observability",
  compute: "Compute",
  documentation: "Documentation",
  overview: "Overview",
  workstations: "Workstations",
  serves: "Serves",
  workloads: "Workloads",
  logs: "Logs",
  configuration: "Configuration",
  experiments: "Experiments",
  operations: "Operations",
  settings: "Settings",
  access: "Access",
};
const symbols = ["◫", "›_", "◇", "☷", "⌁", "⊞", "▤", "⚙", "⋯"];
const navigation = document.getElementById("primary-navigation");
const activePages = [
  "workbench",
  "playground",
  "models",
  "work",
  "observability",
  "compute",
  "documentation",
  "settings",
  "access",
  ...pages.filter((page) => page !== "settings"),
];
const navPages = [
  "workbench",
  "playground",
  "models",
  "work",
  "observability",
  "compute",
  "documentation",
  "settings",
];
function addNavigation(name) {
  const index = activePages.indexOf(name);
  navigation.append(
    el(
      "a",
      { href: route(name), "data-page": name },
      el("span", {
        class: "nav-symbol",
        "aria-hidden": "true",
        text: symbols[index] || "⌘",
      }),
      titles[name],
    ),
  );
}
navPages.forEach(addNavigation);
function configureAccessNavigation(session) {
  return configureConnectAccess(session);
}
document.body.classList.toggle(
  "density-compact",
  preferences.density === "compact",
);
const announce = (message) => {
  document.getElementById("announcer").textContent = message;
};
function parseRoute() {
  const hash = location.hash.slice(1);
  const pieces = (hash.startsWith("/") ? hash : "/")
    .split("/")
    .filter(Boolean)
    .map((part) => {
      try {
        return decodeURIComponent(part);
      } catch {
        return "";
      }
    });
  return {
    page:
      pieces[0] === "configuration"
        ? "models"
        : activePages.includes(pieces[0])
          ? pieces[0]
          : preferences.landing,
    id:
      pieces[0] === "workbench" &&
      ["overview", "run", "compare", "evidence", "events", "runs"].includes(
        pieces[1],
      )
        ? undefined
        : pieces[1],
    task: pieces[2],
    tab:
      pieces[0] === "workbench" &&
      ["overview", "run", "compare", "evidence", "events", "runs"].includes(
        pieces[1],
      )
        ? pieces[1]
        : pieces[3] || (pieces[0] === "work" ? "overview" : pieces[2]),
  };
}
const sidebar = document.getElementById("navigation"),
  workspace = document.querySelector(".workspace"),
  mobile = matchMedia("(max-width:1119px)");
const navClose = button(
  "Close navigation",
  () => {
    closeNavigation();
    document.getElementById("menu-toggle").focus();
  },
  "quiet-button nav-close",
);
sidebar.insertBefore(navClose, navigation);
function syncNavigation() {
  const open = document.body.classList.contains("nav-open") && mobile.matches;
  sidebar.inert = mobile.matches && !open;
  workspace.inert = open;
  navClose.hidden = !mobile.matches;
  if (open) {
    sidebar.setAttribute("role", "dialog");
    sidebar.setAttribute("aria-modal", "true");
  } else {
    sidebar.removeAttribute("role");
    sidebar.removeAttribute("aria-modal");
  }
}
mobile.addEventListener("change", syncNavigation);
syncNavigation();
function closeNavigation() {
  document.body.classList.remove("nav-open");
  document.getElementById("menu-toggle").setAttribute("aria-expanded", "false");
  syncNavigation();
}
document.getElementById("menu-toggle").addEventListener("click", () => {
  const open = document.body.classList.toggle("nav-open");
  document
    .getElementById("menu-toggle")
    .setAttribute("aria-expanded", String(open));
  syncNavigation();
  if (open) navigation.querySelector("[aria-current=page]")?.focus();
});
document.addEventListener("keydown", (event) => {
  if (
    event.key === "Tab" &&
    mobile.matches &&
    document.body.classList.contains("nav-open")
  ) {
    const focusable = [...sidebar.querySelectorAll("a,button")].filter(
      (node) => !node.hidden,
    );
    const first = focusable[0],
      last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
  if (event.key === "Escape" && document.body.classList.contains("nav-open")) {
    closeNavigation();
    document.getElementById("menu-toggle").focus();
  }
});
document.addEventListener("click", (event) => {
  if (
    document.body.classList.contains("nav-open") &&
    !event.target.closest(".sidebar") &&
    !event.target.closest("#menu-toggle")
  )
    closeNavigation();
});
document.querySelector(".skip-link").addEventListener("click", (event) => {
  event.preventDefault();
  main.focus();
});
function sessionChrome(session) {
  if (session?.authentication_mode === "connect")
    authenticationMode = "connect";
  configureAccessNavigation(session);
  document.getElementById("access-mode").textContent = session?.authenticated
    ? session.operate
      ? "Operate"
      : "View only"
    : "Signed out";
  if (session && Object.hasOwn(session, "fixture"))
    fixtureMode = session.fixture;
  document.getElementById("fixture-label").hidden = !fixtureMode;
  const build = session?.build || "";
  const shortBuild = build.startsWith("wheel-sha256:")
    ? build.slice(13, 25)
    : build.slice(0, 24);
  const buildLabel = document.getElementById("build-label");
  buildLabel.textContent = build ? `Build ${shortBuild}` : "";
  buildLabel.title = build;
  buildLabel.setAttribute("aria-label", build ? `Build ${build}` : "");
  document.getElementById("account-button").textContent = session?.authenticated
    ? "Sign out"
    : "Sign in";
}
document
  .getElementById("account-button")
  .addEventListener("click", async () => {
    if (getSession()?.authenticated) {
      if (
        hasDirtyDraft() &&
        !window.confirm("Sign out and discard local configuration edits?")
      )
        return;
      if (getSession().authentication_mode === "connect") {
        const endpoint = new URL(
          "/_anvil-connect/logout",
          window.location.origin,
        );
        if (
          endpoint.origin !== window.location.origin ||
          endpoint.pathname !== "/_anvil-connect/logout" ||
          endpoint.search ||
          endpoint.hash
        ) {
          announce("The Anvil Connect sign-out endpoint is unavailable.");
          return;
        }
        const form = document.createElement("form");
        form.method = "POST";
        form.action = endpoint.href;
        form.hidden = true;
        document.body.append(form);
        form.submit();
        return;
      }
      try {
        await request("session", { method: "DELETE" });
      } catch (error) {
        announce(error.message);
        return;
      }
      setSession(null);
      clearDrafts();
      closeDialog();
    }
    await refresh();
  });
function login(message) {
  scope.hidden = true;
  document.getElementById("system-hud").hidden = true;
  sessionChrome(null);
  if (authenticationMode === "connect") {
    main.replaceChildren(
      el(
        "section",
        { class: "panel login-card" },
        el("span", { class: "eyebrow", text: "ANVIL CONNECT" }),
        el("h1", { text: "Anvil Connect session required" }),
        el("p", {
          class: "muted",
          text:
            message ||
            "Return through Anvil Connect, then retry this workspace.",
        }),
        button("Retry identity check", () => refresh(), "primary"),
      ),
    );
    document.getElementById("connection-status").textContent =
      "Anvil Connect session required";
    return;
  }
  const username = el("input", {
      name: "username",
      autocomplete: "username",
      required: true,
    }),
    password = el("input", {
      name: "password",
      type: "password",
      autocomplete: "current-password",
      required: true,
    });
  const errors = el("div", { role: "alert" });
  if (message) errors.append(notice(message, "warning"));
  const submit = el("button", {
    type: "submit",
    class: "primary",
    text: "Sign in",
  });
  const form = el(
    "form",
    {
      onSubmit: async (event) => {
        event.preventDefault();
        submit.disabled = true;
        errors.replaceChildren();
        try {
          const session = await request("session", {
            method: "POST",
            body: { username: username.value, password: password.value },
          });
          password.value = "";
          setSession(session);
          await refresh();
        } catch (error) {
          password.value = "";
          errors.replaceChildren(notice(error.message, "danger"));
          password.focus();
        } finally {
          submit.disabled = false;
        }
      },
    },
    field("Username", username),
    field("Password", password),
    errors,
    submit,
  );
  main.replaceChildren(
    el(
      "section",
      { class: "panel login-card" },
      el("span", { class: "eyebrow", text: "ANVIL WORKBENCH" }),
      el("h1", { text: "Sign in to your workspace" }),
      el("p", {
        class: "muted",
        text: "Use your configured application account. Available observations and actions follow your assigned access.",
      }),
      form,
      el("p", {
        class: "meta",
        text: "Credentials stay out of browser storage.",
      }),
    ),
  );
  document.getElementById("connection-status").textContent =
    "Authentication required";
}
function updateScope(session, page) {
  scope.hidden = [
    "settings",
    "access",
    "workbench",
    "playground",
    "models",
    "work",
    "compute",
    "documentation",
  ].includes(page);
  if (scope.hidden) return;
  const rangeControl = select(
    [
      ["15m", "Last 15 minutes"],
      ["1h", "Last hour"],
      ["6h", "Last 6 hours"],
      ["24h", "Last 24 hours"],
      ["7d", "Last 7 days"],
    ],
    range,
    (event) => {
      range = event.target.value;
      refresh();
    },
  );
  scope.replaceChildren(
    ...[
      ["overview", "observability", "workstations", "serves", "logs"].includes(page)
        ? field("Historical range", rangeControl)
        : null,
      el("span", {
        class: "scope-note",
        text: "Current state always reflects the latest owner observation",
      }),
    ].filter(Boolean),
  );
}
function applyPreferences(saved) {
  Object.assign(preferences, loadPreferences(saved));
  range = preferences.range;
  document.body.classList.toggle(
    "density-compact",
    preferences.density === "compact",
  );
  document.body.dataset.timeZone = preferences.zone;
  document.body.dataset.defaultRange = preferences.range;
  if (fleet) updateHud();
}
function updateHud() {
  const hud = document.getElementById("system-hud");
  hud.hidden = !getSession()?.authenticated;
  if (hud.hidden) return;
  const hosts = list(fleet?.hosts);
  const targets = list(fleet?.serves).filter(
    (item) => !host || item.host_id === host,
  );
  if (host && !hosts.some((item) => item.id === host)) host = "";
  if (serve && !targets.some((item) => item.id === serve)) serve = "";
  const currentHost = hosts.find((item) => item.id === host);
  const currentServe = targets.find((item) => item.id === serve);
  const ownerState =
    currentServe?.readiness ||
    currentHost?.controller?.status ||
    fleet?.coverage?.status ||
    "Unknown";
  const active = operations
    ? list(operations.items).filter(
        (item) =>
          !["succeeded", "failed", "cancelled", "completed"].includes(
            item.status,
          ),
      ).length
    : null;
  const status = (label, value, detail) =>
    el(
      "div",
      { class: "hud-status" },
      el("span", { class: "hud-label", text: label }),
      el("strong", { text: value }),
      detail ? el("small", { text: detail }) : null,
    );
  hud.replaceChildren(
    field(
      "Compute",
      select(
        [
          ["", "All hosts"],
          ...hosts.map((item) => [item.id, item.display_name || item.id]),
        ],
        host,
        (event) => {
          host = event.target.value;
          serve = "";
          refresh();
        },
      ),
    ),
    field(
      "Target",
      select(
        [
          ["", "All declared targets"],
          ...targets.map((item) => [
            item.id,
            item.display_name || item.model || item.id,
          ]),
        ],
        serve,
        (event) => {
          serve = event.target.value;
          refresh();
        },
      ),
    ),
    status(
      "CURRENT OWNER",
      String(ownerState).replaceAll("_", " "),
      currentServe?.model ||
        (host ? "Selected host" : `${hosts.length} declared hosts`),
    ),
    status(
      "OPERATIONS",
      active === null
        ? "Not reported"
        : active
          ? `${active} active`
          : "No active runs",
      operations
        ? `${list(operations.items).length} retained`
        : "Source unavailable",
    ),
    status(
      "PI ENVIRONMENT",
      catalog?.pi?.configured ? "Configured" : "Not configured",
      "Readiness checked per task",
    ),
    el("a", { class: "hud-link", href: route("compute"), text: "Compute ↗" }),
  );
}
async function refresh({ quiet = false } = {}) {
  clearTimeout(refreshTimer);
  currentController?.abort();
  currentController = new AbortController();
  const signal = currentController.signal,
    serial = ++generation;
  const target = parseRoute();
  const currentPath = [
    target.page,
    target.id || "",
    target.task || "",
    target.tab || "",
  ].join("/");
  const navigationChanged = currentPath !== lastRoute;
  lastRoute = currentPath;
  closeNavigation();
  for (const link of navigation.querySelectorAll("a")) {
    if (link.dataset.page === target.page)
      link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  document.getElementById("breadcrumb").textContent =
    `Local research / ${titles[target.page]}${target.id ? " / " + target.id : ""}`;
  document.title = `${titles[target.page]} · Anvil Workbench`;
  if (!quiet)
    main.replaceChildren(
      el("p", {
        class: "loading",
        role: "status",
        text: "Reading current evidence…",
      }),
    );
  try {
    let session = getSession();
    if (!session?.authenticated) {
      session = await request("session", { signal });
      setSession(session);
    }
    if (serial !== generation) return;
    if (!session?.authenticated) {
      sessionChrome(session);
      login();
      return;
    }
    sessionChrome(session);
    if (!preferencesLoaded) {
      try {
        applyPreferences(await workbenchRequest("preferences", { signal }));
      } catch (error) {
        if (error.name === "AbortError") throw error;
        announce(
          "Saved preferences are unavailable; using workspace defaults.",
        );
      }
      preferencesLoaded = true;
    }
    // Access navigation is learned from the authenticated session. Re-evaluate
    // a bookmarked Access route before any normal dashboard read begins.
    if (target.page !== parseRoute().page) {
      refresh({ quiet });
      return;
    }
    if (target.page !== "access") {
      const reads = await Promise.allSettled([
        request("fleet", { signal }),
        request("settings", { signal }),
        workbenchRequest("catalog", { signal }),
        request("operations", { signal }),
      ]);
      if (signal.aborted) return;
      if (reads[0].status === "rejected") throw reads[0].reason;
      fleet = reads[0].value;
      catalog = reads[2].status === "fulfilled" ? reads[2].value : null;
      operations = reads[3].status === "fulfilled" ? reads[3].value : null;
      settings =
        reads[1].status === "fulfilled"
          ? reads[1].value
          : { integration_error: reads[1].reason.message };
      if (!Array.isArray(fleet?.hosts) || !Array.isArray(fleet?.serves))
        throw new Error(
          "Invalid fleet response. Current owner state cannot be established.",
        );
    }
    updateScope(session, target.page);
    updateHud();
    const jobs = [];
    const ctx = {
      session,
      fleet,
      settings,
      host,
      serve,
      range,
      zone: preferences.zone,
      preferences,
      operations,
      catalog,
      applyPreferences,
      signal,
      jobs,
      announce,
      refresh: () => refresh(),
      render: () => refresh({ quiet: true }),
    };
    let content;
    switch (target.page) {
      case "workbench":
        content = await workbenchView(ctx, target.id, target.tab);
        break;
      case "playground":
        content = await playgroundView(ctx);
        break;
      case "models":
        content = await modelsView(ctx, target.id);
        break;
      case "observability":
        content = await observabilityView(ctx, target.id);
        break;
      case "compute":
        content = await computeView(ctx);
        break;
      case "documentation":
        content = await documentationView(ctx, target.id);
        break;
      case "work":
        content = await projectWorkView(
          ctx,
          target.id,
          target.task,
          target.tab,
        );
        break;
      case "workstations":
        content = await workstationsView(ctx, target.id);
        break;
      case "serves":
        content = await servesView(ctx, target.id, target.tab);
        break;
      case "logs":
        content = await logsView(ctx);
        break;
      case "workloads":
        content = await workloadsView(ctx);
        break;
      case "configuration":
        content = await configurationView(ctx, target.id);
        break;
      case "experiments":
        content = await experimentsView(ctx, target.id);
        break;
      case "operations":
        content = await operationsView(ctx);
        break;
      case "settings":
        content = await workbenchSettingsView(ctx, target.id);
        break;
      case "access":
        content = await connectAccessView(ctx);
        break;
      default:
        content = await overviewView(ctx);
    }
    if (signal.aborted) return;
    main.replaceChildren(content);
    document.getElementById("connection-status").textContent =
      target.page === "access"
        ? "Anvil Connect access inventory"
        : `Source coverage: ${fleet.coverage?.status || "unknown"}`;
    if (navigationChanged) {
      main.focus({ preventScroll: true });
      window.scrollTo(0, 0);
      announce(`${titles[target.page]} loaded`);
    }
    let nextJob = 0;
    await Promise.allSettled(
      Array.from({ length: Math.min(4, jobs.length) }, async () => {
        while (nextJob < jobs.length && !signal.aborted) {
          const job = jobs[nextJob++];
          await job();
        }
      }),
    );
    if (signal.aborted) return;
    if (
      target.page === "operations" &&
      target.id &&
      (!quiet || navigationChanged)
    )
      await openOperation(target.id, ctx);
    if (
      ![
        "configuration",
        "experiments",
        "settings",
        "logs",
        "access",
        "workbench",
        "playground",
        "models",
        "work",
        "compute",
        "documentation",
      ].includes(target.page)
    )
      refreshTimer = setTimeout(() => {
        if (
          !document.hidden &&
          !document.querySelector("dialog[open]") &&
          (!main.contains(document.activeElement) ||
            document.activeElement === main)
        )
          refresh({ quiet: true });
        else scheduleResume();
      }, 5000);
  } catch (error) {
    if (signal.aborted || serial !== generation) return;
    if (error.status === 401) {
      login("Your session expired. Sign in to continue.");
      return;
    }
    main.replaceChildren(
      heading(
        titles[target.page],
        "The source could not establish current state.",
      ),
      notice(error.message, "danger", "alert"),
      button("Retry read", () => refresh(), "quiet-button"),
    );
    document.getElementById("connection-status").textContent =
      "Source unavailable";
  }
}
function scheduleResume() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    const page = parseRoute().page;
    if (
      !document.hidden &&
      !document.querySelector("dialog[open]") &&
      ![
        "configuration",
        "experiments",
        "settings",
        "logs",
        "access",
        "workbench",
        "playground",
        "models",
        "work",
        "compute",
        "documentation",
      ].includes(page) &&
      (!main.contains(document.activeElement) ||
        document.activeElement === main)
    )
      refresh({ quiet: true });
    else scheduleResume();
  }, 15000);
}
window.addEventListener("observatory-session-changed", () => {
  preferencesLoaded = false;
});
window.addEventListener("hashchange", () => {
  closeDialog();
  refresh();
});
window.addEventListener("observatory-session-expired", (event) => {
  if (event.detail?.code === "connect_assertion_denied")
    authenticationMode = "connect";
  currentController?.abort();
  clearTimeout(refreshTimer);
  clearDrafts();
  closeDialog();
  login(
    authenticationMode === "connect"
      ? "Your Anvil Connect session is no longer available."
      : "Your session expired. Sign in to continue.",
  );
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    clearTimeout(refreshTimer);
  } else if (
    ![
      "configuration",
      "experiments",
      "settings",
      "access",
      "workbench",
      "playground",
      "models",
      "work",
      "compute",
      "documentation",
    ].includes(parseRoute().page) &&
    !document.querySelector("dialog[open]")
  )
    refresh({ quiet: true });
});
refresh();
