import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import { chromium } from "playwright";

const require = createRequire(import.meta.url);
const packageVersion = require("playwright/package.json").version;
const here = dirname(fileURLToPath(import.meta.url));
const fixture = join(here, "fixture.html");
const expectedPlaywrightVersion = "1.63.0";

function browserExecutable() {
  const executable = process.env.CHROMIUM_EXECUTABLE || "/usr/bin/google-chrome";
  if (!existsSync(executable)) throw new Error("missing_browser");
  return executable;
}

function pngDimensions(image) {
  assert.deepEqual([...image.subarray(1, 4)], [80, 78, 71]);
  return { width: image.readUInt32BE(16), height: image.readUInt32BE(20) };
}

class ReadOnlyBrowserOwner {
  constructor(page, cdp, fixtureUrl) {
    this.page = page;
    this.cdp = cdp;
    this.fixtureUrl = fixtureUrl;
    this.tail = Promise.resolve();
    this.inFlight = 0;
    this.maxInFlight = 0;
    this.sequence = [];
    this.nextObservation = 0;
    this.observations = new Map();
  }

  async navigate(url) {
    if (url !== this.fixtureUrl) throw new Error("navigation_not_permitted");
    await this.page.goto(url, { waitUntil: "load" });
    if (this.page.url() !== this.fixtureUrl) throw new Error("navigation_not_permitted");
  }

  capture(label, beforeScreenshot) {
    const queued = this.tail.then(() => this.#capture(label, beforeScreenshot));
    this.tail = queued.catch(() => {});
    return queued;
  }

  async #capture(label, beforeScreenshot) {
    this.inFlight += 1;
    this.maxInFlight = Math.max(this.maxInFlight, this.inFlight);
    this.sequence.push(label);
    try {
      const start = await this.page.evaluate(() => ({
        epoch: window.__fixtureEpoch,
        url: location.href,
      }));
      if (start.url !== this.fixtureUrl) throw new Error("navigation_not_permitted");
      const entities = await this.page.locator("#owner-scope [data-owner-entity]").evaluateAll((nodes) =>
        nodes.map((node, index) => {
          const tag = node.tagName.toLowerCase();
          const disabled = node.matches(":disabled") || node.getAttribute("aria-disabled") === "true";
          const interactive = !disabled && ["button", "input", "select", "textarea", "a"].includes(tag);
          return {
            id: `entity-${index + 1}`,
            domId: node.id,
            role: node.getAttribute("role") || (tag === "button" ? "button" : tag),
            label: node.getAttribute("aria-label") || node.textContent.trim(),
            state: disabled ? "disabled" : interactive ? "enabled" : "non_interactive",
            interactive,
          };
        }),
      );
      const bindings = new Map();
      for (const entity of entities) {
        const handle = await this.page.locator(`#${entity.domId}`).elementHandle();
        if (!handle) throw new Error("missing_dom_binding");
        bindings.set(entity.id, handle);
      }
      if (beforeScreenshot) await beforeScreenshot(this.page);
      const accessibility = await this.cdp.send("Accessibility.getFullAXTree");
      const image = await this.page.locator("#owner-scope").screenshot({ type: "png" });
      const end = await this.page.evaluate(() => window.__fixtureEpoch);
      // ponytail: page-wide epoch; track relevant subtrees if unrelated rerenders cause excess reacquisition.
      if (start.epoch !== end) throw new Error("incoherent_capture");
      const observationId = `observation-${++this.nextObservation}`;
      const receipt = {
        observationId,
        owner: "synthetic-read-only-browser-owner",
        package: { name: "playwright", version: packageVersion, transport: "Playwright Chromium CDP over a local process" },
        navigation: { permitted: true, source: "synthetic file fixture" },
        scope: {
          kind: "subtree",
          root: "synthetic-fixture-main",
          filter: "marked_entities",
          coverage: { complete: true, omittedCount: 0, searchedRegions: ["synthetic-fixture-main"], unsupportedRegions: [] },
        },
        entities,
        inventory: {
          disabledCount: entities.filter((entity) => entity.state === "disabled").length,
          nonInteractiveCount: entities.filter((entity) => entity.state === "non_interactive").length,
        },
        accessibility: { source: "Accessibility.getFullAXTree", nodeCount: accessibility.nodes.length },
        screenshot: {
          format: "png",
          byteLength: image.length,
          sha256: createHash("sha256").update(image).digest("hex"),
          dimensions: pngDimensions(image),
        },
        coherence: { navigationEpoch: 1, domEpoch: start.epoch, checkedEpoch: end, coherent: true },
      };
      this.observations.set(observationId, { bindings, epoch: end, receipt });
      return receipt;
    } finally {
      this.inFlight -= 1;
    }
  }

