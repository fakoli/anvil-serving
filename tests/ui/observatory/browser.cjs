"use strict";
// Run with a dev-only Playwright module and Chromium executable supplied by the caller.
// This fixture harness cannot contact or mutate a real resource owner.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createFixture } = require("./fixture_server.cjs");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const output = path.resolve(process.argv[2] || "test-results/observatory");
fs.mkdirSync(output, { recursive: true });
const results = [];
(async () => {
  const fixture = await createFixture();
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
    headless: true,
    args: ["--disable-gpu"],
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (
      message.type() === "error" &&
      !message.text().includes("503") &&
      !message.text().includes("net::ERR_EMPTY_RESPONSE")
    )
      errors.push(message.text());
  });
  const test = async (name, run) => {
    const before = Date.now();
    await run();
    results.push({ name, status: "passed", duration_ms: Date.now() - before });
    process.stdout.write(`PASS ${name}\n`);
  };
  const navigate = async (hash, title) => {
    await page.goto(fixture.url + "#/" + hash);
    await page
      .getByRole("heading", { name: title, exact: true })
      .first()
      .waitFor();
    await page
      .locator("main .loading")
      .waitFor({ state: "hidden" })
      .catch(() => {});
  };
  const screenshot = async (name) => {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: path.join(output, name + ".png"),
      fullPage: true,
    });
  };
  try {
    await test("Session login and no credential storage", async () => {
      await page.goto(fixture.url);
      await page.getByLabel("Username", { exact: true }).fill("fixture-user");
      await page
        .getByLabel("Password", { exact: true })
        .fill("fixture-password");
      await page
        .locator("main")
        .getByRole("button", { name: "Sign in", exact: true })
        .click();
      await page.getByRole("heading", { name: "Fleet overview" }).waitFor();
      assert.equal(
        await page.getByText("Isolated fixture", { exact: true }).count(),
        1,
      );
      assert.deepEqual(
        await page.evaluate(() => ({
          local: Object.keys(localStorage),
          session: Object.keys(sessionStorage),
        })),
        { local: [], session: [] },
      );
      assert.ok(fixture.state.posts.some((p) => p.route === "session"));
    });
    await test("Base path, null chart gaps, unsupported source and table alternatives", async () => {
      await page
        .getByRole("heading", { name: "Generation throughput", exact: true })
        .waitFor();
      await page
        .getByText("This engine does not expose TTFT samples.", { exact: true })
        .waitFor();
      const chart = page.getByRole("article", { name: "generation chart" });
      const pathValue = await chart
        .locator('path[data-series="0"]')
        .getAttribute("d");
      assert.equal((pathValue.match(/M/g) || []).length, 2);
      await chart.getByText(/View data table/).click();
      assert.ok(
        await chart.getByText("Gap — no sample", { exact: true }).count(),
      );
      await chart.getByText(/View data table/).click();
      assert.ok(
        fixture.state.reads.some(
          (r) => r.route === "metrics" && r.query.chart === "generation",
        ),
      );
      assert.equal(await page.locator('script[src^="/"]').count(), 0);
    });
    await test("Late response from a previous host cannot replace the current scope", async () => {
      const scopedPage = await context.newPage();
      fixture.state.slowMetrics = 1200;
      fixture.state.slowHost = "host-fixture-a";
      try {
        await scopedPage.goto(fixture.url + "#/overview");
        await scopedPage
          .getByRole("heading", { name: "Fleet overview", exact: true })
          .waitFor();
        const previousStarted = scopedPage.waitForRequest((request) => {
          const address = new URL(request.url());
          return (
            address.pathname.endsWith("/metrics") &&
            address.searchParams.get("host") === "host-fixture-a"
          );
        });
        await scopedPage
          .getByLabel("Workstation", { exact: true })
          .selectOption("host-fixture-a");
        await previousStarted;
        await scopedPage
          .getByLabel("Workstation", { exact: true })
          .selectOption("host-fixture-b");
        const legend = scopedPage
          .getByRole("article", { name: "generation chart" })
          .locator(".chart-legend");
        await legend.getByText("1. host-fixture-b", { exact: true }).waitFor();
        await scopedPage.waitForTimeout(1500);
        assert.equal(
          await scopedPage
            .getByLabel("Workstation", { exact: true })
            .inputValue(),
          "host-fixture-b",
        );
        assert.equal(
          await legend.getByText("1. host-fixture-b", { exact: true }).count(),
          1,
        );
        assert.equal(
          await legend.getByText("1. host-fixture-a", { exact: true }).count(),
          0,
        );
      } finally {
        fixture.state.slowMetrics = 0;
        fixture.state.slowHost = null;
        await scopedPage.close();
      }
    });
    await test("Synthetic visibility events pause scheduled reads and resume current evidence", async () => {
      const visibilityPage = await context.newPage();
      let requests = 0;
      visibilityPage.on("request", (request) => {
        if (new URL(request.url()).pathname.includes("/api/observatory/v1/"))
          requests += 1;
      });
      try {
        await visibilityPage.goto(fixture.url + "#/overview");
        await visibilityPage
          .getByRole("heading", { name: "Fleet overview", exact: true })
          .waitFor();
        await visibilityPage
          .locator("main .loading")
          .first()
          .waitFor({ state: "hidden" });
        // Explicitly simulated document state: this tests the handler wiring,
        // and does not claim native browser background-tab behavior.
        await visibilityPage.evaluate(() => {
          Object.defineProperty(document, "hidden", {
            configurable: true,
            value: true,
          });
          document.dispatchEvent(new Event("visibilitychange"));
        });
        requests = 0;
        await visibilityPage.waitForTimeout(6000);
        assert.equal(requests, 0);
        const resumed = visibilityPage.waitForRequest((request) =>
          new URL(request.url()).pathname.endsWith("/fleet"),
        );
        await visibilityPage.evaluate(() => {
          delete document.hidden;
          document.dispatchEvent(new Event("visibilitychange"));
        });
        await resumed;
        assert.ok(requests > 0);
      } finally {
        await visibilityPage.close();
      }
    });
    await test("Physical GPU and separate source identity", async () => {
      await navigate("workstations/host-fixture-a", "Atlas workstation");
      await page.getByText("GPU-fixture-compute-a", { exact: true }).waitFor();
      await page.getByText("GPU-fixture-compute-b", { exact: true }).waitFor();
      await page
        .getByRole("heading", { name: "Declared operating profiles" })
        .waitFor();
    });
    await test("Configured, observed, proposed and draft survival", async () => {
      await navigate("configuration/serve-a", "Configuration");
      assert.equal(
        await page
          .locator('[data-configured="max_output_tokens"]')
          .textContent(),
        "32",
      );
      assert.equal(
        await page.locator('[data-observed="max_output_tokens"]').textContent(),
        "24",
      );
      const input = page.locator("#setting-max_output_tokens");
      await input.fill("48");
      assert.equal(
        await page
          .locator('[data-configured="max_output_tokens"]')
          .textContent(),
        "32",
      );
      assert.equal(
        await page.locator('[data-observed="max_output_tokens"]').textContent(),
        "24",
      );
      assert.equal(
        fixture.state.posts.filter((p) => p.route === "drafts").length,
        0,
      );
      await page
        .getByRole("navigation", { name: "Main", exact: true })
        .getByRole("link", { name: "Workstations", exact: true })
        .click();
      await page
        .getByRole("heading", { name: "Workstations", exact: true })
        .waitFor();
      await page
        .getByRole("navigation", { name: "Main", exact: true })
        .getByRole("link", { name: "Configuration", exact: true })
        .click();
      await input.waitFor();
      assert.equal(await input.inputValue(), "48");
      await page.getByRole("button", { name: "Validate", exact: true }).click();
      await page.getByText(/Validated version 1/).waitFor();
      assert.equal(
        fixture.state.posts.filter((p) => p.route === "operations").length,
        0,
      );
      assert.equal(
        fixture.state.posts.find((p) => p.route === "drafts").csrf,
        "synthetic-csrf",
      );
    });
    await test("Preview exact impact, focus trap and one apply", async () => {
      await page
        .getByRole("button", { name: "Review impact", exact: true })
        .click();
      const dialog = page.getByRole("dialog");
      await dialog.getByText("candidate-v1", { exact: true }).waitFor();
      await dialog.getByText("llm.primary", { exact: true }).waitFor();
      assert.equal(
        await page.evaluate(() => document.activeElement.id),
        "dialog-title",
      );
      await page.keyboard.press("Shift+Tab");
      assert.ok(
        await page.evaluate(() => !!document.activeElement.closest("dialog")),
      );
      await page.keyboard.press("Escape");
      await dialog.waitFor({ state: "hidden" });
      assert.equal(
        await page
          .getByRole("button", { name: "Review impact" })
          .evaluate((el) => el === document.activeElement),
        true,
      );
      await page.getByRole("button", { name: "Review impact" }).click();
      await page
        .getByRole("dialog")
        .getByRole("button", { name: "Apply change", exact: true })
        .click();
      await page.waitForURL(/#\/operations\/operation-1$/);
      await page
        .getByRole("dialog")
        .getByText("owner-001", { exact: true })
        .waitFor();
      assert.equal(
        fixture.state.posts.filter((p) => p.route === "operations").length,
        1,
      );
    });
    await test("Refresh resumes durable operation without replay; verification separate", async () => {
      await page.reload();
      await page
        .getByRole("dialog")
        .getByText("owner-001", { exact: true })
        .waitFor();
      assert.equal(
        fixture.state.posts.filter((p) => p.route === "operations").length,
        1,
      );
      const op = fixture.state.operations[0];
      Object.assign(op, {
        status: "failed",
        native_state: "completed",
        execution_outcome: "succeeded",
        verification: {
          status: "failed",
          message: "Observed identity did not match the candidate.",
        },
      });
      await page
        .getByRole("dialog")
        .getByText("Observed identity did not match the candidate.", {
          exact: true,
        })
        .waitFor({ timeout: 8000 });
      assert.equal(
        await page
          .getByRole("dialog")
          .getByText("succeeded", { exact: true })
          .count(),
        1,
      );
      await page
        .getByRole("heading", { name: "Verification failed", exact: true })
        .waitFor();
      await screenshot("operation-verification-failed");
      op.status = "succeeded";
      await page.reload();
      await page
        .getByRole("heading", { name: "Verification failed", exact: true })
        .waitFor();
      assert.equal(
        await page.getByRole("dialog").locator(".badge.success").count(),
        0,
      );
      op.status = "failed";
      await page.keyboard.press("Escape");
    });
    await test("Editing invalidates preview and numerical bounds prevent request", async () => {
      await navigate("configuration/serve-a", "Configuration");
      await page.locator("#setting-max_output_tokens").fill("160");
      const before = fixture.state.posts.length;
      await page.getByRole("button", { name: "Validate", exact: true }).click();
      await page
        .getByRole("heading", { name: "Resolve these fields before reviewing" })
        .waitFor();
      assert.equal(fixture.state.posts.length, before);
      await page.locator("#setting-max_output_tokens").fill("40");
      await page.getByRole("button", { name: "Validate", exact: true }).click();
      await page.getByText(/Validated version/).waitFor();
      assert.equal(
        await page.getByRole("button", { name: "Review impact" }).isDisabled(),
        false,
      );
      await page.locator("#setting-max_output_tokens").fill("41");
      assert.equal(
        await page.getByRole("button", { name: "Review impact" }).isDisabled(),
        true,
      );
    });
    await test("Lost transport locks submission and exposes durable history", async () => {
      fixture.state.lost = true;
      await page.getByRole("button", { name: "Validate", exact: true }).click();
      await page.getByText(/Validated version/).waitFor();
      await page.getByRole("button", { name: "Review impact" }).click();
      await page
        .getByRole("dialog")
        .getByRole("button", { name: "Apply change" })
        .click();
      await page
        .getByText(/Outcome unknown; reconciling with owner\. Open Operations/)
        .waitFor();
      assert.equal(
        await page
          .getByRole("button", { name: "Submission locked" })
          .isDisabled(),
        true,
      );
      assert.equal(fixture.state.operations.length, 2);
      await screenshot("operation-lost-transport");
      await page.getByRole("link", { name: "Check Operations" }).click();
      await page
        .getByRole("heading", { name: "Operations", exact: true })
        .waitFor();
      assert.equal(fixture.state.operations.length, 2);
      fixture.state.lost = false;
    });
    await test("Canonical workload quality, omissions, unknown progress and malformed evidence", async () => {
      fixture.state.partialWorkloads = true;
      await navigate("workloads", "Workloads");
      await page
        .getByText(/unknown omission count/)
        .first()
        .waitFor();
      await page.getByText("Not reported · running", { exact: true }).waitFor();
      await page.getByLabel("Activity", { exact: true }).selectOption("true");
      await page.getByText(/Active-only excludes stale observations/).waitFor();
      assert.ok(
        fixture.state.reads.some(
          (r) => r.route === "workloads" && r.query.active_only === "true",
        ),
      );
      fixture.state.malformedWorkloads = true;
      await page.getByRole("button", { name: "Refresh", exact: true }).click();
      await page.getByText(/Invalid canonical workload evidence/).waitFor();
      fixture.state.malformedWorkloads = false;
    });
    await test("Managed experiment catalog and exact evidence comparison", async () => {
      await navigate("experiments/experiment-a", "Experiments");
      await page
        .getByLabel("Maximum output tokens (tokens)", { exact: true })
        .fill("20");
      assert.equal(
        await page
          .getByRole("button", { name: "Review experiment" })
          .isDisabled(),
        false,
      );
      await page.getByText(/Not directly comparable\. At least one/).waitFor();
      await page.getByRole("button", { name: "Review experiment" }).click();
      await page
        .getByRole("dialog")
        .getByText("experiment-a", { exact: true })
        .waitFor();
      assert.equal(
        fixture.state.posts.at(-1).body.parameters.max_output_tokens,
        20,
      );
      await page.keyboard.press("Escape");
    });
    await test("Declared serve uses only the exact supported fixed probe action", async () => {
      fixture.state.serveProbeOnly = true;
      try {
        await navigate("experiments/serve-a", "Experiments");
        await page.getByLabel("Declared serve", { exact: true }).waitFor();
        assert.equal(await page.locator('[id^="experiment-"]').count(), 0);
        await page
          .getByRole("button", { name: "Review experiment", exact: true })
          .click();
        await page
          .getByRole("dialog")
          .getByText("serve-a", { exact: true })
          .waitFor();
        const request = fixture.state.posts.at(-1);
        assert.equal(request.route, "previews");
        assert.equal(request.body.resource_id, "serve-a");
        assert.equal(request.body.action_id, "serve.probe");
        assert.deepEqual(request.body.parameters, {});
        await page.keyboard.press("Escape");
      } finally {
        fixture.state.serveProbeOnly = false;
      }
    });
    await test("Independent chart failure preserves current owner state", async () => {
      fixture.state.metricFailure = true;
      await navigate("overview", "Fleet overview");
      await page
        .getByText("Historical source unavailable.", { exact: true })
        .first()
        .waitFor();
      await page
        .getByRole("link", { name: "Atlas workstation", exact: true })
        .waitFor();
      fixture.state.metricFailure = false;
    });
    await test("Viewer cannot edit or apply and preferences contain no secrets", async () => {
      fixture.state.operate = false;
      await page.reload();
      await navigate("configuration/serve-a", "Configuration");
      assert.equal(
        await page.locator("#setting-max_output_tokens").isDisabled(),
        true,
      );
      assert.equal(
        await page
          .getByRole("button", { name: "Validate", exact: true })
          .isDisabled(),
        true,
      );
      await navigate("settings", "Settings");
      await page.getByLabel("Time zone", { exact: true }).selectOption("UTC");
      const store = await page.evaluate(() =>
        JSON.parse(localStorage.getItem("anvil-observatory-display")),
      );
      assert.deepEqual(Object.keys(store).sort(), [
        "density",
        "landing",
        "range",
        "zone",
      ]);
      fixture.state.operate = true;
      await page.reload();
    });
    await test("All eight screens at desktop and mobile without horizontal overflow", async () => {
      for (const width of [1440, 390]) {
        await page.setViewportSize({
          width,
          height: width === 390 ? 844 : 900,
        });
        for (const [route, title] of [
          ["overview", "Fleet overview"],
          ["workstations", "Workstations"],
          ["serves", "Serves"],
          ["workloads", "Workloads"],
          ["configuration/serve-a", "Configuration"],
          ["experiments/experiment-a", "Experiments"],
          ["operations", "Operations"],
          ["settings", "Settings"],
        ]) {
          await navigate(route, title);
          await page.waitForTimeout(120);
          assert.equal(
            await page.evaluate(
              () => document.documentElement.scrollWidth <= innerWidth + 1,
            ),
            true,
            `${route} overflow at ${width}`,
          );
          await screenshot(`${route.split("/")[0]}-${width}`);
          fs.writeFileSync(
            path.join(output, `${route.split("/")[0]}-${width}.aria.yml`),
            await page.locator("body").ariaSnapshot(),
          );
        }
      }
    });
    await test("320, tablet, desktop and ultrawide reflow; reduced motion and forced colors", async () => {
      for (const [width, height] of [
        [320, 844],
        [768, 1024],
        [1920, 1080],
        [2560, 1080],
      ]) {
        await page.setViewportSize({ width, height });
        await navigate("overview", "Fleet overview");
        assert.equal(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth + 1,
          ),
          true,
          `overflow ${width}`,
        );
        await screenshot(`overview-${width}`);
      }
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.emulateMedia({
        reducedMotion: "reduce",
        forcedColors: "active",
      });
      await navigate("configuration/serve-a", "Configuration");
      await screenshot("configuration-forced-colors");
      await page.emulateMedia({
        reducedMotion: "reduce",
        forcedColors: "none",
      });
      await page.setViewportSize({ width: 720, height: 450 });
      await navigate("configuration/serve-a", "Configuration");
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
        true,
      );
      await screenshot("configuration-200-percent-equivalent-reflow");
    });
    await test("Mobile navigation is inert when closed, traps focus when open, and restores focus", async () => {
      await page.setViewportSize({ width: 390, height: 844 });
      await navigate("overview", "Fleet overview");
      assert.equal(
        await page.locator("#navigation").evaluate((el) => el.inert),
        true,
      );
      await page.getByRole("button", { name: "Open navigation" }).click();
      assert.equal(
        await page.locator(".workspace").evaluate((el) => el.inert),
        true,
      );
      await page
        .getByRole("navigation", { name: "Main", exact: true })
        .getByRole("link", { name: "Settings", exact: true })
        .focus();
      await page.keyboard.press("Tab");
      assert.ok(
        await page.evaluate(
          () => !!document.activeElement.closest("#navigation"),
        ),
      );
      await page.keyboard.press("Escape");
      assert.equal(
        await page
          .getByRole("button", { name: "Open navigation" })
          .evaluate((el) => document.activeElement === el),
        true,
      );
      assert.equal(
        await page.locator(".workspace").evaluate((el) => el.inert),
        false,
      );
    });
    await test("Root-mounted shell uses root-local API and preserves refresh deep links", async () => {
      const root = await createFixture("/");
      root.state.authenticated = true;
      const rootPage = await context.newPage();
      try {
        await rootPage.goto(root.url + "#/serves/serve-a/metrics");
        await rootPage
          .getByRole("heading", { name: "Primary reasoning", exact: true })
          .waitFor();
        await rootPage
          .getByRole("heading", { name: "Generation throughput", exact: true })
          .waitFor();
        assert.ok(
          root.state.reads.some(
            (read) =>
              read.route === "metrics" && read.query.serve === "serve-a",
          ),
        );
        await rootPage.reload();
        await rootPage
          .getByRole("heading", { name: "Primary reasoning", exact: true })
          .waitFor();
        assert.equal(root.state.posts.length, 0);
      } finally {
        await rootPage.close();
        await root.close();
      }
    });
    await test("Named controls remain reachable and unobscured at 320px and zoom-equivalent reflow", async () => {
      for (const [width, height] of [
        [320, 844],
        [720, 450],
      ]) {
        await page.setViewportSize({ width, height });
        for (const [path, title] of [
          ["configuration/serve-a", "Configuration"],
          ["experiments/experiment-a", "Experiments"],
        ]) {
          await navigate(path, title);
          const unnamed = await page
            .locator("main input,main select,main button")
            .evaluateAll((nodes) =>
              nodes
                .filter((node) => {
                  const labelled = (node.getAttribute("aria-labelledby") || "")
                    .split(/\s+/)
                    .map((id) => document.getElementById(id)?.textContent || "")
                    .join(" ")
                    .trim();
                  return !(
                    labelled ||
                    node.getAttribute("aria-label") ||
                    [...(node.labels || [])]
                      .map((label) => label.textContent)
                      .join("") ||
                    (node.tagName === "BUTTON" && node.textContent)
                  );
                })
                .map((node) => node.outerHTML),
            );
          assert.deepEqual(unnamed, [], `${path} has unnamed controls`);
          const controls = page.locator("main button.primary");
          for (let index = 0; index < (await controls.count()); index++) {
            const control = controls.nth(index);
            await control.scrollIntoViewIfNeeded();
            assert.equal(
              await control.evaluate((node) => {
                const rect = node.getBoundingClientRect();
                const hit = document.elementFromPoint(
                  rect.x + rect.width / 2,
                  rect.y + rect.height / 2,
                );
                return (
                  rect.x >= 0 &&
                  rect.right <= innerWidth + 1 &&
                  rect.y >= 0 &&
                  rect.bottom <= innerHeight + 1 &&
                  (hit === node || node.contains(hit))
                );
              }),
              true,
              `${path} primary action obscured at ${width}`,
            );
          }
        }
      }
    });
    await test("No unsafe HTML, CSP violations, console errors, or browser bearer control", async () => {
      assert.deepEqual(errors, []);
      assert.ok(
        fixture.state.posts
          .filter((p) => p.route !== "session")
          .every((p) => p.csrf === "synthetic-csrf"),
      );
      const source = fs.readFileSync(
        path.resolve(
          __dirname,
          "../../../anvil_serving/observability/dashboard/static/observatory.js",
        ),
        "utf8",
      );
      assert.equal(source.includes("innerHTML"), false);
    });
    fs.writeFileSync(
      path.join(output, "results.json"),
      JSON.stringify(
        {
          fixture: true,
          base_path: "/nested/observatory/",
          tests: results,
          screenshots: fs.readdirSync(output).filter((x) => x.endsWith(".png")),
          limitations: [
            "Synthetic owner only; no live mutation qualification.",
            "720 by 450 viewport tests reflow equivalent to 200% desktop zoom; native browser zoom and screen-reader pairing need separate verification.",
          ],
        },
        null,
        2,
      ),
    );
  } catch (error) {
    fs.writeFileSync(
      path.join(output, "failure.json"),
      JSON.stringify(
        {
          tests: results,
          error: error.stack,
          url: page.url(),
          console: errors,
        },
        null,
        2,
      ),
    );
    await screenshot("failure");
    throw error;
  } finally {
    await context.close();
    await browser.close();
    await fixture.close();
  }
})().catch((error) => {
  process.stderr.write(error.stack + "\n");
  process.exitCode = 1;
});
