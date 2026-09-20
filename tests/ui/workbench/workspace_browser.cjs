"use strict";
// Real Console and browser, deterministic owners. Canonical State has a separate integration gate.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

(async () => {
  const output = path.resolve(process.argv[2] || "test-results/workspace-browser");
  fs.mkdirSync(output, { recursive: true });
  const fixture = spawn(process.env.OBSERVATORY_TEST_PYTHON || "python3", [path.join(__dirname, "real_fixture.py"), output], {
    stdio: ["pipe", "pipe", "inherit"], env: { ...process.env, PYTHONPATH: path.resolve(__dirname, "../../..") },
  });
  const lines = createInterface({ input: fixture.stdout });
  const first = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error("Fixture startup deadline exceeded")), 15000);
    lines.once("line", line => { clearTimeout(timer); resolve(JSON.parse(line)); });
  });
  let browser;
  try {
    const { url, username, password } = await first;
    browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || undefined, headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(url);
    await page.getByLabel("Username", { exact: true }).fill(username);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.locator("main").getByRole("button", { name: "Sign in", exact: true }).click();
    await page.getByRole("navigation", { name: "Main", exact: true }).getByRole("link", { name: "Anvil work", exact: true }).click();
    await page.getByRole("navigation", { name: "Project plans", exact: true }).getByRole("link").click();
    await page.getByText("Persisted revision 1", { exact: false }).waitFor();
    await page.getByRole("navigation", { name: "Plan outline", exact: true }).getByRole("link", { name: "Acceptance", exact: true }).click();
    await page.reload();
    await page.getByText("Persisted revision 1", { exact: false }).waitFor();
    assert.equal(new URL(page.url()).searchParams.get("plan-section"), "acceptance");
    await page.getByLabel("Search plans and tasks", { exact: true }).fill("T002");
    const tasks = page.getByRole("navigation", { name: "Project tasks", exact: true });
    assert.equal(await tasks.getByRole("link").count(), 1);
    await tasks.getByRole("link").click();
    await page.getByRole("button", { name: "Run with Pi", exact: true }).click();
    await page.getByRole("button", { name: "Start isolated run", exact: true }).waitFor();
    assert.equal(await page.getByRole("button", { name: "Start isolated run", exact: true }).isDisabled(), true);
    await page.getByLabel("Search plans and tasks", { exact: true }).fill("T001");
    await tasks.getByRole("link").click();
    await page.getByRole("button", { name: "Run with Pi", exact: true }).click();
    await page.getByRole("link", { name: "Open this session in Pi", exact: true }).click();
    await page.getByLabel("Pi task conversation", { exact: true }).waitFor();
    assert.equal(new URL(page.url()).searchParams.get("pi-session"), "fixture-pi-session");
    await page.reload();
    await page.getByRole("link", { name: "Open run in Workbench", exact: true }).waitFor();
    await page.getByRole("button", { name: "+ New conversation", exact: true }).click();
    let starts = 0, requestId;
    await page.route("**/api/workbench/v1/pi/sessions", route => {
      assert.equal(route.request().method(), "POST");
      starts++; requestId = route.request().postDataJSON().request_id;
      return route.abort("failed");
    });
    await page.getByRole("button", { name: "Start isolated run", exact: true }).click();
    await page.getByRole("button", { name: "Reconcile original start", exact: true }).waitFor();
    assert.equal(starts, 1);
    // Return to the task's new-run preview after reload; same stored start survives.
    await page.goto(`${url}#/work/research-fixture/workspace%3AT001/agent`);
    await page.getByRole("button", { name: "Reconcile original start", exact: true }).waitFor();
    let lookups = 0;
    await page.route("**/api/workbench/v1/pi/starts/*", async route => {
      assert.ok(new URL(route.request().url()).pathname.endsWith(requestId)); lookups++;
      const session = await page.evaluate(async () => (await (await fetch(location.pathname + "api/workbench/v1/pi/sessions/fixture-pi-session?project=research-fixture&task=workspace%3AT001")).json()).data);
      await route.fulfill({ json: { ok: true, data: { project_id: "research-fixture", task_id: "workspace:T001", status: "ready", sessions: [session] } } });
    });
    await page.getByRole("button", { name: "Reconcile original start", exact: true }).click();
    await page.getByRole("link", { name: "Open this session in Pi", exact: true }).waitFor();
    assert.equal(starts, 1); assert.equal(lookups, 1);
    let sends = 0;
    await page.route("**/api/workbench/v1/pi/sessions/*/command", async route => {
      sends++; await route.fetch(); await route.abort("failed");
    });
    await page.getByLabel("Message", { exact: true }).fill("One retained turn");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await page.getByRole("button", { name: "Reconcile command", exact: true }).waitFor();
    assert.equal(await page.evaluate(() => JSON.stringify(sessionStorage).includes("One retained turn")), false);
    await page.reload();
    await page.getByRole("button", { name: "Run with Pi", exact: true }).click();
    await page.getByRole("button", { name: "Reconcile command", exact: true }).waitFor();
    await page.getByRole("button", { name: "Reconcile command", exact: true }).click();
    await page.getByRole("button", { name: "Reconcile command", exact: true }).waitFor({ state: "hidden" });
    assert.equal(sends, 1);
    assert.equal(await page.getByLabel("Message", { exact: true }).inputValue(), "");
    assert.equal(await page.locator(".message.user").filter({ hasText: "One retained turn" }).count(), 1);
    await page.unroute("**/api/workbench/v1/pi/sessions");
    const bindingA = "fixture-pi-binding", bindingB = "fixture-pi-binding-b";
    await page.route("**/api/workbench/v1/pi/sessions?*", async route => {
      const request = route.request();
      const requested = new URL(request.url()).searchParams;
      assert.equal(request.method(), "GET");
      assert.equal(requested.get("project"), "research-fixture");
      assert.equal(requested.get("task"), "workspace:T001");
      await route.fulfill({ json: { ok: true, data: { items: [
        { binding_id: bindingB, project_id: "research-fixture", task_id: "workspace:T001" },
        { binding_id: bindingA, project_id: "research-fixture", task_id: "workspace:T001" },
      ] } } });
    });
    const artifactPath = `/api/workbench/v1/artifacts/${bindingA}`;
    const rootEvidence = {
      binding_id: bindingA, source: "isolated-sandbox", status: "submitted_release_pending",
      packet_digest: "b".repeat(64), rootset_digest: "c".repeat(64), acceptance: "independent_review_required",
      review: { manifest_digest: "d".repeat(64), roots: [{ root_id: "primary", baseline_sha: "a".repeat(40), artifact_digest: "e".repeat(64), files: [{ path: "safe.py", status: "M", mode: "100644" }] }] },
      verification: { manifest_digest: "d".repeat(64), passed: true, transferred: true },
      transfer: { primary: { state: "transferred" } },
      submission: { status: "submitted", serving_manifest_digest: "f".repeat(64) },
    };
    let releaseRetries = 0;
    await page.route(`**${artifactPath}**`, async route => {
      const request = route.request(), endpoint = new URL(request.url()).pathname;
      if (request.method() === "POST" && endpoint.endsWith(`${artifactPath}/submit`)) {
        releaseRetries++;
        assert.deepEqual(request.postDataJSON(), { artifact_digest: "d".repeat(64) });
        await route.fulfill({ json: { ok: true, data: { accepted: true } } });
      } else if (request.method() === "GET" && endpoint.endsWith(`${artifactPath}/roots`)) {
        await route.fulfill({ json: { ok: true, data: { binding_id: bindingA, source: "frozen-task-workspace", roots: [{ root_id: "primary", baseline_sha: "a".repeat(40), reviewed: true }] } } });
      } else if (request.method() === "GET" && endpoint.endsWith(`${artifactPath}/roots/primary/tree`)) {
        await route.fulfill({ json: { ok: true, data: { root_id: "primary", path: "", items: [{ name: "safe.py", kind: "file" }], truncated: false } } });
      } else if (request.method() === "GET" && endpoint.endsWith(`${artifactPath}/roots/primary/text`)) {
        assert.equal(new URL(request.url()).searchParams.get("path"), "safe.py");
        await route.fulfill({ json: { ok: true, data: { root_id: "primary", path: "safe.py", content: "safe = True\n" } } });
      } else if (request.method() === "GET" && endpoint.endsWith(`${artifactPath}/roots/primary/diff`)) {
        await route.fulfill({ json: { ok: true, data: { root_id: "primary", kind: "reviewed", diff: "-safe = False\n+safe = True\n" } } });
      } else if (request.method() === "GET" && endpoint.endsWith(`${artifactPath}/roots/primary/worktree`)) {
        await route.fulfill({ json: { ok: true, data: { root_id: "primary", source: "frozen-task-workspace", baseline_sha: "a".repeat(40), transfer_state: "transferred" } } });
      } else if (request.method() === "GET" && endpoint.endsWith(artifactPath)) {
        await route.fulfill({ json: { ok: true, data: rootEvidence } });
      } else {
        throw Error(`Unexpected frozen evidence request ${request.method()} ${endpoint}`);
      }
    });
    await page.getByRole("navigation", { name: "Main", exact: true }).getByRole("link", { name: "Workbench", exact: true }).click();
    const bindingRow = page.getByText(bindingA, { exact: true }).locator("xpath=ancestor::tr");
    await bindingRow.getByRole("link", { name: "Open run →", exact: true }).click();
    await page.getByRole("link", { name: "Open task evidence →", exact: true }).click();
    assert.equal(new URL(page.url()).searchParams.get("binding"), bindingA);
    assert.equal(new URL(page.url()).hash, "#/work/research-fixture/workspace%3AT001/evidence");
    await page.getByRole("button", { name: "Retry owner release reconciliation", exact: true }).waitFor();
    assert.equal(await page.getByRole("button", { name: "Retry owner release reconciliation", exact: true }).isDisabled(), false);
    await page.getByText("Frozen task files", { exact: true }).click();
    await page.getByLabel("Frozen root", { exact: true }).waitFor();
    await page.getByRole("button", { name: "safe.py", exact: true }).click();
    await page.getByText("safe = True", { exact: false }).waitFor();
    await page.getByRole("button", { name: "Reviewed patch", exact: true }).click();
    await page.getByText("+safe = True", { exact: false }).waitFor();
    await page.getByRole("button", { name: "Retry owner release reconciliation", exact: true }).click();
    await page.waitForTimeout(50);
    assert.equal(releaseRetries, 1);
    await page.screenshot({ path: path.join(output, "workspace-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(output, "workspace-mobile.png"), fullPage: true });
    assert.deepEqual(errors, []);
    const receipt = { fixture: true, search: true, plan_deep_link: true, blocked_task: true, exact_managed_session: true, lost_start: { starts, lookups }, lost_send: { sends }, frozen_roots: { tree: true, text: true, reviewed_diff: true, release_retries: releaseRetries }, mobile_width: 390 };
    fs.writeFileSync(path.join(output, "receipts.json"), JSON.stringify(receipt, null, 2));
    console.log(JSON.stringify(receipt));
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end('{"command":"stop"}\n');
    lines.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
