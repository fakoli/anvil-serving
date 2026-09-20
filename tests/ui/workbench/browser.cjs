"use strict";
// Real Console/session/CSRF/journal + local deterministic owner/model fixtures.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");
const { spawn } = require("node:child_process");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const output = path.resolve(
  process.argv[2] || "test-results/workbench-browser",
);
fs.mkdirSync(output, { recursive: true });

(async () => {
  const fixture = spawn(
    process.env.OBSERVATORY_TEST_PYTHON || "python3",
    [path.join(__dirname, "real_fixture.py"), output],
    {
      stdio: ["pipe", "pipe", "pipe"],
      // The selected Python may be editable-installed from another worktree.
      // Serve the source beside this fixture, including its packaged assets.
      env: { ...process.env, PYTHONPATH: path.resolve(__dirname, "../../..") },
    },
  );
  const lines = readline.createInterface({ input: fixture.stdout });
  const pending = [],
    buffered = [];
  let stderr = "";
  fixture.stderr.on("data", (chunk) => {
    stderr += chunk;
  });
  lines.on("line", (line) => {
    const row = JSON.parse(line);
    pending.length ? pending.shift()(row) : buffered.push(row);
  });
  const next = () =>
    buffered.length
      ? Promise.resolve(buffered.shift())
      : new Promise((resolve, reject) => {
          const timer = setTimeout(
            () => reject(new Error(`Fixture timeout: ${stderr.slice(-2000)}`)),
            10000,
          );
          pending.push((row) => {
            clearTimeout(timer);
            resolve(row);
          });
        });
  const status = () => {
    fixture.stdin.write('{"command":"status"}\n');
    return next();
  };
  const control = (command) => {
    fixture.stdin.write(JSON.stringify({ command }) + "\n");
    return next();
  };
  const { url } = await next();
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
    headless: true,
    args: ["--disable-gpu"],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });
  const errors = [];
  const operationReads = [];
  const runRequests = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/observatory/v1/runs/")) runRequests.push(request.url());
  });
  page.on("response", async (response) => {
    if (response.url().includes("/api/observatory/v1/operations/")) {
      operationReads.push({ status: response.status(), body: await response.json().catch(() => null) });
    }
  });
  page.on("pageerror", (error) => errors.push(error.message));
  const receipts = { fixture: true, journeys: [] };
  const capture = async (name) => {
    await page.screenshot({
      path: path.join(output, `${name}.png`),
      fullPage: true,
    });
  };
  const nav = async (name) =>
    page
      .getByRole("navigation", { name: "Main", exact: true })
      .getByRole("link", { name, exact: true })
      .click();
  const choose = async (name, value) =>
    page.getByRole("combobox", { name, exact: true }).selectOption(value);
  try {
    await page.goto(url);
    await page.getByLabel("Username", { exact: true }).waitFor();
    assert.equal(await page.locator("#fixture-label").isVisible(), true);
    await page.getByLabel("Username", { exact: true }).fill("operator");
    await page.getByLabel("Password", { exact: true }).fill("fixture-password");
    await page
      .locator("main")
      .getByRole("button", { name: "Sign in", exact: true })
      .click();
    await page
      .getByRole("heading", {
        name: "A place for the whole experiment.",
        exact: true,
      })
      .waitFor();
    await page.getByRole("tab", { name: "Run flow", exact: true }).click();
    await page
      .getByRole("button", { name: "Review experiment", exact: true })
      .waitFor();
    assert.equal(new URL(page.url()).hash, "#/workbench/run");
    assert.equal((await status()).owner_mutations, 0);
    await page
      .getByRole("spinbutton", { name: "Output limit (tokens)", exact: true })
      .fill("32");
    await page
      .getByRole("button", { name: "Review experiment", exact: true })
      .click();
    await page
      .getByRole("dialog", { name: "Review impact", exact: true })
      .waitFor();
    assert.equal((await status()).owner_mutations, 0);
    await page
      .getByRole("button", { name: "Apply change", exact: true })
      .click();
    await page.waitForURL(/#\/operations\//);
    await page.getByRole("dialog").waitFor();
    // Native close events are queued: an old close must not stop this view's
    // in-flight read or polling after the shared dialog opens again.
    await page.getByRole("dialog").evaluate((dialog) => dialog.dispatchEvent(new Event("close")));
    await page
      .getByRole("dialog")
      .getByText("Independent deterministic fixture check passed.", {
        exact: true,
      })
      .waitFor();
    const operationUrl = page.url();
    await page.reload();
    await page
      .getByRole("dialog")
      .getByText("Independent deterministic fixture check passed.", {
        exact: true,
      })
      .waitFor();
    assert.equal(page.url(), operationUrl);
    assert.equal((await status()).owner_mutations, 1);
    await page
      .getByRole("button", { name: "View retained evidence", exact: true })
      .click();
    await page.getByText("Evidence metadata", { exact: true }).click();
    await page
      .getByRole("dialog")
      .getByText(/Deterministic browser fixture/)
      .waitFor();
    await page.getByRole("button", { name: "Close", exact: true }).click();
    receipts.journeys.push(
      "empty Run flow, exact preview, one verified operation, reload without replay, retained evidence",
    );

    await nav("Settings");
    await choose("Default workspace", "compute");
    await choose("Density", "compact");
    await choose("Time zone", "America/Los_Angeles");
    await choose("Default range", "6h");
    await page
      .getByRole("button", { name: "Save preferences", exact: true })
      .click();
    await page
      .locator(".settings-feedback")
      .getByText("Workspace preferences saved.", { exact: true })
      .waitFor();
    await page.goto(url);
    await page.getByRole("heading", { name: "Compute", exact: true }).waitFor();
    assert.deepEqual(
      await page.locator("body").evaluate((body) => ({
        compact: body.classList.contains("density-compact"),
        zone: body.dataset.timeZone,
        range: body.dataset.defaultRange,
      })),
      { compact: true, zone: "America/Los_Angeles", range: "6h" },
    );
    await nav("Settings");
    await page
      .getByRole("link", { name: "Pi environment", exact: true })
      .click();
    await choose("Provider", "fixture-provider-b");
    assert.deepEqual(
      await page
        .getByRole("combobox", { name: "Model", exact: true })
        .locator("option")
        .allTextContents(),
      ["orion-fixture"],
    );
    await page
      .getByRole("button", { name: "Save Pi defaults", exact: true })
      .click();
    await page
      .locator(".settings-feedback")
      .getByText("Pi defaults saved.", { exact: true })
      .waitFor();
    await page.reload();
    assert.equal(
      await page
        .getByRole("combobox", { name: "Model", exact: true })
        .inputValue(),
      "orion-fixture",
    );
    await page.getByRole("link", { name: "General", exact: true }).click();
    await capture("settings-desktop");
    receipts.journeys.push(
      "server defaults apply and survive reload; provider selection changes and retains model options",
    );

    await nav("Workbench");
    await page
      .getByRole("tabpanel", { name: "Overview", exact: true })
      .waitFor();
    await page.getByText("context-001", { exact: true }).waitFor();
    await page.getByText("fixture-active-benchmark", { exact: true }).first().waitFor();
    await page.getByText("Deterministic response check", { exact: true }).first().waitFor();
    await page.getByRole("tab", { name: "All runs", exact: true }).click();
    const sourceFilter = page.getByRole("combobox", { name: "Run source", exact: true });
    await sourceFilter.focus();
    await page.waitForResponse(
      (response) => response.url().includes("/api/observatory/v1/runs/benchmark") && response.status() === 200,
      { timeout: 4000 },
    );
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute("aria-label")), "Run source");
    const contextRunLink = page
      .getByText("context-001", { exact: true })
      .locator("xpath=ancestor::tr")
      .getByRole("link", { name: "Open run →", exact: true });
    await contextRunLink.focus();
    const contextHref = await contextRunLink.getAttribute("href");
    await page.waitForResponse(
      (response) => response.url().includes("/api/observatory/v1/runs/benchmark") && response.status() === 200,
      { timeout: 4000 },
    );
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute("href")), contextHref);
    await contextRunLink.click();
    await page.getByRole("tabpanel", { name: "Events", exact: true }).waitFor();
    await page.getByText("This source exposes native IDs and artifact references only; it has no declared run-detail route.", { exact: true }).waitFor();
    const selectedRunUrl = page.url();
    await page.setViewportSize({ width: 1440, height: 400 });
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    const selectedScroll = await page.evaluate(() => window.scrollY);
    assert.ok(selectedScroll > 0);
    const delayedAt = Date.now();
    await control("benchmark-hang");
    const healthyObserved = page.waitForResponse(
      (response) => response.url().includes("/api/observatory/v1/runs/operations") && response.status() === 200,
      { timeout: 10000 },
    ).then(() => Date.now());
    const staleObserved = page.getByLabel("Run source coverage").getByText("stale", { exact: true }).waitFor({ timeout: 5000 }).then(() => Date.now());
    const [healthyAt, staleAt] = await Promise.all([healthyObserved, staleObserved]);
    const pollObservation = { stale_ms: staleAt - delayedAt, healthy_ms: healthyAt - delayedAt };
    assert.ok(pollObservation.stale_ms <= 5000);
    assert.ok(pollObservation.healthy_ms <= 10000);
    receipts.run_polling = pollObservation;
    assert.equal(page.url(), selectedRunUrl);
    assert.equal(await page.evaluate(() => window.scrollY), selectedScroll);
    const readsBeforeHide = runRequests.length;
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await page.waitForTimeout(300);
    assert.equal(runRequests.length, readsBeforeHide);
    const inFlightBenchmark = page.waitForRequest(
      (request) => request.url().includes("/api/observatory/v1/runs/benchmark"),
      { timeout: 3000 },
    );
    const resumedOperations = page.waitForResponse(
      (response) => response.url().includes("/api/observatory/v1/runs/operations") && response.status() === 200,
      { timeout: 3000 },
    );
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await Promise.all([inFlightBenchmark, resumedOperations]);
    assert.equal(page.url(), selectedRunUrl);
    await control("revoke-benchmark");
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      document.dispatchEvent(new Event("visibilitychange"));
      Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await page.getByText("The selected retained run is unavailable from the current authorized sources.", { exact: true }).waitFor({ timeout: 3000 });
    assert.equal(await page.getByText("context-001", { exact: true }).count(), 0);
    await page.waitForTimeout(6500);
    assert.equal(await page.getByText("context-001", { exact: true }).count(), 0);
    await control("restore-benchmark");
    const legacyOperationId = new URL(operationUrl).hash.split("/").at(-1);
    await page.goto(`${url}#/workbench/${encodeURIComponent(legacyOperationId)}/events`);
    await page.getByRole("tabpanel", { name: "Events", exact: true }).waitFor();
    await page.getByRole("button", { name: "Open operation detail", exact: true }).waitFor();
    assert.equal(new URL(page.url()).hash, `#/workbench/${encodeURIComponent(legacyOperationId)}/events`);
    await page.setViewportSize({ width: 1440, height: 900 });
    receipts.journeys.push(
      "CLI-created benchmark and UI-created operation appear in independently polled sources; a delayed benchmark turns stale within 5s while Operations refreshes within 10s, filter focus/selection/scroll persist, hide/resume pauses then refreshes, and an in-flight revoked source cannot restore stale rows",
    );
    await capture("workbench-desktop");
    await page.getByRole("tab", { name: "Overview", exact: true }).focus();
    await page.keyboard.press("ArrowRight");
    assert.equal(
      await page.evaluate(() => document.activeElement.textContent),
      "Run flow",
    );
    await page.keyboard.press("Enter");
    await page
      .getByRole("button", { name: "Review experiment", exact: true })
      .waitFor();
    await nav("Anvil work");
    await page.getByRole("button", { name: "Read plan", exact: true }).click();
    await page.getByText("Persisted revision 1", { exact: false }).waitFor();
    await page.getByRole("navigation", { name: "Plan outline", exact: true }).getByRole("link", { name: "Acceptance", exact: true }).click();
    await page.getByText("Persisted revision 1", { exact: false }).waitFor();
    assert.equal(new URL(page.url()).searchParams.get("plan"), "workspace");
    assert.equal(new URL(page.url()).searchParams.get("plan-section"), "acceptance");
    await page.reload();
    await page.getByText("Persisted revision 1", { exact: false }).waitFor();
    receipts.journeys.push("persisted plan outline and deep link survive reload");
    await nav("Workbench");
    await page
      .getByRole("tabpanel", { name: "Overview", exact: true })
      .waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await capture("workbench-mobile");
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    assert.equal(
      await page.locator("#navigation").evaluate((node) => node.inert),
      true,
    );
    await page
      .getByRole("button", { name: "Open navigation", exact: true })
      .click();
    assert.equal(
      await page.locator(".workspace").evaluate((node) => node.inert),
      true,
    );
    await page.keyboard.press("Escape");
    assert.equal(
      await page
        .getByRole("button", { name: "Open navigation", exact: true })
        .getAttribute("aria-expanded"),
      "false",
    );
    await page
      .getByRole("button", { name: "Open navigation", exact: true })
      .click();
    await nav("Settings");
    await page
      .getByRole("button", { name: "Save preferences", exact: true })
      .waitFor();
    await capture("settings-mobile");
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    receipts.journeys.push(
      "1440 and 390 viewport reflow, keyboard tabs, inert mobile drawer and Escape return",
    );
    assert.deepEqual(errors, []);
    receipts.status = await status();
    fs.writeFileSync(
      path.join(output, "receipts.json"),
      JSON.stringify(receipts, null, 2),
    );
    console.log(JSON.stringify(receipts));
  } catch (error) {
    await capture("failure");
    fs.writeFileSync(path.join(output, "failure.json"), JSON.stringify({
      error: error.message, url: page.url(), errors,
      text: await page.locator("body").innerText(), status: await status(),
      operationReads,
      latestOperation: await page.evaluate(async () => {
        const id = location.hash.match(/^#\/operations\/(.+)$/)?.[1];
        return id ? (await fetch(location.pathname + "api/observatory/v1/operations/" + id)).json() : null;
      }),
    }, null, 2));
    throw error;
  } finally {
    await browser.close();
    fixture.stdin.end('{"command":"stop"}\n');
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
