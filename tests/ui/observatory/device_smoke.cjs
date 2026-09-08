"use strict";
// An installed browser on a second authorized device; credentials arrive on stdin.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

(async () => {
  const input = JSON.parse(fs.readFileSync(0, "utf8"));
  assert(["read", "probe"].includes(input.scenario));
  const url = new URL(input.url);
  assert(url.protocol === "https:" && !url.username && !url.search && !url.hash);
  const output = path.resolve(input.output);
  fs.mkdirSync(output, { recursive: true, mode: 0o700 });
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, headless: true });
  const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", () => errors.push("page-error"));
  const receipt = { scenario: input.scenario, device: process.platform, browser: browser.version(), passed: false };
  try {
    await page.goto(input.url, { waitUntil: "domcontentloaded" });
    await page.getByLabel("Username", { exact: true }).fill(input.username);
    await page.getByLabel("Password", { exact: true }).fill(input.password);
    input.password = null;
    await page.locator("main").getByRole("button", { name: "Sign in", exact: true }).click();
    await page.getByRole("heading", { name: "Fleet overview", exact: true }).waitFor();
    const api = input.url + "api/observatory/v1/";
    const session = (await (await page.request.get(api + "session")).json()).data;
    receipt.build = session.build;
    const fleet = (await (await page.request.get(api + "fleet")).json()).data;
    receipt.counts = { hosts: fleet.hosts.length, serves: fleet.serves.length, gpus: fleet.hosts.reduce((sum, host) => sum + host.gpus.length, 0) };
    assert(receipt.counts.hosts > 0 && receipt.counts.serves > 0);
    await page.screenshot({ path: path.join(output, "overview.png"), fullPage: true });
    await page.goto(input.url + "#/serves/" + encodeURIComponent(input.resource));
    await page.getByRole("heading", { name: "Fleet overview", exact: true }).waitFor({ state: "hidden" });
    if (input.scenario === "probe") {
      const posted = [];
      page.on("request", request => {
        if (request.method() === "POST" && request.url().endsWith("/operations")) posted.push("confirmation");
      });
      await page.getByRole("button", { name: "Probe", exact: true }).click();
      const review = page.getByRole("dialog");
      await review.getByRole("button", { name: "Apply change", exact: true }).waitFor();
      const checkbox = review.getByRole("checkbox");
      if (await checkbox.count()) await checkbox.check();
      const response = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/operations"));
      await review.getByRole("button", { name: "Apply change", exact: true }).click();
      const submitted = (await (await response).json()).data;
      assert(submitted.id);
      receipt.operation_id = submitted.id;
      await page.reload();
      let operation;
      for (let attempt = 0; attempt < 90; attempt++) {
        operation = (await (await page.request.get(api + "operations/" + submitted.id)).json()).data;
        if (["succeeded", "failed", "manual_recovery_required"].includes(operation.status)) break;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      receipt.operation_status = operation.status;
      receipt.verification = operation.verification;
      receipt.confirmations = posted.length;
      assert.equal(posted.length, 1);
      assert.equal(operation.status, "succeeded");
      assert.equal(operation.verification.status, "passed");
      assert(operation.evidence_id);
      receipt.evidence_id = operation.evidence_id;
      await page.goto(input.url + "#/operations");
    }
    const chart = (await (await page.request.get(api + "metrics?chart=generation&serve=" + encodeURIComponent(input.resource))).json()).data;
    const grafana = chart.grafana_url;
    if (grafana) {
      assert(new URL(grafana).origin === url.origin);
      const response = await page.request.get(grafana);
      receipt.grafana_status = response.status();
      assert(response.status() < 400);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(output, "mobile.png"), fullPage: true });
    receipt.javascript_errors = errors.length;
    assert.equal(errors.length, 0);
    receipt.passed = true;
  } catch (error) {
    receipt.error = error instanceof assert.AssertionError ? "acceptance assertion failed" : "browser journey failed";
    await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true }).catch(() => {});
  } finally {
    await context.close();
    await browser.close();
    fs.writeFileSync(path.join(output, "results.json"), JSON.stringify(receipt, null, 2), { mode: 0o600 });
    process.stdout.write(JSON.stringify(receipt) + "\n");
    process.exitCode = receipt.passed ? 0 : 1;
  }
})();
