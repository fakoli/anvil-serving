const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const { spawn } = require("node:child_process");
const readline = require("node:readline");
const fs = require("node:fs");
const assert = require("node:assert/strict");
const path = require("node:path");
const root = process.env.PI_FIXTURE_OUTPUT;
(async () => {
  const child = spawn(
    process.env.PI_FIXTURE_BINARY,
    ["-test.run", "^TestBrowserFixture$", "-test.v"],
    {
      env: {
        PATH: process.env.PATH,
        ANVIL_CONNECT_BROWSER_FIXTURE: "1",
        ANVIL_CONNECT_PI_EMBED_ORIGIN: process.env.PI_FIXTURE_ORIGIN,
      },
      stdio: ["pipe", "pipe", "pipe"],
    },
  );
  let browser;
  const pending = [];
  let stderr = "";
  child.stderr.on("data", (x) => (stderr = (stderr + x).slice(-16384)));
  const lines = readline.createInterface({ input: child.stdout });
  const results = [];
  try {
    const ready = await new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(Error("Connect fixture timeout " + stderr)),
        10000,
      );
      lines.on("line", (line) => {
        try {
          const v = JSON.parse(line);
          if (v.url) {
            clearTimeout(timer);
            resolve(v);
          } else if (v.ack) pending.shift()?.(v.ack);
        } catch { stderr = (stderr + line + "\n").slice(-16384); }
      });
      child.on("error", reject);
      child.on("exit", (code) => {
        clearTimeout(timer);
        reject(Error("Connect exited " + code + " " + stderr));
      });
    });
    const command = (text) =>
      new Promise((resolve, reject) => {
        const timer = setTimeout(
          () => reject(Error("fixture command timeout")),
          5000,
        );
        pending.push((value) => {
          clearTimeout(timer);
          resolve(value);
        });
        child.stdin.write(text + "\n");
      });
    browser = await chromium.launch({
      executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
      headless: true,
      args: [
        "--disable-gpu",
        "--no-proxy-server",
        "--disable-quic",
        `--host-resolver-rules=MAP dash.example.test:443 ${ready.resolver},MAP idp.example.test:443 ${ready.resolver},MAP console.example.test:443 ${ready.resolver}`,
        `--ignore-certificate-errors-spki-list=${ready.spki}`,
      ],
    });
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(ready.url);
    await page.waitForTimeout(1200);
    const call = (route, body) =>
      page.evaluate(
        async ({ route, body }) => {
          const r = await fetch(route, {
            method: body ? "POST" : "GET",
            redirect: "manual",
            headers: body ? { "Content-Type": "application/json" } : {},
            body: body ? JSON.stringify(body) : undefined,
          });
          return { status: r.status, body: await r.text() };
        },
        { route, body },
      );
    let created = await call("/api/agent/new", {
      cwd: process.env.PI_FIXTURE_PROJECT,
      type: "prompt",
      message: "fixture seed",
      provider: "fixture",
      modelId: "fixture-a",
      thinkingLevel: "off",
    });
    assert.equal(created.status, 200);
    const id = JSON.parse(created.body).sessionId;
    assert.ok(id);
    assert.equal(
      (
        await call("/api/agent/" + id, {
          type: "set_session_name",
          name: "Embed fixture " + id,
        })
      ).status,
      200,
    );
    await page.waitForTimeout(1500);
    const before = await call("/api/sessions?force=1");
    assert.equal(before.status, 200);
    assert.equal(JSON.parse((await call("/api/agent/" + id)).body).state.isStreaming, true);
    results.push({
      check:
        "real pinned Pi creates retained synthetic session through Connect",
      passed: true,
      id,
    });
    await page.goto("https://console.example.test/");
    const frame = page.frameLocator("iframe");
    await frame
      .getByText("Embed fixture " + id, { exact: false })
      .first()
      .waitFor({ timeout: 15000 });
    await frame
      .getByText("Embed fixture " + id, { exact: false })
      .first()
      .click();
    await frame
      .getByText("Synthetic Pi integration response.", { exact: false })
      .first()
      .waitFor({ timeout: 15000 });
    await page.screenshot({
      path: path.join(root, "embedded-pi.png"),
      fullPage: true,
    });
    results.push({
      check: "retained session and transcript render in separate-origin frame",
      passed: true,
    });
    await page.reload();
    await frame
      .getByText("Embed fixture " + id, { exact: false })
      .first()
      .waitFor({ timeout: 15000 });
    await frame.getByText("Embed fixture " + id, { exact: false }).first().click();
    const second = await context.newPage();
    await second.goto("https://console.example.test/");
    const secondFrame = second.frameLocator("iframe");
    await secondFrame.getByText("Embed fixture " + id, { exact: false }).first().click();
    await secondFrame.getByText("Synthetic Pi integration response.", { exact: false }).first().waitFor();
    const secondChild = second.frames().find((item) => item.url().startsWith(ready.url));
    const running = await secondChild.evaluate(async (id) => ({
      state: await fetch("/api/agent/" + id).then((r) => r.json()),
      sessions: await fetch("/api/agent/running").then((r) => r.json()),
    }), id);
    assert.equal(running.state.state.isStreaming, true);
    assert.deepEqual(running.sessions.runningSessionIds, [id]);
    fs.writeFileSync(path.join(root, "release-stream"), "release");
    await secondChild.waitForFunction(async (id) =>
      !(await fetch("/api/agent/" + id).then((r) => r.json())).state.isStreaming, id);
    await second.close();
    results.push({
      check: "active stream survives frame reload and second-tab attachment with one native writer",
      passed: true,
    });
    await page.goto(ready.url);
    const after = await call("/api/sessions?force=1");
    assert.deepEqual(
      JSON.parse(after.body)
        .sessions.map((x) => x.id)
        .sort(),
      JSON.parse(before.body)
        .sessions.map((x) => x.id)
        .sort(),
    );
    await page.evaluate((id) => {
      window.fixtureEvents = [];
      window.fixtureSource = new EventSource("/api/agent/" + id + "/events");
      window.fixtureSource.onmessage = (e) => {
        try {
          window.fixtureEvents.push(JSON.parse(e.data));
        } catch {}
      };
    }, id);
    await page.waitForFunction(() => window.fixtureEvents.length > 0);
    await page.evaluate((id) => {
      window.fixturePrompt = fetch("/api/agent/" + id, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: "prompt", message: "/fixture-confirm" }),
      }).then((r) => r.status);
    }, id);
    await page.waitForFunction(
      () => window.fixtureEvents.some((x) => x.type === "extension_ui_request"),
      { timeout: 10000 },
    );
    const question = await page.evaluate(() =>
      window.fixtureEvents.find((x) => x.type === "extension_ui_request"),
    );
    await page.reload();
    await page.evaluate((id) => {
      window.fixtureEvents = [];
      window.fixtureSource = new EventSource("/api/agent/" + id + "/events");
      window.fixtureSource.onmessage = (e) => {
        try { window.fixtureEvents.push(JSON.parse(e.data)); } catch {}
      };
    }, id);
    await page.waitForFunction((id) => window.fixtureEvents.some(
      (x) => x.type === "extension_ui_request" && x.id === id), question.id);
    const wrong = await call("/api/agent/" + id, {
      type: "extension_ui_response",
      id: "wrong-request",
      confirmed: true,
    });
    assert.equal(wrong.status, 200);
    await page.waitForTimeout(100);
    assert.equal(
      await page.evaluate(() =>
        window.fixtureEvents.some(
          (x) => x.method === "notify" && x.message === "Fixture confirmed",
        ),
      ),
      false,
    );
    const answered = await call("/api/agent/" + id, {
      type: "extension_ui_response",
      id: question.id,
      confirmed: true,
    });
    assert.equal(answered.status, 200);
    await page.waitForFunction(() =>
      window.fixtureEvents.some(
        (x) => x.method === "notify" && x.message === "Fixture confirmed",
      ),
    );
    await page.evaluate(() => window.fixtureSource.close());
    results.push({
      check: "pending extension confirmation keeps request identity across reconnect",
      passed: true,
    });
    const cookie = (await context.cookies(ready.url))
      .map((c) => c.name + "=" + c.value)
      .join("; ");
    const rejected = await context.request.post(
      "https://" + ready.resolver + "/api/agent/" + id,
      {
        ignoreHTTPSErrors: true,
        maxRedirects: 0,
        headers: {
          Host: "dash.example.test",
          Origin: "https://console.example.test",
          Cookie: cookie,
        },
        data: { type: "prompt", message: "forged cross-origin request" },
      },
    );
    assert.equal(rejected.status(), 403);
    results.push({
      check: "forged parent-origin command denied",
      passed: true,
    });
    await command("revoke member");
    const revoked = await call("/api/sessions?force=1");
    assert.notEqual(revoked.status, 200);
    results.push({
      check: "revoked Connect session denied",
      passed: true,
      status: revoked.status,
    });
    await command("subject operator");
    const operator = await browser.newContext();
    const operatorPage = await operator.newPage();
    await operatorPage.goto(ready.url);
    const operatorDenied = await operatorPage.evaluate(() => fetch("/api/sessions").then((r) => r.status));
    assert.notEqual(operatorDenied, 200);
    results.push({ check: "registered non-owner operator cannot access owner-only Pi resource", passed: true, status: operatorDenied });
    await operator.close();
    await command("subject denied");
    const foreign = await browser.newContext();
    const foreignPage = await foreign.newPage();
    await foreignPage.goto(ready.url);
    const denied = await foreignPage.evaluate(async () => {
      const r = await fetch("/api/sessions");
      return r.status;
    });
    assert.notEqual(denied, 200);
    results.push({
      check: "different ungranted principal denied",
      passed: true,
      status: denied,
    });
    await foreign.close();
    await command("subject allowed");
    const renewed = await browser.newContext();
    const renewedPage = await renewed.newPage();
    await renewedPage.goto(ready.url);
    assert.equal(
      await renewedPage.evaluate(() =>
        fetch("/api/sessions").then((r) => r.status),
      ),
      200,
    );
    await command("expire sessions");
    assert.equal(
      await renewedPage.evaluate(() =>
        fetch("/api/sessions", { redirect: "manual" }).then((r) => r.status),
      ),
      401,
    );
    results.push({ check: "expired Connect identity denied", passed: true });
    await renewed.close();
    fs.writeFileSync(
      path.join(root, "result.json"),
      JSON.stringify(results, null, 2),
    );
    console.log(JSON.stringify(results));
  } finally {
    fs.writeFileSync(
      path.join(root, "checks.json"),
      JSON.stringify(results, null, 2),
    );
    await browser?.close();
    child.stdin.end();
    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        child.kill("SIGTERM");
        resolve();
      }, 5000);
      child.on("exit", () => {
        clearTimeout(timer);
        resolve();
      });
    });
    lines.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
