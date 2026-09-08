"use strict";
// Actual Orca/AT-SPI/Speech Dispatcher pairing in a private desktop test session.
const fs = require("node:fs");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const { createFixture } = require("./fixture_server.cjs");
const output = path.resolve(process.argv[2] || "test-results/observatory-orca");
fs.mkdirSync(output, { recursive: true });
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
(async () => {
  if (process.argv[3] !== "--isolated") {
    const sandbox = fs.mkdtempSync(path.join(path.dirname(output), ".at-"));
    const environment = { ...process.env, GDK_BACKEND: "x11" };
    delete environment.AT_SPI_BUS_ADDRESS;
    for (const [key, name] of [
      ["XDG_CONFIG_HOME", "config"],
      ["XDG_DATA_HOME", "data"],
      ["XDG_CACHE_HOME", "cache"],
      ["XDG_RUNTIME_DIR", "runtime"],
    ]) {
      const directory = path.join(sandbox, name);
      fs.mkdirSync(directory, { mode: 0o700 });
      environment[key] = directory;
    }
    const log = fs.openSync(path.join(output, "bus-process.log"), "w");
    try {
      const child = spawnSync(
        "dbus-run-session",
        ["--", process.execPath, __filename, output, "--isolated"],
        {
          env: environment,
          timeout: 45000,
          encoding: "utf8",
          stdio: ["ignore", "pipe", log],
        },
      );
      process.stdout.write(child.stdout || "");
      if (child.error) throw child.error;
      assert.equal(
        child.status,
        0,
        "Isolated AT check failed; inspect its retained receipt.",
      );
    } finally {
      fs.closeSync(log);
      fs.rmSync(sandbox, { recursive: true, force: true });
    }
    return;
  }
  const fixture = await createFixture();
  fixture.state.authenticated = true;
  const operation = {
    id: "operation-at",
    resource_id: "serve-a",
    host_id: "host-fixture-a",
    label: "Isolated accessibility check",
    status: "running",
    native_state: "running",
    execution_outcome: "pending",
    verification: { status: "pending" },
    recovery: { status: "not_required" },
    events: [],
  };
  fixture.state.operations.push(operation);
  const report = {
    fixture: true,
    observed_at: new Date().toISOString(),
    isolation:
      "Private DBus, XDG config/data/runtime and disposable Chrome profile; isolated HTTP owner",
    versions: {
      orca: spawnSync("orca", ["--version"], {
        encoding: "utf8",
      }).stdout?.trim(),
    },
    status: "running",
  };
  const profile = fs.mkdtempSync(path.join(output, "chrome-profile-"));
  const debugPath = path.join(output, "orca-debug.log");
  let stderr = "";
  const orca = spawn(
    "orca",
    [
      "--debug",
      "--debug-file",
      debugPath,
      "--speech-system",
      "speechdispatcherfactory",
    ],
    { stdio: ["ignore", "ignore", "pipe"] },
  );
  orca.stderr.on("data", (chunk) => (stderr += chunk.toString()));
  let context;
  try {
    await delay(1800);
    context = await chromium.launchPersistentContext(profile, {
      executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
      headless: false,
      viewport: { width: 1200, height: 900 },
      args: [
        "--disable-gpu",
        "--ozone-platform=x11",
        "--force-renderer-accessibility",
      ],
    });
    const page = context.pages()[0];
    await page.goto(fixture.url + "#/operations/operation-at");
    await page.getByRole("dialog").waitFor();
    await page.bringToFront();
    await page.keyboard.press("Tab");
    await delay(4500);
    report.repeated_unchanged_polls =
      fixture.state.reads.filter(
        (item) => item.route === "operations/operation-at",
      ).length - 1;
    assert.ok(report.repeated_unchanged_polls >= 2);
    await page.bringToFront();
    await delay(350);
    assert.equal(await page.evaluate(() => document.hasFocus()), true);
    operation.verification.status = "failed";
    operation.execution_outcome = "succeeded";
    operation.status = "failed";
    await page.evaluate(() =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    await page
      .getByRole("heading", { name: "Verification failed", exact: true })
      .waitFor();
    await delay(4000);
    await context.close();
    context = null;
    orca.kill("SIGTERM");
    await Promise.race([
      new Promise((resolve) => orca.once("exit", resolve)),
      delay(3000),
    ]);
    // Orca buffers its debug stream. Read only after its shutdown flushed it.
    const debug = fs.readFileSync(debugPath, "utf8");
    const speech = debug
      .split("\n")
      .filter((line) => line.includes("SPEECH OUTPUT:"));
    const outputLines = speech.filter((line) =>
      line.includes("SPEECH OUTPUT: 'Operation verification failed."),
    );
    assert.equal(outputLines.length, 1);
    assert.ok(speech.some((line) => line.includes("Close dialog")));
    assert.ok(
      debug.includes(
        "SPEECH DISPATCHER: Speaking 'Operation verification failed.",
      ),
    );
    const initialStateLines = speech.filter((line) =>
      line.includes("SPEECH OUTPUT: 'Operation running."),
    );
    assert.ok(
      initialStateLines.length <= 1,
      "Unchanged polling must not repeat the operation state.",
    );
    report.status = "passed";
    report.changed_outcome_speech = outputLines;
    report.initial_state_speech_count = initialStateLines.length;
    report.unchanged_poll_repeat_speech = 0;
    report.close_button_spoken = true;
    report.speech_dispatcher_call_observed = true;
    report.limitations = [
      "Automatic actual Orca speech generation/dispatch observed; no human listening or braille usability certification.",
      "The isolated X11 bridge reported an XKEYBOARD warning; observed speech and AT events still passed.",
    ];
    process.stdout.write(
      "PASS actual Orca/Chrome pairing: labeled Close control, one changed outcome speech dispatch, no repeated-state speech\n",
    );
  } catch (error) {
    report.status = "failed";
    report.error = error.stack;
    throw error;
  } finally {
    await context?.close();
    if (orca.exitCode === null) orca.kill("SIGTERM");
    await fixture.close();
    report.orca_stderr = stderr;
    fs.writeFileSync(
      path.join(output, "results.json"),
      JSON.stringify(report, null, 2),
    );
    fs.rmSync(profile, { recursive: true, force: true });
  }
})().catch((error) => {
  process.stderr.write(error.stack + "\n");
  process.exitCode = 1;
});
