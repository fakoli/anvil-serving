/* Offline Compute prototype: every host, service, and response is synthetic. */
"use strict";
const ComputeSurface = (() => {
  const esc = (v) =>
    String(v ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const hosts = {
    compute: {
      name: "Compute",
      caption: "Primary model owner",
      state: "Owner ready",
    },
    macbook: {
      name: "MacBook",
      caption: "Native MLX candidate",
      state: "Owner ready",
    },
    harness: {
      name: "Harness",
      caption: "Client and checks",
      state: "Owner unavailable",
    },
  };
  let selected = "atlas",
    tab = "logs",
    filter = "",
    pending = null;
  const consoleDrafts = {};
  const extra = { media: true, speech: true };
  const items = (location, api) => {
    if (location === "compute")
      return [
        {
          id: "atlas",
          kind: "model",
          model: "Atlas 32B",
          title: "Atlas 32B",
          runtime: "Docker container",
          identity: "sample-atlas",
          detail: "Declared local tier · synthetic",
          running: !!api.getDeployment("Atlas 32B"),
        },
        {
          id: "orion",
          kind: "model",
          model: "Orion 70B",
          title: "Orion 70B",
          runtime: "Docker container",
          identity: "sample-orion",
          detail: "Declared candidate · prior sample gate failed",
          startupBlock:
            "This two-GPU candidate needs a new resource-owner placement review. Existing GPU reservations remain in place even when a workload is stopped; this demo does not release or replace them.",
          running: !!api.getDeployment("Orion 70B"),
        },
        {
          id: "media",
          title: "Media bridge",
          runtime: "Docker container",
          identity: "sample-media",
          detail: "Optional capability bridge · synthetic",
          running: extra.media,
        },
      ];
    if (location === "macbook")
      return [
        {
          id: "finch",
          kind: "model",
          model: "Finch 8B",
          title: "Finch 8B",
          runtime: "Native MLX service",
          identity: "mlx-finch.service",
          detail: "Candidate service · synthetic",
          running: !!api.getDeployment("Finch 8B"),
        },
        {
          id: "speech",
          title: "Native speech services",
          runtime: "Native processes",
          identity: "speech-input.service · speech-output.service",
          detail: "Paired local processes · synthetic",
          running: extra.speech,
        },
      ];
    return [];
  };
  const active = (api) =>
    items(api.location, api).find((x) => x.id === selected) ||
    items(api.location, api)[0];
  const recipeFor = (service, api) =>
    service?.kind === "model"
      ? api.getDeployment(service.model) || api.getRecipe(service.model)
      : null;
  const recipeIdentity = (recipe) =>
    recipe
      ? `${recipe.id || "unnamed recipe"} · revision ${recipe.revision || recipe.version || "unversioned"}`
      : "No declared recipe";
  const sameRecipe = (a, b) =>
    !!a &&
    !!b &&
    a.id === b.id &&
    (a.revision || a.version || "") === (b.revision || b.version || "") &&
    a.location === b.location &&
    a.model === b.model;
  const status = (running) =>
    `<span class="compute-status ${running ? "up" : "down"}"><i></i>${running ? "Running" : "Stopped"}</span>`;
  const draftKey = (location, service) => `${location}:${service.id}`;
  const focusAfterRender = (api, id) => {
    api.render();
    queueMicrotask(() => document.getElementById(id)?.focus());
  };
  function hostCards(api) {
    return `<div class="compute-hosts" role="group" aria-label="Compute locations">${Object.entries(
      hosts,
    )
      .map(
        ([id, h]) =>
          `<button type="button" id="compute-host-${id}" class="compute-host ${api.location === id ? "selected" : ""}" data-action="compute-host-${id}" aria-pressed="${api.location === id}"><span class="compute-host-mark">${id === "compute" ? "01" : id === "macbook" ? "02" : "03"}</span><span><strong>${h.name}</strong><small>${h.caption}</small></span><em>${h.state}</em></button>`,
      )
      .join("")}</div>`;
  }
  function logLines(s) {
    return [
      `09:42:16 ${s.identity} · readiness sample: ${s.running ? "ready" : "not running"}`,
      `09:42:13 ${s.identity} · synthetic event retained for review`,
      "09:41:58 control plane · offline prototype; no service contacted",
    ].filter(
      (line) => !filter || line.toLowerCase().includes(filter.toLowerCase()),
    );
  }
  function detail(s, api) {
    if (!s)
      return `<section class="compute-detail empty"><p>Select an available workload to inspect its offline sample details.</p></section>`;
    const disabled = !s.running ? "disabled" : "";
    const draft = consoleDrafts[draftKey(api.location, s)] || {
      command: "",
      output: "[demo] No command has been entered.",
    };
    const recipe = recipeFor(s, api);
    const body =
      tab === "logs"
        ? `<label class="compute-filter">Filter sample logs<input id="compute-filter" data-compute-input="filter" value="${esc(filter)}" placeholder="Literal text filter"></label><pre id="compute-log" class="compute-log" aria-label="Selected service sample logs">${esc(logLines(s).join("\n") || "No matching synthetic log lines.")}</pre>`
        : tab === "configuration"
          ? `<dl class="compute-config"><div><dt>Runtime</dt><dd>${esc(s.runtime)}</dd></div><div><dt>Service identity</dt><dd>${esc(s.identity)}</dd></div><div><dt>Recipe / revision</dt><dd>${s.kind === "model" ? esc(recipeIdentity(recipe)) : "Managed bridge sample"}</dd></div><div><dt>Scope</dt><dd>Offline prototype only</dd></div></dl>`
          : `<form class="compute-console" data-compute-form="exec"><label for="compute-command">Command for ${esc(s.identity)}</label><input id="compute-command" data-compute-input="command" value="${esc(draft.command)}" ${disabled} placeholder="e.g. status"><button type="submit" class="primary" ${disabled}>Run demo command</button><pre id="compute-exec-output" aria-live="polite">${esc(draft.output)}</pre></form>`;
    return `<section class="compute-detail"><div class="compute-detail-head"><div><div class="eyebrow">SELECTED WORKLOAD</div><h2>${esc(s.title)}</h2><p>${esc(s.runtime)} · <code>${esc(s.identity)}</code></p></div>${status(s.running)}</div><div class="compute-controls"><button type="button" class="small quiet" data-action="compute-start" ${s.running || api.location === "harness" ? "disabled" : ""}>Start</button><button type="button" class="small danger" data-action="compute-stop" ${disabled}>Stop</button></div><div class="compute-tabs" role="tablist" aria-label="${esc(s.title)} details">${[
      ["logs", "Logs"],
      ["configuration", "Configuration"],
      ["exec", "Exec"],
    ]
      .map(
        ([id, label]) =>
          `<button type="button" id="compute-tab-${id}" role="tab" aria-selected="${tab === id}" aria-controls="compute-tab-panel" tabindex="${tab === id ? "0" : "-1"}" data-action="compute-tab-${id}">${label}</button>`,
      )
      .join(
        "",
      )}</div><div id="compute-tab-panel" class="compute-tab-panel" role="tabpanel" tabindex="0" aria-labelledby="compute-tab-${tab}">${body}</div></section>`;
  }
  function render(api) {
    const rows = items(api.location, api);
    if (!rows.some((item) => item.id === selected))
      selected = rows[0]?.id || "";
    const workload = active(api);
    return `<section class="compute-surface"><div class="compute-heading"><div><div class="eyebrow">CONTROL PLANE / COMPUTE</div><h1>Compute</h1><p>Inspect declared services, review lifecycle actions, and use a non-executing console.</p></div><a href="#models" class="text-link">Browse model recipes ↗</a></div><div class="compute-demo" role="note"><strong>Demo workspace</strong><span>All hosts, service state, logs, and command output are synthetic. No operation reaches a runtime.</span></div>${hostCards(api)}<div class="compute-layout"><section class="compute-workloads"><div class="compute-section-head"><div><div class="eyebrow">${esc(hosts[api.location].name)}</div><h2>Declared workloads</h2></div><span>${rows.length} service${rows.length === 1 ? "" : "s"}</span></div>${rows.length ? `<table class="compute-table" aria-label="Declared workloads"><thead><tr><th scope="col">Workload</th><th scope="col">Runtime / identity</th><th scope="col">Status</th><th scope="col"><span class="sr-only">Selection</span></th></tr></thead><tbody>${rows.map((s) => `<tr class="${selected === s.id ? "selected" : ""}"><td><strong>${esc(s.title)}</strong><small>${esc(s.detail)}</small></td><td><b>${esc(s.runtime)}</b><code>${esc(s.identity)}</code></td><td>${status(s.running)}</td><td><button type="button" id="compute-workload-${s.id}" class="small quiet" data-action="compute-workload-${s.id}" aria-pressed="${selected === s.id}">Select</button></td></tr>`).join("")}</tbody></table>` : `<div class="compute-empty"><strong>No declared workloads</strong><p>This owner is unavailable and has no models assigned.</p></div>`}</section>${detail(workload, api)}</div></section>`;
  }
  function review(action, api) {
    const s = active(api);
    if (!s) return true;
    if (action === "start" && s.startupBlock) {
      pending = null;
      api.openDialog(
        "Startup needs placement review",
        `<p>${esc(s.title)} · ${esc(hosts[api.location].name)}</p><div class="notice">${esc(s.startupBlock)}</div>`,
        api.btn("Close", "close"),
      );
      return true;
    }
    const recipe = recipeFor(s, api);
    pending = {
      action,
      service: structuredClone(s),
      location: api.location,
      running: s.running,
      recipe: recipe && structuredClone(recipe),
    };
    api.openDialog(
      action === "start" ? "Review startup" : "Review stop",
      `<p>${action === "start" ? "Start" : "Stop"} this synthetic workload?</p><dl class="compute-review"><div><dt>Host</dt><dd>${esc(hosts[api.location].name)}</dd></div><div><dt>Model / service</dt><dd>${esc(s.title)}</dd></div><div><dt>Container / process</dt><dd>${esc(s.identity)}</dd></div>${s.kind === "model" ? `<div><dt>Recipe / revision</dt><dd>${esc(recipeIdentity(recipe))}</dd></div><div><dt>Configuration</dt><dd>${esc(recipe?.precision)} · context ${esc(recipe?.context)} · concurrency ${esc(recipe?.concurrency)}</dd></div><div><dt>Recorded gate</dt><dd>${esc(recipe?.gate || "Unknown")}</dd></div>` : ""}</dl><p class="muted">The demo records only local page state.</p>`,
      `<button type="button" class="quiet" data-action="close">Cancel</button><button type="button" class="primary" data-action="compute-confirm-${action}">Confirm ${action}</button>`,
    );
    return true;
  }
  function action(name, api) {
    if (name.startsWith("compute-host-")) {
      const id = name.slice(13);
      if (!hosts[id]) return false;
      api.setLocation(id);
      selected = id === "compute" ? "atlas" : id === "macbook" ? "finch" : "";
      tab = "logs";
      filter = "";
      api.announce(`Selected ${hosts[id].name}`);
      focusAfterRender(api, `compute-host-${id}`);
      return true;
    }
    if (name.startsWith("compute-workload-")) {
      selected = name.slice(17);
      tab = "logs";
      filter = "";
      focusAfterRender(api, `compute-workload-${selected}`);
      return true;
    }
    if (name.startsWith("compute-tab-")) {
      tab = name.slice(12);
      focusAfterRender(api, `compute-tab-${tab}`);
      return true;
    }
    if (name === "compute-start" || name === "compute-stop")
      return review(name.slice(8), api);
    if (name === "compute-confirm-start" || name === "compute-confirm-stop") {
      if (!pending) return true;
      const { service, action: op, location, running, recipe } = pending;
      const current = active(api);
      const currentRecipe = recipeFor(current, api);
      const unchanged =
        api.location === location &&
        current?.id === service.id &&
        current.running === running &&
        (!service.kind || sameRecipe(recipe, currentRecipe));
      if (!unchanged) {
        pending = null;
        api.closeDialog();
        api.announce("Review expired; the workload state or recipe changed.");
        api.render();
        return true;
      }
      if (service.kind === "model")
        op === "start"
          ? api.startModel(service.model, recipe)
          : api.stopModel(service.model);
      else extra[service.id] = op === "start";
      api.closeDialog();
      api.announce(
        `${op === "start" ? "Started" : "Stopped"} ${service.title} in demo`,
      );
      pending = null;
      api.render();
      return true;
    }
    return false;
  }
  function input(event, api) {
    const target = event.target,
      key = target?.dataset?.computeInput;
    if (target?.getAttribute?.("role") === "tab" && event.type === "keydown") {
      const ids = ["logs", "configuration", "exec"],
        index = ids.indexOf(tab);
      const next =
        event.key === "Home"
          ? 0
          : event.key === "End"
            ? ids.length - 1
            : event.key === "ArrowRight"
              ? (index + 1) % ids.length
              : event.key === "ArrowLeft"
                ? (index + ids.length - 1) % ids.length
                : -1;
      if (next < 0) return false;
      event.preventDefault();
      tab = ids[next];
      focusAfterRender(api, `compute-tab-${tab}`);
      return true;
    }
    if (!key) return false;
    const service = active(api);
    if (!service) return true;
    if (key === "filter") {
      filter = target.value;
      const log = document.getElementById("compute-log");
      if (log)
        log.textContent =
          logLines(service).join("\n") || "No matching synthetic log lines.";
    }
    if (key === "command") {
      const draft = consoleDrafts[draftKey(api.location, service)] || {
        output: "[demo] No command has been entered.",
      };
      draft.command = target.value;
      consoleDrafts[draftKey(api.location, service)] = draft;
    }
    return true;
  }
  function submit(event, api) {
    if (event.target?.dataset?.computeForm !== "exec") return false;
    event.preventDefault();
    const service = active(api);
    if (!service || !service.running) return true;
    const draft = consoleDrafts[draftKey(api.location, service)] || {
      command: "",
    };
    draft.output = draft.command
      ? `[demo] command not executed\n$ ${draft.command}`
      : "[demo] No command has been entered.";
    consoleDrafts[draftKey(api.location, service)] = draft;
    const output = document.getElementById("compute-exec-output");
    if (output) output.textContent = draft.output;
    api.announce("Demo command was not executed");
    return true;
  }
  return { render, action, input, submit };
})();
