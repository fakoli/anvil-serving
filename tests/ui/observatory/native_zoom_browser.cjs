"use strict";
// Native Chrome page-zoom preference in a disposable profile; no CSS emulation.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const { createFixture } = require("./fixture_server.cjs");
const output = path.resolve(
  process.argv[2] || "test-results/observatory-native-zoom",
);
fs.mkdirSync(output, { recursive: true });
(async () => {
  const profile = fs.mkdtempSync(path.join(output, "chrome-profile-"));
  const fixture = await createFixture();
  fixture.state.authenticated = true;
  const context = await chromium.launchPersistentContext(profile, {
    executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
    headless: false,
    viewport: null,
    args: ["--disable-gpu", "--ozone-platform=x11", "--window-size=1440,1000"],
  });
  const page = context.pages()[0];
  const report = {
    fixture: true,
    observed_at: new Date().toISOString(),
    method:
      "Native Chrome Settings > Appearance > Page zoom > 200% in a disposable profile",
    routes: [],
    status: "running",
  };
  const geometry = () =>
    page.evaluate(() => ({
      dpr: devicePixelRatio,
      width: innerWidth,
      height: innerHeight,
      outer_width: outerWidth,
      overflow: document.documentElement.scrollWidth > innerWidth + 1,
    }));
  try {
    await page.goto(fixture.url + "#/overview");
    await page
      .getByRole("heading", { name: "Fleet overview", exact: true })
      .waitFor();
    report.before = await geometry();
    const settings = await context.newPage();
    await settings.goto("chrome://settings/appearance");
    await settings.locator("#zoomLevel").selectOption("2");
    assert.equal(await settings.locator("#zoomLevel").inputValue(), "2");
    await settings.close();
    await page.bringToFront();
    await page.reload();
    report.after = await geometry();
    assert.equal(report.after.dpr / report.before.dpr, 2);
    assert.ok(Math.abs(report.after.width * 2 - report.before.width) <= 2);
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
      await page.goto(fixture.url + "#/" + route);
      await page
        .getByRole("heading", { name: title, exact: true })
        .first()
        .waitFor();
      await page.locator("main .loading").first().waitFor({ state: "hidden" });
      const bounds = await geometry();
      assert.equal(
        bounds.overflow,
        false,
        route + " page overflow at native200%",
      );
      const primary = page.locator("main button.primary");
      for (const button of await primary.all()) {
        await button.scrollIntoViewIfNeeded();
        assert.ok(
          await button.evaluate((node) => {
            const box = node.getBoundingClientRect();
            const hit = document.elementFromPoint(
              box.x + box.width / 2,
              box.y + box.height / 2,
            );
            return (
              box.x >= 0 &&
              box.right <= innerWidth + 1 &&
              box.y >= 0 &&
              box.bottom <= innerHeight + 1 &&
              (hit === node || node.contains(hit))
            );
          }),
        );
      }
      const tables = await page
        .locator(".table-wrap")
        .evaluateAll((nodes) =>
          nodes.map((node) => ({
            caption: node.querySelector("caption")?.textContent,
            overflow_x: getComputedStyle(node).overflowX,
            bounded: node.getBoundingClientRect().width <= innerWidth,
            scrolls: node.scrollWidth > node.clientWidth,
          })),
        );
      for (const table of tables) {
        assert.ok(table.caption);
        assert.ok(table.bounded);
        assert.equal(table.overflow_x, "auto");
      }
      report.routes.push({
        route,
        ...bounds,
        primary_controls_reachable: true,
        tables,
      });
      const cdp = await context.newCDPSession(page);
      const capture = await cdp.send("Page.captureScreenshot", {
        format: "png",
        fromSurface: true,
        captureBeyondViewport: false,
      });
      fs.writeFileSync(path.join(output, route.split("/")[0] + "-native-200.png"), Buffer.from(capture.data, "base64"));
      await cdp.detach();
    }
    report.status = "passed";
    process.stdout.write(
      "PASS native Chrome200% zoom: measured DPR doubled, viewport halved, eight routes and primary controls checked\n",
    );
  } catch (error) {
    report.status = "failed";
    report.error = error.stack;
    throw error;
  } finally {
    fs.writeFileSync(
      path.join(output, "results.json"),
      JSON.stringify(report, null, 2),
    );
    await context.close();
    await fixture.close();
    fs.rmSync(profile, { recursive: true, force: true });
  }
})().catch((error) => {
  process.stderr.write(error.stack + "\n");
  process.exitCode = 1;
});