  async resolve(observationId, entityId) {
    const observation = this.observations.get(observationId);
    const binding = observation?.bindings.get(entityId);
    if (!binding) throw new Error("unknown_binding");
    const epoch = await this.page.evaluate(() => window.__fixtureEpoch);
    if (epoch !== observation.epoch || !(await binding.evaluate((node) => node.isConnected))) {
      throw new Error("stale_binding");
    }
    return observation.receipt.entities.find((entity) => entity.id === entityId);
  }
}

function assertNonzeroFailure(argument, environment) {
  const result = spawnSync(process.execPath, [fileURLToPath(import.meta.url), argument], {
    env: { ...process.env, ...environment },
    encoding: "utf8",
  });
  assert.notEqual(result.status, 0, `${argument} must fail`);
}

async function main() {
  if (process.argv.includes("--forced-failure")) throw new Error("forced_failure");
  if (process.argv.includes("--missing-browser")) {
    browserExecutable();
    throw new Error("missing_browser_check_did_not_fail");
  }
  assert.equal(packageVersion, expectedPlaywrightVersion, "pinned Playwright version changed");
  assertNonzeroFailure("--forced-failure");
  assertNonzeroFailure("--missing-browser", { CHROMIUM_EXECUTABLE: join(here, "missing-browser") });

  const browser = await chromium.launch({ executablePath: browserExecutable(), headless: true, args: ["--disable-gpu"] });
  try {
    const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
    const cdp = await page.context().newCDPSession(page);
    const fixtureUrl = pathToFileURL(fixture).href;
    const owner = new ReadOnlyBrowserOwner(page, cdp, fixtureUrl);
    await assert.rejects(owner.navigate(pathToFileURL(join(here, "other-fixture.html")).href), /navigation_not_permitted/);
    assert.equal(page.url(), "about:blank");
    await owner.navigate(fixtureUrl);
    const first = await owner.capture("initial");
    assert.equal(first.navigation.permitted, true);
    assert.equal(first.scope.kind, "subtree");
    assert.equal(first.scope.filter, "marked_entities");
    assert.equal(first.scope.coverage.complete, true);
    assert.equal(first.entities.length, 3);
    assert.equal(first.inventory.disabledCount, 1);
    assert.equal(first.inventory.nonInteractiveCount, 1);
    assert.ok(first.accessibility.nodeCount > 0);
    assert.ok(first.screenshot.byteLength > 0);
    assert.ok(first.screenshot.dimensions.width > 0 && first.screenshot.dimensions.height > 0);
    assert.equal(await owner.resolve(first.observationId, "entity-1").then((entity) => entity.label), "Capture report");

    await assert.rejects(
      owner.capture("incoherent", (ownerPage) => ownerPage.evaluate(() => {
        document.querySelector("#owner-scope").append(document.createElement("span"));
      })),
      /incoherent_capture/,
    );
    await Promise.all([owner.capture("serialized-a"), owner.capture("serialized-b")]);
    assert.equal(owner.maxInFlight, 1, "owner must serialize captures");
    assert.deepEqual(owner.sequence, ["initial", "incoherent", "serialized-a", "serialized-b"]);

    await page.evaluate(() => {
      const oldNode = document.querySelector("#capture");
      const replacement = document.createElement("button");
      replacement.id = "capture";
      replacement.setAttribute("data-owner-entity", "");
      replacement.textContent = "Replacement";
      oldNode.replaceWith(replacement);
    });
    await page.waitForFunction((epoch) => window.__fixtureEpoch > epoch, first.coherence.domEpoch);
    await assert.rejects(owner.resolve(first.observationId, "entity-1"), /stale_binding/);

    console.log(JSON.stringify({
      status: "passed",
      selected: first.package,
      browserVersion: browser.version(),
      receipt: first,
      serializedAdmission: { maximumInFlight: owner.maxInFlight, sequence: owner.sequence },
      staleBinding: "rejected",
      limitations: ["No source-owned production adapter transport, authorization, or observation persistence seam is proven by this fixture-only owner."],
    }));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
