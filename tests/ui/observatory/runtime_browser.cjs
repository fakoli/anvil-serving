"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const { createFixture } = require("./fixture_server.cjs");
const output = path.resolve(
  process.argv[2] || "test-results/observatory-runtime",
);
fs.mkdirSync(output, { recursive: true });
(async () => {
  const fixture = await createFixture();
  fixture.state.authenticated = true;
  fixture.state.runtimeCandidate = true;
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
    headless: true,
    args: ["--disable-gpu"],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });
  const report = {
    fixture: true,
    observed_at: new Date().toISOString(),
    status: "running",
  };
  try {
    await page.goto(fixture.url + "#/experiments/runtime-a");
    const runtime = page.getByRole("region", {
      name: "Runtime candidate",
      exact: true,
    });
    const request = page.getByRole("region", {
      name: "Request-only experiment",
      exact: true,
    });
    await runtime.waitFor();
    assert.equal(
      await page.getByText(/^(?:null)+$/, { exact: true }).count(),
      0,
    );
    assert.equal(
      await request
        .getByLabel("Declared managed test", { exact: true })
        .inputValue(),
      "experiment-a",
    );
    assert.equal(
      await runtime
        .getByLabel("Runtime candidate test", { exact: true })
        .inputValue(),
      "runtime-a",
    );
    assert.equal(
      await page.locator("#experiment-max_output_tokens").inputValue(),
      "16",
    );
    await page.locator("#runtime-candidate-max_output_tokens").fill("129");
    await runtime
      .getByRole("button", { name: "Review runtime candidate", exact: true })
      .click();
    assert.equal(
      fixture.state.posts.filter((item) => item.route === "previews").length,
      0,
    );
    await page.locator("#runtime-candidate-max_output_tokens").fill("48");
    await runtime
      .getByRole("button", { name: "Review runtime candidate", exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByText("restore_exact_baseline", { exact: true })
      .waitFor();
    const preview = fixture.state.posts.at(-1).body;
    assert.deepEqual(preview, {
      resource_id: "runtime-a",
      action_id: "experiment.start",
      parameters: { max_output_tokens: 48 },
    });
    for (const text of [
      "baseline-v1",
      "candidate-v1",
      "llm.fixture",
      "baseline_probe",
      "candidate_probe",
      "verify_restore",
    ])
      await page.getByRole("dialog").getByText(text, { exact: true }).waitFor();
    assert.equal(fixture.state.operations.length, 0);
    await page.setViewportSize({ width: 320, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth + 1,
      ),
      false,
    );
    const apply = page
      .getByRole("dialog")
      .getByRole("button", { name: "Apply change", exact: true });
    await apply.scrollIntoViewIfNeeded();
    assert.ok(
      await apply.evaluate((node) => {
        const r = node.getBoundingClientRect();
        return (
          r.x >= 0 &&
          r.right <= innerWidth + 1 &&
          r.y >= 0 &&
          r.bottom <= innerHeight + 1
        );
      }),
    );
    await page.screenshot({
      path: path.join(output, "runtime-preview-320.png"),
      fullPage: false,
    });
    await apply.click();
    await page.waitForURL(/operations\/operation-1$/);
    assert.equal(fixture.state.operations.length, 1);
    const original = fixture.state.operations[0];
    original.status = "manual_recovery_required";
    original.verification = {
      status: "failed",
      message: "Baseline restoration was not verified.",
    };
    original.recovery = {
      status: "failed",
      message: "Review an exact retained restore.",
    };
    await page.reload();
    await page
      .getByRole("dialog")
      .getByText("Baseline restoration was not verified.", { exact: true })
      .waitFor();
    assert.equal(
      await page
        .getByRole("dialog")
        .getByRole("button", { name: "Review restore", exact: true })
        .count(),
      0,
    );
    fixture.state.recoveryPermitted = true;
    await page.reload();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Review restore", exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByText("Restore only; no probe replay", { exact: true })
      .waitFor();
    const restore = fixture.state.posts.at(-1).body;
    assert.deepEqual(restore, {
      resource_id: "runtime-a",
      action_id: "operation.recover",
      operation_id: original.id,
    });
    assert.equal(fixture.state.operations.length, 1);
    assert.equal(
      await page
        .getByRole("dialog")
        .getByText("baseline_probe", { exact: true })
        .count(),
      0,
    );
    await page.screenshot({
      path: path.join(output, "runtime-restore-preview-320.png"),
      fullPage: false,
    });
    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(fixture.url + "#/experiments/runtime-a");
    await runtime.waitFor();
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth + 1,
      ),
      false,
    );
    await page.screenshot({
      path: path.join(output, "runtime-form-390.png"),
      fullPage: true,
    });
    await runtime.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: path.join(output, "runtime-panel-390.png"),
      fullPage: false,
    });
    fs.writeFileSync(
      path.join(output, "runtime-form-390.aria.yml"),
      await page.locator("main").ariaSnapshot(),
    );
    Object.assign(report, {
      status: "passed",
      separate_typed_panels: true,
      candidate_bound_enforced: true,
      candidate_preview: preview,
      preview_owner_mutations: 0,
      explicit_candidate_apply_operations: 1,
      mobile_confirmation_visible: true,
      recovery_requires_fresh_catalog_permission: true,
      restore_preview: restore,
      restore_preview_owner_mutations: 0,
      no_browser_run_id_or_private_bindings: true,
    });
    process.stdout.write(
      "PASS typed runtime candidate: separate forms, bounds, exact preview/apply,320px confirmation, original-intent restore with fresh controls\n",
    );
  } catch (error) {
    report.status = "failed";
    report.error = error.stack;
    await page.screenshot({
      path: path.join(output, "failure.png"),
      fullPage: true,
    });
    throw error;
  } finally {
    fs.writeFileSync(
      path.join(output, "results.json"),
      JSON.stringify(report, null, 2),
    );
    await browser.close();
    await fixture.close();
  }
})().catch((error) => {
  process.stderr.write(error.stack + "\n");
  process.exitCode = 1;
});
