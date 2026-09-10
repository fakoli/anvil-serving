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
    { stdio: ["pipe", "pipe", "pipe"] },
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
  } finally {
    await browser.close();
    fixture.stdin.end('{"command":"stop"}\n');
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
