"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

(async () => {
  const output = path.resolve(process.argv[2] || "test-results/state-browser");
  fs.mkdirSync(output, { recursive: true });
  const fixture = spawn(process.env.OBSERVATORY_TEST_PYTHON || "python3", [path.join(__dirname, "state_fixture.py")], {
    stdio: ["pipe", "pipe", "inherit"], env: { ...process.env, PYTHONPATH: path.resolve(__dirname, "../../..") },
  });
  const lines = createInterface({ input: fixture.stdout });
  let browser;
  try {
    const started = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error("State fixture startup deadline exceeded")), 45000);
      fixture.once("exit", code => { clearTimeout(timer); reject(Error(`State fixture exited: ${code}`)); });
      lines.once("line", line => { clearTimeout(timer); resolve(JSON.parse(line)); });
    });
    browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || undefined, headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [], receipts = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("response", async response => {
      if (new URL(response.url()).pathname.endsWith(`/prds/${started.plan_id}`)) receipts.push(await response.json());
    });
    await page.goto(started.url);
    await page.getByLabel("Username", { exact: true }).fill("fixture");
    await page.getByLabel("Password", { exact: true }).fill("fixture-password");
    await page.locator("main").getByRole("button", { name: "Sign in", exact: true }).click();
    await page.getByRole("navigation", { name: "Main", exact: true }).getByRole("link", { name: "Anvil work", exact: true }).click();
    await page.getByRole("navigation", { name: "Project plans", exact: true }).getByRole("link", { name: new RegExp(started.plan_title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) }).click();
    await page.getByText(`Persisted revision ${started.prd_revision}`, { exact: false }).waitFor();
    const outline = page.getByRole("navigation", { name: "Plan outline", exact: true }).getByRole("link");
    assert.ok(await outline.count() > 0);
    await outline.first().click();
    const section = new URL(page.url()).searchParams.get("plan-section");
    await page.reload();
    await page.getByText(`Persisted revision ${started.prd_revision}`, { exact: false }).waitFor();
    assert.equal(new URL(page.url()).searchParams.get("plan"), started.plan_id);
    assert.equal(new URL(page.url()).searchParams.get("plan-section"), section);
    assert.ok(receipts.length >= 2);
    assert.ok(receipts.every(value => value.ok && value.data.source_digest === started.source_digest && value.data.prd_revision === started.prd_revision));
    assert.ok(await page.getByRole("navigation", { name: "Project tasks", exact: true }).getByRole("link").count() > 0);
    await page.screenshot({ path: path.join(output, "canonical-plan-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(output, "canonical-plan-mobile.png"), fullPage: true });
    assert.deepEqual(errors, []);
    const receipt = { canonical_disposable_state: true, plan_id: started.plan_id, revision: started.prd_revision,
      source_digest: started.source_digest, matching_owner_reads: receipts.length, outline_reload: true, mobile_width: 390 };
    fs.writeFileSync(path.join(output, "receipts.json"), JSON.stringify(receipt, null, 2)); console.log(receipt);
  } finally {
    if (browser) await browser.close();
    if (!fixture.killed) fixture.stdin.end('{"command":"stop"}\n');
    lines.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
