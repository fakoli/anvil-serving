"use strict";
// One idle read-only browser. This is a bounded refresh check, not a load test.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const zlib = require("node:zlib");
const { execFileSync } = require("node:child_process");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const site = new URL(process.argv[2]);
const output = path.resolve(process.argv[3]);
if (site.protocol !== "https:" || !site.pathname.endsWith("/"))
  throw new Error("Use an exact HTTPS deployment base path.");
fs.mkdirSync(output, { recursive: true });
const sourceRoot = path.resolve(
  __dirname,
  "../../../anvil_serving/observability/dashboard/static",
);
const measureBytes = (body) => ({
  bytes: body.length,
  gzip_bytes: zlib.gzipSync(body).length,
  sha256: crypto.createHash("sha256").update(body).digest("hex"),
});

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
    window_seconds: 25,
    phases: [],
    assets: {},
    mutations: [],
    limitations: [
      "Short idle windows characterize this installed web build only; no model calls or load test.",
      "Docker samples describe the web container, not inference utilization.",
      "Gzip sizes are calculated per file; transport compression is not assumed.",
    ],
  };
  let currentPhase = null;
  const assetReads = [];
  page.on("request", (request) => {
    const address = new URL(request.url());
    if (request.method() !== "GET" && !address.pathname.endsWith("/session"))
      report.mutations.push({
        method: request.method(),
        route: address.pathname,
      });
    if (!currentPhase) return;
    currentPhase.requests += 1;
    const route = address.pathname.split("/api/observatory/v1/")[1];
    if (route) {
      const key =
        route === "metrics"
          ? "metrics:" + address.searchParams.get("chart")
          : route;
      currentPhase.api_requests[key] =
        (currentPhase.api_requests[key] || 0) + 1;
    }
  });
  page.on("response", (response) => {
    const address = new URL(response.url());
    if (
      response.status() !== 200 ||
      !address.pathname.startsWith(site.pathname)
    )
      return;
    const relative = address.pathname.slice(site.pathname.length);
    if (relative && !/\.(js|css)$/.test(relative)) return;
    const sourceName = relative || "observatory.html";
    if (sourceName.includes("..")) return;
    assetReads.push(
      (async () => {
        try {
          const body = await response.body();
          const source = fs.readFileSync(path.join(sourceRoot, sourceName));
          report.assets[sourceName] = {
            installed: measureBytes(body),
            source: measureBytes(source),
          };
        } catch (error) {
          report.assets[sourceName] = { unavailable: error.code || error.name };
        }
      })(),
    );
  });
  const stats = () => {
    if (!process.env.OBSERVATORY_WEB_CONTAINER)
      return { unavailable: "No container selected" };
    try {
      const raw = JSON.parse(
        execFileSync(
          "docker",
          [
            "stats",
            "--no-stream",
            "--format",
            "{{json .}}",
            process.env.OBSERVATORY_WEB_CONTAINER,
          ],
          { encoding: "utf8", timeout: 10000 },
        ),
      );
      return {
        cpu_percent: raw.CPUPerc,
        memory_usage: raw.MemUsage,
        memory_percent: raw.MemPerc,
        pids: raw.PIDs,
      };
    } catch (error) {
      return { unavailable: error.code || error.name };
    }
  };
  const phase = async (name, visibility) => {
    const item = {
      name,
      visibility,
      started_at: new Date().toISOString(),
      requests: 0,
      api_requests: {},
      container_before: stats(),
    };
    currentPhase = item;
    const before = Date.now();
    await new Promise((resolve) => setTimeout(resolve, 25000));
    item.elapsed_ms = Date.now() - before;
    currentPhase = null;
    item.container_after = stats();
    report.phases.push(item);
    process.stdout.write(
      JSON.stringify({
        phase: name,
        requests: item.requests,
        api_requests: item.api_requests,
      }) + "\n",
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
    const session = await page.evaluate(
      async (base) =>
        (
          await (
            await fetch(new URL("api/observatory/v1/session", base), {
              credentials: "same-origin",
              redirect: "error",
            })
          ).json()
        ).data,
      site.href,
    );
    assert.equal(session.operate, false);
    report.build = session.build;
    report.operate = session.operate;
    await page
      .locator("main .loading")
      .first()
      .waitFor({ state: "hidden", timeout: 30000 });
    await page.waitForTimeout(1000);
    const visible = await page.evaluate(() => ({
      state: document.visibilityState,
      hidden: document.hidden,
    }));
    assert.equal(visible.hidden, false);
    await phase("open", visible);
    const blank = await context.newPage();
    await blank.bringToFront();
    const background = await page.evaluate(() => ({
      state: document.visibilityState,
      hidden: document.hidden,
    }));
    report.hidden_attempt = {
      method: "Another real browser tab brought to front",
      observed: background,
    };
    if (background.hidden) await phase("hidden", background);
    else
      report.limitations.push(
        "Headless Chromium kept the original page visible after activating another tab; no hidden-window performance measurement is claimed.",
      );
    await blank.close();
    await page.close();
    await phase("closed", { state: "closed" });
    await Promise.allSettled(assetReads);
    report.asset_totals = Object.fromEntries(
      ["installed", "source"].map((kind) => [
        kind,
        Object.values(report.assets).reduce(
          (sum, item) => ({
            bytes: sum.bytes + (item[kind]?.bytes || 0),
            gzip_bytes: sum.gzip_bytes + (item[kind]?.gzip_bytes || 0),
          }),
          { bytes: 0, gzip_bytes: 0 },
        ),
      ]),
    );
    assert.deepEqual(report.mutations, []);
    assert.equal(
      report.phases.find((item) => item.name === "closed").requests,
      0,
    );
    report.status = "passed";
    fs.writeFileSync(
      path.join(output, "results.json"),
      JSON.stringify(report, null, 2),
    );
  } catch (error) {
    report.status = "failed";
    report.error = error.stack;
    fs.writeFileSync(
      path.join(output, "results.json"),
      JSON.stringify(report, null, 2),
    );
    throw error;
  } finally {
    await browser.close();
  }
})().catch((error) => {
  process.stderr.write(error.stack + "\n");
  process.exitCode = 1;
});
