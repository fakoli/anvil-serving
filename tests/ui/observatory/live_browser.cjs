"use strict";
// Authorized read-only deployment walkthrough. Credentials are read in process,
// never written to evidence, and never used on a lifecycle endpoint.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const site = new URL(process.argv[2]);
const output = path.resolve(process.argv[3]);
if (site.protocol !== "https:" || !site.pathname.endsWith("/"))
  throw new Error("Use an exact HTTPS deployment base path.");
fs.mkdirSync(output, { recursive: true });

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
    headless: true,
    args: ["--disable-gpu"],
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();
  const report = {
    fixture: false,
    observed_at: new Date().toISOString(),
    origin: site.origin,
    base_path: site.pathname,
    routes: [],
    charts: [],
    assets: {},
    console_errors: [],
    attempted_mutations: [],
  };
  page.on("pageerror", (error) => report.console_errors.push(error.message));
  page.on("request", (request) => {
    if (
      request.method() !== "GET" &&
      !new URL(request.url()).pathname.endsWith("/session")
    )
      report.attempted_mutations.push({
        method: request.method(),
        route: new URL(request.url()).pathname,
      });
  });
  page.on("response", async (response) => {
    const name = new URL(response.url()).pathname;
    if (
      response.status() === 200 &&
      /\.(?:js|css)$/.test(name) &&
      name.startsWith(site.pathname)
    ) {
      try {
        report.assets[name] = crypto
          .createHash("sha256")
          .update(await response.body())
          .digest("hex");
      } catch {}
    }
  });
  const read = async (route) =>
    page.evaluate(
      async ({ base, route }) => {
        const response = await fetch(
          new URL("api/observatory/v1/" + route, base),
          {
            cache: "no-store",
            credentials: "same-origin",
            redirect: "error",
            signal: AbortSignal.timeout(20000),
          },
        );
        return { status: response.status, payload: await response.json() };
      },
      { base: site.href, route },
    );
  const capture = async (name) => {
    await page
      .locator("main .loading")
      .first()
      .waitFor({ state: "hidden", timeout: 25000 })
      .catch(() => {});
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({
      path: path.join(output, name + ".png"),
      fullPage: true,
    });
    fs.writeFileSync(
      path.join(output, name + ".aria.yml"),
      await page.locator("body").ariaSnapshot(),
    );
  };
  try {
    await page.goto(site.href, {
      waitUntil: "domcontentloaded",
      timeout: 30000,
    });
    await page
      .getByLabel("Username", { exact: true })
      .fill(process.env.OBSERVATORY_USERNAME);
    let password = fs
      .readFileSync(process.env.OBSERVATORY_PASSWORD_FILE, "utf8")
      .trim();
    await page.getByLabel("Password", { exact: true }).fill(password);
    password = "";
    await page
      .locator("main")
      .getByRole("button", { name: "Sign in", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Fleet overview", exact: true })
      .waitFor({ timeout: 45000 });
    const sessionResult = await read("session");
    const session = sessionResult.payload.data;
    assert.equal(session.authenticated, true);
    assert.equal(
      session.operate,
      false,
      "Expected this deployment to remain in read-only mode.",
    );
    report.session = {
      authenticated: session.authenticated,
      role: session.role,
      operate: session.operate,
      build: session.build,
      fixture: session.fixture,
      base_path: session.base_path,
    };
    const fleetResponse = await read("fleet");
    const fleet = fleetResponse.payload.data;
    assert.ok(Array.isArray(fleet.hosts) && Array.isArray(fleet.serves));
    report.fleet = {
      status: fleetResponse.status,
      coverage: fleet.coverage,
      hosts: fleet.hosts.map((host) => ({
        id: host.id,
        platform: host.platform,
        controller: host.controller?.status,
        telemetry: host.telemetry?.status,
        gpus: host.gpus?.map((gpu) => ({
          id: gpu.id,
          memory: gpu.memory_used?.status,
          utilization: gpu.utilization?.status,
        })),
      })),
      serves: fleet.serves.map((serve) => ({
        id: serve.id,
        host_id: serve.host_id,
        readiness: serve.readiness,
        runtime_state: serve.runtime_state,
        metrics: Object.fromEntries(
          Object.entries(serve.metrics || {}).map(([name, metric]) => [
            name,
            { status: metric.status, value: metric.value },
          ]),
        ),
      })),
    };
    const settingsResponse = await read("settings");
    const settings = settingsResponse.payload.data;
    const charts = settings.integrations?.charts || [];
    const catalog = Array.isArray(charts)
      ? charts.map((item) => (typeof item === "string" ? item : item.id))
      : Object.keys(charts);
    for (const chart of catalog) {
      const response = await read(
        "metrics?" + new URLSearchParams({ chart, range: "1h" }),
      );
      const data = response.payload.data;
      report.charts.push({
        id: chart,
        http_status: response.status,
        status: data?.status,
        reason: data?.reason || response.payload.error?.message,
        series: data?.series?.length || 0,
        samples:
          data?.series?.reduce(
            (total, series) => total + series.points.length,
            0,
          ) || 0,
      });
    }
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      for (const [route, title] of [
        ["overview", "Fleet overview"],
        ["workstations", "Workstations"],
        ["serves", "Serves"],
        ["workloads", "Workloads"],
        ["configuration", "Configuration"],
        ["experiments", "Experiments"],
        ["operations", "Operations"],
        ["settings", "Settings"],
      ]) {
        await page.goto(site.href + "#/" + route, {
          waitUntil: "domcontentloaded",
        });
        await page
          .getByRole("heading", { name: title, exact: true })
          .first()
          .waitFor({ timeout: 45000 });
        await page.waitForTimeout(500);
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth + 1,
        );
        const alerts = await page
          .locator("main [role=alert]")
          .allTextContents();
        const enabledPrimary = await page
          .locator("main button.primary:not(:disabled)")
          .allTextContents();
        report.routes.push({
          route,
          width,
          overflow,
          alerts,
          enabled_primary: enabledPrimary,
        });
        assert.equal(overflow, false, `${route} overflow at ${width}`);
        await capture(`${route}-${width}`);
      }
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    for (const host of fleet.hosts) {
      await page.goto(
        site.href + "#/workstations/" + encodeURIComponent(host.id),
      );
      await page
        .getByRole("heading", {
          name: host.display_name || host.id,
          exact: true,
        })
        .waitFor({ timeout: 45000 });
      await page.waitForTimeout(500);
      await capture("host-" + host.id);
    }
    for (const serve of fleet.serves)
      for (const tab of [
        "overview",
        "configuration",
        "metrics",
        "logs",
        "evidence",
      ]) {
        await page.goto(
          site.href + "#/serves/" + encodeURIComponent(serve.id) + "/" + tab,
        );
        await page
          .getByRole("heading", {
            name: serve.display_name || serve.id,
            exact: true,
          })
          .waitFor({ timeout: 45000 });
        await page.waitForTimeout(300);
        await capture("serve-" + serve.id + "-" + tab);
      }
    const cookie = (await context.cookies()).find(
      (item) => item.name === "anvil_observatory_session",
    );
    report.cookie = {
      secure: cookie?.secure,
      http_only: cookie?.httpOnly,
      same_site: cookie?.sameSite,
      path: cookie?.path,
    };
    assert.deepEqual(report.attempted_mutations, []);
    report.status = "passed";
    fs.writeFileSync(
      path.join(output, "results.json"),
      JSON.stringify(report, null, 2),
    );
    process.stdout.write(
      JSON.stringify({
        status: report.status,
        build: report.session.build,
        hosts: report.fleet.hosts.length,
        serves: report.fleet.serves.length,
        gpus: report.fleet.hosts.reduce(
          (total, host) => total + host.gpus.length,
          0,
        ),
        charts: report.charts.length,
        route_checks: report.routes.length,
        mutations: 0,
      }) + "\n",
    );
  } catch (error) {
    report.status = "failed";
    report.error = error.message;
    fs.writeFileSync(
      path.join(output, "results.json"),
      JSON.stringify(report, null, 2),
    );
    await capture("failure");
    throw error;
  } finally {
    await context.close();
    await browser.close();
  }
})().catch((error) => {
  process.stderr.write(error.message + "\n");
  process.exitCode = 1;
});
