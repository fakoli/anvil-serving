"use strict";
// Host shell behavior with deterministic owner/iframe. Native pin capabilities have a separate gate.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

(async () => {
  const output = path.resolve(process.argv[2] || "test-results/host-browser");
  fs.mkdirSync(output, { recursive: true });
  const staticRoot = path.resolve(__dirname, "../../../anvil_serving/observability/dashboard/static");
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || undefined, headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [], opens = [], creates = new Map();
    let lose = true, requests = 0, nativeStatus = "running", nativeUnavailable = false, nativeReads = 0, sourceEpoch = 1;
    const nativeRows = Array.from({ length: 105 }, (_, index) => ({ id: `native-run-${index}`, source: "workspace-host-pi", native_id: `native-${index}`, title: `Native run ${index}`, context_project_id: "product", status: index === 104 ? "running" : "retained", native_state: index === 104 ? "running" : "retained", freshness: "fresh" }));
    let items = [{ native_id: "retained", title: "Existing native", running: false, archived: false, source: "native", provenance: "unassigned" }];
    page.on("pageerror", error => errors.push(error.message));
    await page.exposeFunction("recordOpen", value => opens.push(value));
    await page.route("https://pi.example.test/**", route => route.fulfill({ contentType: "text/html", body: `<!doctype html><title>Native owner fixture</title>
      <label>Native draft<textarea id="draft"></textarea></label><div id="selected"></div><script>
      let sequence=0; window.bridgeId=crypto.randomUUID();
      addEventListener('message',event=>{ if(event.origin==='https://workbench.example.test' && event.source===parent && event.data.type==='open_session' && event.data.bridge_id===window.bridgeId) {
        document.querySelector('#selected').textContent=event.data.native_id; window.recordOpen(event.data);
        parent.postMessage({v:1,type:'session_changed',bridge_id:window.bridgeId,native_id:event.data.native_id,sequence:++sequence},event.origin);
      }});
      parent.postMessage({v:1,type:'ready',bridge_id:window.bridgeId,sequence:0},'https://workbench.example.test');
      </script>` }));
    await page.route("https://workbench.example.test/**", async route => {
      const url = new URL(route.request().url()), name = url.pathname;
      if (name.includes("/api/observatory/v1/")) {
        const endpoint = name.split("/api/observatory/v1/")[1];
        let data;
        if (endpoint === "run-sources") data = { items: [{ id: "workspace-host-pi", label: "Native Pi sessions", kind: "workspace", authority_key: String(sourceEpoch) }] };
        else if (endpoint === "runs/workspace-host-pi") {
          nativeReads++;
          if (nativeUnavailable) { await route.fulfill({ status: 503, json: { ok: false, error: { code: "source_unavailable", message: "Owner unavailable" } } }); return; }
          data = { items: url.searchParams.has("cursor") ? nativeRows.slice(100) : nativeRows.slice(0, 100), next_cursor: url.searchParams.has("cursor") ? null : "snapshot.100",
            refresh: { items: nativeRows.map(row => ({ id: row.id, status: row.id === "native-run-104" ? nativeStatus : row.status, native_state: row.id === "native-run-104" ? nativeStatus : row.status, freshness: "fresh" })) }, sources: [{ status: "fresh" }] };
        } else throw Error(`Unexpected source endpoint ${endpoint}`);
        await route.fulfill({ json: { ok: true, data } }); return;
      }
      if (name.includes("/api/workbench/v1/")) {
        const endpoint = name.split("/api/workbench/v1/")[1];
        let data;
        const body = route.request().postDataJSON();
        if (endpoint.endsWith("/preferences")) data = { roots: [{ id: "primary", label: "Primary", task_access: "read-write", state_root: true }, { id: "context", label: "Context", task_access: "read-only", state_root: false }], defaults: { primary_root_id: "primary", writable_root_ids: [] } };
        else if (endpoint.endsWith("/tree")) data = { items: [{ name: "guide.txt", kind: "file" }], truncated: false };
        else if (endpoint.endsWith("/worktree")) data = { branch: "feature", head: "a".repeat(40), dirty: true };
        else if (endpoint.endsWith("/text")) data = { content: "<script>neverExecute()</script>\nProject guide" };
        else if (endpoint.endsWith("/diff")) data = { diff: "+ reviewed change" };
        else if (endpoint.endsWith("/threads") && route.request().method() === "GET") data = { items };
        else if (endpoint.endsWith("/rename")) { items.find(item => item.request_id === endpoint.split("/").at(-2)).title = body.title; data = {}; }
        else if (endpoint.endsWith("/archive")) { items.find(item => item.request_id === endpoint.split("/").at(-2)).archived = body.archived; data = {}; }
        else if (endpoint.endsWith("/threads") || endpoint.endsWith("/associate")) {
          requests++;
          if (!creates.has(body.request_id)) {
            const item = { project_id: "product", request_id: body.request_id, native_id: body.native_id || "created-once", title: "New host thread", archived: false, running: true, source: "host-pi", provenance: "associated" };
            creates.set(body.request_id, item); items = [item, ...items.filter(row => row.native_id !== item.native_id)];
          }
          data = creates.get(body.request_id);
          if (lose) { lose = false; await route.abort("failed"); return; }
        } else throw Error(`Unexpected fixture endpoint ${endpoint}`);
        await route.fulfill({ json: { ok: true, data } }); return;
      }
      if (name.endsWith(".js") || name.endsWith(".css") || name.endsWith(".ttf")) {
        const file = path.join(staticRoot, name);
        await route.fulfill({ contentType: name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "font/ttf", body: fs.readFileSync(file) }); return;
      }
      await route.fulfill({ contentType: "text/html", body: `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/observatory.css"><link rel="stylesheet" href="/workbench.css"><main></main><dialog id="operation-dialog"></dialog><script type="module">
      import {hostPiView} from '/views/host_pi.js';
      import {workbenchView} from '/views/workbench.js';
      const ctx={session:{identity:'owner'},signal:new AbortController().signal,refresh:()=>location.reload()};
      const catalog={host_pi:{origin:'https://pi.example.test',parent_origin:location.origin,bridge:true,authority:'Owner host session — tools use operator account access'},projects:[{id:'product',label:'Product'}]};
      document.querySelector('main').append(location.search.includes('runs=1') ? await workbenchView(ctx, new URL(location.href).searchParams.get('selected'), new URL(location.href).searchParams.has('selected') ? 'events' : 'runs') : await hostPiView(ctx,catalog));
      </script>` });
    });
    await page.goto("https://workbench.example.test/#/playground");
    const nativeTabLink = page.getByRole("link", { name: "Open Pi Web", exact: true });
    assert.equal(await nativeTabLink.getAttribute("href"), "https://pi.example.test/");
    assert.equal(await nativeTabLink.getAttribute("target"), "_blank");
    assert.equal(await nativeTabLink.getAttribute("rel"), "noopener noreferrer");
    const threadPanel = page.locator(".host-pi-navigation");
    const openThreads = async () => {
      if (!await threadPanel.evaluate((node) => node.open))
        await threadPanel.getByText("Project threads", { exact: true }).click();
    };
    await openThreads();
    await page.getByRole("button", { name: /Existing native/ }).click();
    await page.waitForFunction(() => !document.querySelector(".host-pi-navigation").open);
    await openThreads();
    await page.frameLocator("iframe").locator("#selected").filter({ hasText: "retained" }).waitFor();
    const oldBridge = await page.frameLocator("iframe").locator("body").evaluate(() => window.bridgeId);
    await page.getByRole("button", { name: "Reload Pi", exact: true }).click();
    await page.frameLocator("iframe").locator("#selected").filter({ hasText: "retained" }).waitFor();
    await openThreads();
    await page.frameLocator("iframe").locator("body").evaluate((_, old) => parent.postMessage({ v: 1, type: "session_changed", bridge_id: old, native_id: "stale", sequence: 999 }, 'https://workbench.example.test'), oldBridge);
    assert.equal(new URL(page.url()).searchParams.get("pi-thread"), "retained");
    await page.frameLocator("iframe").getByLabel("Native draft").fill("Draft stays in native UI");
    await page.getByLabel("Search threads").fill("missing");
    await page.getByText("No matching threads.").waitFor();
    await page.getByLabel("Search threads").fill("");
    assert.equal(await page.frameLocator("iframe").getByLabel("Native draft").inputValue(), "Draft stays in native UI");
    await openThreads();
    await page.getByRole("button", { name: "New host thread", exact: true }).click();
    await page.getByRole("button", { name: "Reconcile original thread", exact: true }).waitFor();
    assert.equal(creates.size, 1);
    await page.reload();
    await openThreads();
    await page.getByRole("button", { name: "Reconcile original thread", exact: true }).click();
    const threadStatus = threadPanel.locator(".meta[role=status]");
    await threadStatus.filter({ hasText: "Native thread ready." }).waitFor({ state: "attached" });
    assert.equal(await threadStatus.textContent(), "Native thread ready.");
    assert.equal(creates.size, 1); assert.equal(requests, 2);
    assert.equal(new URL(page.url()).searchParams.get("pi-thread"), "created-once");
    await openThreads();
    await page.getByLabel("Thread label").fill("Retained label");
    await page.getByRole("button", { name: "Save thread label", exact: true }).click();
    await page.getByRole("button", { name: /Retained label · running/ }).waitFor();
    await page.getByRole("button", { name: "Archive thread", exact: true }).click();
    await page.getByRole("button", { name: /Retained label · running/ }).waitFor({ state: "hidden" });
    await page.getByLabel("Show archived threads").check();
    await page.getByRole("button", { name: /Retained label · running/ }).click();
    await page.waitForFunction(() => !document.querySelector(".host-pi-navigation").open);
    await openThreads();
    await page.getByRole("button", { name: "Restore thread", exact: true }).click();
    const hostFiles = page.locator(".host-pi-files");
    if (!await hostFiles.evaluate((node) => node.open))
      await hostFiles.locator(":scope > summary").click();
    await page.waitForFunction(() => document.querySelector(".host-pi-files").open);
    const projectFiles = hostFiles.locator(".project-files");
    await page.waitForFunction(() => document.querySelector(".host-pi-files .project-files").open);
    await projectFiles.getByLabel("Browse root", { exact: true }).waitFor();
    await page.getByRole("button", { name: "guide.txt", exact: true }).click();
    await page.getByText("<script>neverExecute()</script>\nProject guide", { exact: true }).waitFor();
    await page.getByRole("button", { name: "staged diff", exact: true }).click();
    await page.getByText("+ reviewed change", { exact: true }).waitFor();
    const selected = new URL(page.url()).searchParams.get("pi-thread"), opensBefore = opens.length;
    await page.getByLabel("Browse root").selectOption("context");
    await page.getByRole("button", { name: "guide.txt", exact: true }).waitFor();
    assert.equal(new URL(page.url()).searchParams.get("pi-thread"), selected);
    assert.equal(opens.length, opensBefore);
    // Same-origin page messages cannot impersonate the native iframe.
    await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", { origin: "https://pi.example.test", source: window, data: { v: 1, type: "session_changed", native_id: "forged", sequence: 999 } })));
    assert.equal(new URL(page.url()).searchParams.get("pi-thread"), selected);
    await page.screenshot({ path: path.join(output, "host-desktop.png"), fullPage: true });
    if (await threadPanel.evaluate((node) => node.open))
      await threadPanel.getByText("Project threads", { exact: true }).click();
    await page.waitForFunction(() => !document.querySelector(".host-pi-navigation").open);
    await page.setViewportSize({ width: 720, height: 900 });
    assert.ok((await page.locator("iframe").boundingBox()).y < 300);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(output, "host-tablet.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.waitForFunction(() => !document.querySelector(".host-pi-navigation").open);
    assert.ok((await page.locator("iframe").boundingBox()).y < 300);
    await page.getByText("Project threads", { exact: true }).focus();
    await page.keyboard.press("Enter");
    await page.getByLabel("Search threads").waitFor({ state: "visible" });
    await page.getByText("Project threads", { exact: true }).click();
    await page.waitForFunction(() => !document.querySelector(".host-pi-navigation").open);
    await page.screenshot({ path: path.join(output, "host-mobile.png"), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("https://workbench.example.test/?runs=1#/workbench/runs");
    await page.getByRole("button", { name: "Load more", exact: true }).click();
    const lastRun = page.getByRole("row").filter({ has: page.getByText("Native run 104", { exact: true }) });
    await lastRun.getByText("running", { exact: true }).waitFor();
    const activeRefreshStarted = Date.now(); nativeStatus = "retained";
    await lastRun.getByText("retained", { exact: true }).waitFor({ timeout: 5000 });
    const activeRefreshMs = Date.now() - activeRefreshStarted;
    assert.equal(await page.getByRole("row").count(), 106);
    nativeUnavailable = true;
    await lastRun.getByText("stale", { exact: true }).waitFor({ timeout: 10000 });
    assert.equal(await page.getByRole("row").count(), 106);
    nativeUnavailable = false; sourceEpoch++;
    await lastRun.waitFor({ state: "hidden", timeout: 10000 });
    assert.equal(await page.getByRole("row").count(), 101);
    nativeRows[104].native_state = "recoverable";
    nativeRows[104].status = "running";
    nativeStatus = "running";
    await page.goto("https://workbench.example.test/?runs=1&selected=native-run-104#/workbench/native-run-104/events");
    const selectedRun = page.getByLabel("Selected run", { exact: true });
    await selectedRun.locator(".focus-top .badge").getByText("recoverable", { exact: true }).waitFor();
    assert.equal(await selectedRun.locator(".focus-top .badge").innerText(), "recoverable");
    const nativeLink = page.getByRole("link", { name: "Open native Pi thread →", exact: true });
    await nativeLink.waitFor();
    assert.equal(await nativeLink.getAttribute("href"), "?project=product&pi-thread=native-104#/playground");
    await page.screenshot({ path: path.join(output, "native-run-desktop.png"), fullPage: true });
    assert.deepEqual(errors, []);
    const result = { fixture: true, native_creates: creates.size, requests, retained_draft: true, renamed_archived_restored: true, context_does_not_rebind: true, source_checked: true, native_runs: { count: 105, activeRefreshMs, reads: nativeReads, stale_retained: true, authority_change_clears_history: true, exact_link: true } };
    fs.writeFileSync(path.join(output, "receipts.json"), JSON.stringify(result, null, 2)); console.log(result);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
