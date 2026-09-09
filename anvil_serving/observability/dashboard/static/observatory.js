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
import { request, setSession, getSession } from "./views/api.js";
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
import { settingsView, loadPreferences, pages } from "./views/settings.js";
const main = document.getElementById("main"),
  scope = document.getElementById("scope-bar");
const preferences = loadPreferences();
let fleet = null,
  settings = null,
  currentController = null,
  generation = 0,
  refreshTimer = null,
  lastRoute = "";
let host = "",
  serve = "",
  range = preferences.range;
const titles = {
  overview: "Overview",
  workstations: "Workstations",
  serves: "Serves",
  workloads: "Workloads",
  logs: "Logs",
  configuration: "Configuration",
  experiments: "Experiments",
  operations: "Operations",
  settings: "Settings",
};
const symbols = ["◫", "▦", "◈", "≋", "▤", "⚙", "⌁", "⇄", "⋯"];
const navigation = document.getElementById("primary-navigation");
pages.forEach((name, index) =>
  navigation.append(
    el(
      "a",
      { href: route(name), "data-page": name },
      el("span", {
        class: "nav-symbol",
        "aria-hidden": "true",
        text: symbols[index],
      }),
      titles[name],
    ),
  ),
);
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
    page: pages.includes(pieces[0]) ? pieces[0] : preferences.landing,
    id: pieces[1],
    tab: pieces[2],
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
  document.getElementById("access-mode").textContent = session?.authenticated
    ? session.operate
      ? "Operate"
      : "View only"
    : "Signed out";
  document.getElementById("fixture-label").hidden = !session?.fixture;
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
  sessionChrome(null);
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
      el("span", { class: "eyebrow", text: "ANVIL OBSERVATORY" }),
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
  scope.hidden = ["settings"].includes(page);
  const hostControl = select(
    [
      ["", "All workstations"],
      ...list(fleet.hosts).map((h) => [h.id, h.display_name || h.id]),
    ],
    host,
    (event) => {
      host = event.target.value;
      serve = "";
      refresh();
    },
  );
  const serveControl = select(
    [
      ["", "All serves"],
      ...list(fleet.serves)
        .filter((s) => !host || s.host_id === host)
        .map((s) => [s.id, s.display_name || s.id]),
    ],
    serve,
    (event) => {
      serve = event.target.value;
      refresh();
    },
  );
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
  const selected = parseRoute();
  if (selected.id && ["workstations", "serves"].includes(page)) {
    const detail =
      page === "serves"
        ? list(fleet.serves).find((item) => item.id === selected.id)
        : null;
    hostControl.value = detail?.host_id || selected.id;
    hostControl.disabled = true;
    if (detail) {
      serveControl.value = detail.id;
      serveControl.disabled = true;
    }
  }
  scope.replaceChildren(
    ...[
      field("Workstation", hostControl),
      ["overview", "serves", "logs"].includes(page)
        ? field("Serve", serveControl)
        : null,
      ["overview", "workstations", "serves", "logs"].includes(page)
        ? field("Historical range", rangeControl)
        : null,
      el("span", {
        class: "scope-note",
        text: "Current state always reflects the latest owner observation",
      }),
    ].filter(Boolean),
  );
}
async function refresh({ quiet = false } = {}) {
  clearTimeout(refreshTimer);
  currentController?.abort();
  currentController = new AbortController();
  const signal = currentController.signal,
    serial = ++generation;
  const target = parseRoute();
  const currentPath = [target.page, target.id || "", target.tab || ""].join(
    "/",
  );
  const navigationChanged = currentPath !== lastRoute;
  lastRoute = currentPath;
  closeNavigation();
  for (const link of navigation.querySelectorAll("a")) {
    if (link.dataset.page === target.page)
      link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  document.getElementById("breadcrumb").textContent =
    `Observatory / ${titles[target.page]}${target.id ? " / " + target.id : ""}`;
  document.title = `${titles[target.page]} · Anvil Observatory`;
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
      login();
      return;
    }
    sessionChrome(session);
    const reads = await Promise.allSettled([
      request("fleet", { signal }),
      request("settings", { signal }),
    ]);
    if (signal.aborted) return;
    if (reads[0].status === "rejected") throw reads[0].reason;
    fleet = reads[0].value;
    settings =
      reads[1].status === "fulfilled"
        ? reads[1].value
        : { integration_error: reads[1].reason.message };
    if (!Array.isArray(fleet?.hosts) || !Array.isArray(fleet?.serves))
      throw new Error(
        "Invalid fleet response. Current owner state cannot be established.",
      );
    updateScope(session, target.page);
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
      signal,
      jobs,
      announce,
      refresh: () => refresh(),
    };
    let content;
    switch (target.page) {
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
        content = settingsView(ctx);
        break;
      default:
        content = await overviewView(ctx);
    }
    if (signal.aborted) return;
    main.replaceChildren(content);
    document.getElementById("connection-status").textContent =
      `Source coverage: ${fleet.coverage?.status || "unknown"}`;
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
    if (!["configuration", "experiments", "settings", "logs"].includes(target.page))
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
      !["configuration", "experiments", "settings", "logs"].includes(page) &&
      (!main.contains(document.activeElement) ||
        document.activeElement === main)
    )
      refresh({ quiet: true });
    else scheduleResume();
  }, 15000);
}
window.addEventListener("hashchange", () => {
  closeDialog();
  refresh();
});
window.addEventListener("observatory-session-expired", () => {
  currentController?.abort();
  clearTimeout(refreshTimer);
  clearDrafts();
  closeDialog();
  login("Your session expired. Sign in to continue.");
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    clearTimeout(refreshTimer);
    currentController?.abort();
  } else if (
    !["configuration", "experiments", "settings"].includes(parseRoute().page) &&
    !document.querySelector("dialog[open]")
  )
    refresh({ quiet: true });
});
refresh();
