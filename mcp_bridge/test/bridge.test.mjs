import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { once } from "node:events";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  Client as ModernClient,
} from "@modelcontextprotocol/client";
import {
  StdioClientTransport as ModernStdioClientTransport,
} from "@modelcontextprotocol/client/stdio";
import {
  Client as LegacyClient,
} from "mcp-sdk-v1/client/index.js";
import {
  StdioClientTransport as LegacyStdioClientTransport,
} from "mcp-sdk-v1/client/stdio.js";

const BRIDGE = fileURLToPath(
  new URL("../../anvil_serving/_node/mcp_proxy.mjs", import.meta.url),
);
const TOKEN = "test-controller-token";
const VERSION = "0.17.0";
const SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo";
const PROTOCOL_KEY = "io.modelcontextprotocol/protocolVersion";
const MAX_NATIVE_IMAGE_BASE64_BYTES = 8 * 1024 * 1024;

function controllerResult(id, result) {
  return {
    jsonrpc: "2.0",
    id,
    result: {
      resultType: "complete",
      ...result,
      _meta: {
        [SERVER_INFO_KEY]: {
          name: "anvil-serving",
          version: VERSION,
        },
      },
    },
  };
}

async function startController() {
  const requests = [];
  const toolCalls = [];
  const server = createServer(async (request, response) => {
    if (request.method === "GET") {
      response.writeHead(405, { Allow: "POST" });
      response.end();
      return;
    }
    assert.equal(request.method, "POST");
    assert.equal(request.headers.authorization, `Bearer ${TOKEN}`);
    let raw = "";
    for await (const chunk of request) {
      raw += chunk;
    }
    const body = JSON.parse(raw);
    requests.push({ body, headers: request.headers });
    assert.equal(body.params._meta[PROTOCOL_KEY], "2026-07-28");
    assert.equal(request.headers["mcp-protocol-version"], "2026-07-28");
    assert.equal(request.headers["mcp-method"], body.method);

    let payload;
    if (body.method === "server/discover") {
      payload = controllerResult(body.id, {
        supportedVersions: ["2026-07-28"],
        capabilities: { tools: {} },
        instructions: "Test controller.",
        ttlMs: 30_000,
        cacheScope: "private",
      });
    } else if (body.method === "tools/list") {
      payload = controllerResult(body.id, {
        tools: [
          {
            name: "echo",
            description: "Echo a bounded string.",
            inputSchema: {
              type: "object",
              additionalProperties: false,
              properties: {
                value: { type: "string", maxLength: 32 },
              },
              required: ["value"],
              maxProperties: 1,
            },
          },
          {
            name: "media_workflow_run",
            description: "Submit one named media workflow.",
            inputSchema: {
              type: "object",
              additionalProperties: false,
              properties: {
                workflow_id: { type: "string", maxLength: 128 },
                version: { type: "string", maxLength: 64 },
                parameters: { type: "object", maxProperties: 32 },
                idempotency_key: { type: "string", maxLength: 128 },
              },
              required: [
                "workflow_id",
                "version",
                "parameters",
                "idempotency_key",
              ],
              maxProperties: 4,
            },
            _meta: { "anvil/requiredScope": "media:submit" },
          },
        ],
        ttlMs: 30_000,
        cacheScope: "private",
      });
    } else if (body.method === "tools/call") {
      assert.equal(request.headers["mcp-name"], body.params.name);
      toolCalls.push(body.params);
      if (body.params.arguments.value === "force-error") {
        payload = {
          jsonrpc: "2.0",
          id: body.id,
          error: {
            code: -32603,
            message: `upstream rejected bearer ${TOKEN}`,
          },
        };
      } else if (body.params.arguments.value === "large-image") {
        payload = controllerResult(body.id, {
          content: [
            {
              type: "image",
              data: "A".repeat(MAX_NATIVE_IMAGE_BASE64_BYTES),
              mimeType: "image/png",
            },
          ],
          structuredContent: {
            byteLength: (MAX_NATIVE_IMAGE_BASE64_BYTES / 4) * 3,
          },
          isError: false,
        });
      } else if (body.params.name === "media_workflow_run") {
        payload = controllerResult(body.id, {
          content: [
            {
              type: "text",
              text: JSON.stringify({
                job: { id: "job_opaque_identifier", state: "queued" },
              }),
            },
          ],
          structuredContent: {
            job: { id: "job_opaque_identifier", state: "queued" },
            created: true,
          },
          isError: false,
        });
      } else {
        payload = controllerResult(body.id, {
          content: [
            {
              type: "text",
              text: body.params.arguments.value,
            },
          ],
          structuredContent: {
            value: body.params.arguments.value,
          },
          isError: false,
        });
      }
    } else {
      payload = {
        jsonrpc: "2.0",
        id: body.id,
        error: { code: -32601, message: "method not found" },
      };
    }
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify(payload));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  assert.equal(typeof address, "object");
  return {
    url: `http://127.0.0.1:${address.port}/mcp`,
    requests,
    toolCalls,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

function bridgeTransport(Transport, controllerUrl) {
  return new Transport({
    command: process.execPath,
    args: [
      BRIDGE,
      "--controller-url",
      controllerUrl,
      "--auth-env",
      "ANVIL_CONTROLLER_TOKEN",
      "--server-version",
      VERSION,
    ],
    env: {
      ...process.env,
      ANVIL_CONTROLLER_TOKEN: TOKEN,
    },
  });
}

test("legacy SDK 1.29 and modern SDK 2.0 use the same bridge", async () => {
  const controller = await startController();
  try {
    const legacy = new LegacyClient({
      name: "openclaw-compatible-test",
      version: "1.0.0",
    });
    await legacy.connect(
      bridgeTransport(LegacyStdioClientTransport, controller.url),
    );
    assert.equal(legacy.getServerVersion().name, "anvil-serving");
    assert.deepEqual(
      (await legacy.listTools()).tools.map((tool) => tool.name),
      ["echo", "media_workflow_run"],
    );
    const legacyResult = await legacy.callTool({
      name: "echo",
      arguments: { value: "legacy" },
    });
    assert.equal(legacyResult.isError, false);
    assert.equal(legacyResult.structuredContent.value, "legacy");
    const legacyMedia = await legacy.callTool({
      name: "media_workflow_run",
      arguments: {
        workflow_id: "image.test",
        version: "v1",
        parameters: { prompt: "mountain" },
        idempotency_key: "legacy-media",
      },
    });
    assert.equal(legacyMedia.isError, false);
    assert.equal(legacyMedia.structuredContent.job.state, "queued");
    assert.equal(
      (await legacy.listTools()).tools.find(
        (tool) => tool.name === "media_workflow_run",
      )._meta["anvil/requiredScope"],
      "media:submit",
    );

    const callsBeforeInvalid = controller.toolCalls.length;
    const invalidResult = await legacy.callTool({
      name: "echo",
      arguments: { value: 42 },
    });
    assert.equal(invalidResult.isError, true);
    assert.equal(controller.toolCalls.length, callsBeforeInvalid);

    const redactedResult = await legacy.callTool({
      name: "echo",
      arguments: { value: "force-error" },
    });
    assert.equal(redactedResult.isError, true);
    assert.match(JSON.stringify(redactedResult), /<redacted>/);
    assert.doesNotMatch(JSON.stringify(redactedResult), new RegExp(TOKEN));
    await legacy.close();

    const modern = new ModernClient(
      {
        name: "modern-test",
        version: "1.0.0",
      },
      {
        supportedProtocolVersions: ["2026-07-28"],
        versionNegotiation: {
          mode: { pin: "2026-07-28" },
        },
      },
    );
    await modern.connect(
      bridgeTransport(ModernStdioClientTransport, controller.url),
    );
    assert.equal(modern.getProtocolEra(), "modern");
    assert.deepEqual(
      (await modern.listTools()).tools.map((tool) => tool.name),
      ["echo", "media_workflow_run"],
    );
    const modernResult = await modern.callTool({
      name: "echo",
      arguments: { value: "modern" },
    });
    assert.equal(modernResult.isError, false);
    assert.equal(modernResult.structuredContent.value, "modern");
    const modernMedia = await modern.callTool({
      name: "media_workflow_run",
      arguments: {
        workflow_id: "video.test",
        version: "v1",
        parameters: { prompt: "ocean" },
        idempotency_key: "modern-media",
      },
    });
    assert.equal(modernMedia.isError, false);
    assert.equal(modernMedia.structuredContent.job.id, "job_opaque_identifier");
    const modernImage = await modern.callTool({
      name: "echo",
      arguments: { value: "large-image" },
    });
    assert.equal(modernImage.isError, false);
    assert.equal(modernImage.content[0].type, "image");
    assert.equal(
      modernImage.content[0].data.length,
      MAX_NATIVE_IMAGE_BASE64_BYTES,
    );
    assert.equal(
      modernImage.structuredContent.byteLength,
      6 * 1024 * 1024,
    );
    await modern.close();

    assert.deepEqual(
      controller.toolCalls.map((call) => call.name),
      [
        "echo",
        "media_workflow_run",
        "echo",
        "echo",
        "media_workflow_run",
        "echo",
      ],
    );
    assert.ok(
      controller.requests.every(
        (entry) => entry.body.params._meta[PROTOCOL_KEY] === "2026-07-28",
      ),
    );
  } finally {
    await controller.close();
  }
});

test("the bridge fails closed when its token environment is absent", async () => {
  const child = spawn(
    process.execPath,
    [
      BRIDGE,
      "--controller-url",
      "http://127.0.0.1:8765/mcp",
      "--auth-env",
      "ANVIL_CONTROLLER_TOKEN",
      "--server-version",
      VERSION,
    ],
    {
      env: {
        PATH: process.env.PATH,
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let stderr = "";
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });
  const [code] = await once(child, "exit");
  assert.equal(code, 2);
  assert.match(stderr, /auth env var is unset or empty/);
  assert.doesNotMatch(stderr, new RegExp(TOKEN));
});
