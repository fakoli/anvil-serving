"use strict";
// Explicitly authorized live checks. Credentials are supplied only through stdin.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
(async () => {
  const input = JSON.parse(fs.readFileSync(0, "utf8"));
  assert(["configuration", "runtime"].includes(input.scenario));
  const base = new URL(input.url);
  assert(base.protocol === "https:" && !base.username && !base.search && !base.hash);
  fs.mkdirSync(input.output, { recursive: true, mode: 0o700 });
  const receipt = { scenario: input.scenario, passed: false, operations: [], errors: [] };
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.on("pageerror", () => receipt.errors.push("page error"));
  page.on("dialog", dialog => dialog.type() === "beforeunload" ? dialog.accept() : dialog.dismiss());
  const api = input.url + "api/observatory/v1/";
  const save = () => fs.writeFileSync(path.join(input.output, "results.json"), JSON.stringify(receipt, null, 2), { mode: 0o600 });
  async function read(route) {
    const response = await page.request.get(api + route);
    const data = await response.json();
    assert(response.ok() && data.ok, "authenticated read failed");
    return data.data;
  }
  async function applyReviewed(label) {
    const dialog = page.getByRole("dialog");
    const apply = dialog.getByRole("button", { name: "Apply change", exact: true });
    await apply.waitFor();
    await page.screenshot({ path: path.join(input.output, label + "-preview.png"), fullPage: true });
    const acknowledgement = dialog.getByRole("checkbox");
    if (await acknowledgement.count()) await acknowledgement.check();
    const response = page.waitForResponse(r => r.request().method() === "POST" && r.url().endsWith("/operations"));
    await apply.click();
    const envelope = await (await response).json();
    assert(envelope.ok && envelope.data.id, "confirmation refused");
    const operation = { label, id: envelope.data.id };
    receipt.operations.push(operation);
    save();
    // Reload retains the existing intent; this harness never resubmits it.
    await page.reload();
    for (let attempt = 0; attempt < 240; attempt++) {
      operation.result = await read("operations/" + operation.id);
      save();
      if (["succeeded", "failed", "manual_recovery_required"].includes(operation.result.status)) break;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    if (operation.result.evidence_id) operation.evidence = await read("evidence/" + operation.result.evidence_id);
    save();
    assert.equal(operation.result.status, "succeeded");
    assert.equal(operation.result.verification.status, "passed");
    await page.goto(input.url + "#/operations/" + operation.id);
    await page.screenshot({ path: path.join(input.output, label + "-result.png"), fullPage: true });
  }
  async function configure(expected, proposed, label) {
    await page.goto(input.url + "#/configuration/" + input.resource);
    const field = page.locator("#setting-" + input.setting);
    await field.waitFor();
    assert.equal(Number(await field.inputValue()), expected, "current value changed outside this reviewed journey");
    await field.fill(String(proposed));
    await page.getByRole("button", { name: "Validate", exact: true }).click();
    const previewResponse = page.waitForResponse(r => r.request().method() === "POST" && r.url().endsWith("/previews"));
    await page.getByRole("button", { name: "Review impact", exact: true }).click();
    const preview = (await (await previewResponse).json()).data;
    assert(preview.diff.some(row => row.field === input.setting && row.before === expected && row.after === proposed));
    await applyReviewed(label);
  }
  try {
    await page.goto(input.url);
    await page.getByLabel("Username", { exact: true }).fill(input.username);
    await page.getByLabel("Password", { exact: true }).fill(input.password);
    input.password = null;
    await page.locator("main").getByRole("button", { name: "Sign in", exact: true }).click();
    await page.getByRole("heading", { name: "Fleet overview", exact: true }).waitFor();
    const session = await read("session");
    assert.equal(session.operate, true);
    receipt.build = session.build;
    if (input.scenario === "configuration") {
      await configure(input.baseline, input.candidate, "candidate");
      await configure(input.candidate, input.baseline, "restore");
      const controls = await read("controls?resource=" + input.resource);
      assert.equal(controls.settings.find(s => s.setting_id === input.setting).configured, input.baseline);
    } else {
      await page.goto(input.url + "#/experiments/" + input.resource);
      await page.getByRole("button", { name: "Review runtime candidate", exact: true }).click();
      await applyReviewed("runtime-candidate");
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(input.output, "mobile-result.png"), fullPage: true });
    assert.equal(receipt.errors.length, 0);
    receipt.passed = true;
  } catch (error) {
    receipt.error = error instanceof assert.AssertionError ? error.message : "browser journey failed; inspect retained operation before retry";
    await page.screenshot({ path: path.join(input.output, "failure.png"), fullPage: true }).catch(() => {});
  } finally {
    save();
    await context.close();
    await browser.close();
    process.stdout.write(JSON.stringify({ passed: receipt.passed, output: input.output, operations: receipt.operations.map(o => o.id) }) + "\n");
    process.exitCode = receipt.passed ? 0 : 1;
  }
})();
