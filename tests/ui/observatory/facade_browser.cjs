"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");
const { spawn } = require("node:child_process");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const output = path.resolve(
  process.argv[2] || "test-results/observatory-real-facade",
);
fs.mkdirSync(output, { recursive: true });

(async () => {
  const child = spawn(
    process.env.OBSERVATORY_TEST_PYTHON || "python3",
    [path.join(__dirname, "real_fixture.py"), output],
    { stdio: ["pipe", "pipe", "pipe"] },
  );
  const lines = readline.createInterface({ input: child.stdout });
  const waiting = [],
    buffered = [];
  let stderr = "";
  child.stderr.on("data", (chunk) => {
    stderr += chunk.toString();
  });
  lines.on("line", (line) => {
    const item = JSON.parse(line);
    if (waiting.length) waiting.shift()(item);
    else buffered.push(item);
  });
  const next = () =>
    buffered.length
      ? Promise.resolve(buffered.shift())
      : new Promise((resolve, reject) => {
          const timer = setTimeout(
            () =>
              reject(
                new Error("Fixture response timeout: " + stderr.slice(-2000)),
              ),
            10000,
          );
          waiting.push((item) => {
            clearTimeout(timer);
            resolve(item);
          });
        });
  const command = (name) => {
    child.stdin.write(JSON.stringify({ command: name }) + "\n");
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
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  try {
    await page.goto(url + "#/configuration/serve-fixture-a");
    await page
      .getByLabel("Username", { exact: true })
      .fill("operator", { timeout: 5000 });
    await page.getByLabel("Password", { exact: true }).fill("fixture-password");
    await page
      .locator("main")
      .getByRole("button", { name: "Sign in", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Configuration", exact: true })
      .waitFor();
    await page.locator("#setting-max_output").fill("32");
    assert.deepEqual(await command("status"), {
      mutations: 0,
      configured: 64,
      observed: 64,
    });
    await page.getByRole("button", { name: "Validate", exact: true }).click();
    await page
      .getByRole("button", { name: "Review impact", exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByText("serve-fixture-a", { exact: true })
      .waitFor();
    assert.deepEqual(await command("status"), {
      mutations: 0,
      configured: 64,
      observed: 64,
    });
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Apply change", exact: true })
      .click();
    await page.waitForURL(/#\/operations\/[a-zA-Z0-9_.:-]+$/);
    await page
      .getByRole("dialog")
      .getByText("Fixture owner checked independently.", { exact: true })
      .waitFor();
    assert.deepEqual(await command("status"), {
      mutations: 1,
      configured: 32,
      observed: 32,
    });
    const operationURL = page.url();
    await page.reload();
    await page
      .getByRole("dialog")
      .getByText("Fixture owner checked independently.", { exact: true })
      .waitFor();
    assert.equal(page.url(), operationURL);
    assert.deepEqual(await command("status"), {
      mutations: 1,
      configured: 32,
      observed: 32,
    });
    await page.screenshot({
      path: path.join(output, "real-facade-operation.png"),
      fullPage: true,
    });
    await page.keyboard.press("Escape");
    await page.goto(url + "#/configuration/serve-fixture-a");
    await page
      .getByRole("heading", { name: "Configuration", exact: true })
      .waitFor();
    assert.equal(
      await page.locator('[data-configured="max_output"]').textContent(),
      "32",
    );
    assert.equal(
      await page.locator('[data-observed="max_output"]').textContent(),
      "32",
    );
    // From the loaded configuration page, every interaction in this journey
    // is a keyboard event. Locators only observe focus and resulting state.
    const keyboardPath = [];
    const tabTo = async (selector) => {
      for (let attempt = 0; attempt < 100; attempt += 1) {
        if (await page.locator(selector + ":focus").count()) {
          keyboardPath.push(selector);
          return;
        }
        await page.keyboard.press("Tab");
      }
      throw new Error("Keyboard could not reach " + selector);
    };
    await tabTo("#setting-max_output");
    await page.keyboard.press("Control+A");
    await page.keyboard.type("16");
    await tabTo("main button.primary");
    await page.keyboard.press("Enter");
    await page
      .getByRole("button", { name: "Review impact", exact: true })
      .waitFor();
    await tabTo("main .draft-strip button:last-of-type");
    await page.keyboard.press("Enter");
    await page
      .getByRole("dialog")
      .getByText("serve-fixture-a", { exact: true })
      .waitFor();
    assert.deepEqual(await command("status"), {
      mutations: 1,
      configured: 32,
      observed: 32,
    });
    await page.setViewportSize({ width: 320, height: 844 });
    const dialogBounds = await page.getByRole("dialog").boundingBox();
    assert.ok(
      dialogBounds.x >= 0 && dialogBounds.x + dialogBounds.width <= 321,
    );
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth + 1,
      ),
      false,
    );
    await tabTo("#operation-dialog button.primary");
    const applyBounds = await page
      .locator("#operation-dialog button.primary")
      .boundingBox();
    assert.ok(applyBounds.x >= 0 && applyBounds.x + applyBounds.width <= 321);
    assert.ok(applyBounds.y >= 0 && applyBounds.y + applyBounds.height <= 845);
    await page.screenshot({
      path: path.join(output, "keyboard-preview-320.png"),
      fullPage: true,
    });
    fs.writeFileSync(
      path.join(output, "keyboard-preview-320.aria.yml"),
      await page.locator("body").ariaSnapshot(),
    );
    await page.keyboard.press("Enter");
    await page.waitForURL(
      (value) =>
        value.href !== operationURL &&
        /#\/operations\/[a-zA-Z0-9_.:-]+$/.test(value.href),
    );
    await page
      .getByRole("dialog")
      .getByText("Fixture owner checked independently.", { exact: true })
      .waitFor();
    assert.deepEqual(await command("status"), {
      mutations: 2,
      configured: 16,
      observed: 16,
    });
    await page.screenshot({
      path: path.join(output, "keyboard-operation-320.png"),
      fullPage: true,
    });
    fs.writeFileSync(
      path.join(output, "keyboard-operation-320.aria.yml"),
      await page.locator("body").ariaSnapshot(),
    );
    const cookies = await page.context().cookies();
    const cookie = cookies.find(
      (item) => item.name === "anvil_observatory_session",
    );
    assert.ok(
      cookie?.httpOnly && cookie?.secure && cookie?.sameSite === "Strict",
    );
    assert.deepEqual(
      await page.evaluate(() => Object.keys(sessionStorage)),
      [],
    );
    fs.writeFileSync(
      path.join(output, "results.json"),
      JSON.stringify(
        {
          fixture: true,
          status: "passed",
          server: "real Console, Access, IntentStore and static composition",
          owner: "independent deterministic regression fixture",
          accepted_mutations: 2,
          verified_configured: 16,
          verified_observed: 16,
          refresh_replay: false,
          secure_cookie: true,
          keyboard_journey: {
            interaction_method: "Tab, Control+A, typing, Enter only",
            focus_path: keyboardPath,
            accepted_mutations: 1,
            explicit_confirmation: "Apply change",
            verified_result: true,
          },
          operation_dialog_320: {
            no_horizontal_overflow: true,
            apply_visible: true,
            bounds: dialogBounds,
          },
        },
        null,
        2,
      ),
    );
    process.stdout.write(
      "PASS real facade browser: authentication, draft, preview, durable apply, independent verification, refresh, keyboard-only apply, 320px dialog\n",
    );
  } catch (error) {
    await page.screenshot({
      path: path.join(output, "failure.png"),
      fullPage: true,
    });
    fs.writeFileSync(
      path.join(output, "failure.json"),
      JSON.stringify({ error: error.stack, stderr, consoleErrors }, null, 2),
    );
    throw error;
  } finally {
    await browser.close();
    child.stdin.end(JSON.stringify({ command: "stop" }) + "\n");
    await new Promise((resolve) => child.once("exit", resolve));
  }
})().catch((error) => {
  process.stderr.write(error.stack + "\n");
  process.exitCode = 1;
});
