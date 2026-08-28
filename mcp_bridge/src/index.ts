import {
  Client,
  StreamableHTTPClientTransport,
  type CallToolResult,
  type Tool,
} from "@modelcontextprotocol/client";
import {
  fromJsonSchema,
  McpServer,
  type JSONValue,
} from "@modelcontextprotocol/server";
import { serveStdio } from "@modelcontextprotocol/server/stdio";
import { parseArgs } from "node:util";
import { pathToFileURL } from "node:url";

const MODERN_PROTOCOL_VERSION = "2026-07-28";
const DEFAULT_REQUEST_TIMEOUT_MS = 120_000;
const MAX_REQUEST_TIMEOUT_MS = 1_860_000;
const MAX_ERROR_TEXT = 4_096;
const ENV_NAME = /^[A-Z][A-Z0-9_]*$/;

interface BridgeOptions {
  controllerUrl: URL;
  authEnv: string;
  token: string;
  serverVersion: string;
}

function usageError(message: string): never {
  throw new Error(message);
}

function parseOptions(argv: string[], env: NodeJS.ProcessEnv): BridgeOptions {
  const nodeMajor = Number.parseInt(
    process.versions.node.split(".", 1)[0] ?? "",
    10,
  );
  if (!Number.isFinite(nodeMajor) || nodeMajor < 20) {
    usageError("Node.js 20 or newer is required for remote MCP controller mode");
  }
  const parsed = parseArgs({
    args: argv,
    allowPositionals: false,
    strict: true,
    options: {
      "controller-url": { type: "string" },
      "auth-env": { type: "string" },
      "server-version": { type: "string" },
    },
  });
  const controllerValue = parsed.values["controller-url"];
  const authEnv = parsed.values["auth-env"];
  const serverVersion = parsed.values["server-version"];
  if (!controllerValue || !authEnv || !serverVersion) {
    usageError("--controller-url, --auth-env, and --server-version are required");
  }
  if (!ENV_NAME.test(authEnv)) {
    usageError("auth-env must name an ENV VAR matching ^[A-Z][A-Z0-9_]*$");
  }
  const token = (env[authEnv] ?? "").trim();
  if (!token) {
    usageError("auth env var is unset or empty");
  }

  let controllerUrl: URL;
  try {
    controllerUrl = new URL(controllerValue);
  } catch {
    usageError("controller-url must be an absolute http(s) URL");
  }
  if (!["http:", "https:"].includes(controllerUrl.protocol)) {
    usageError("controller-url must use http or https");
  }
  if (controllerUrl.username || controllerUrl.password) {
    usageError("controller-url must not contain credentials");
  }
  if (controllerUrl.search || controllerUrl.hash) {
    usageError("controller-url must not contain a query string or fragment");
  }
  if (controllerUrl.hostname.toLowerCase() === "localhost") {
    usageError("use 127.0.0.1 or a private/tailnet host, not localhost");
  }
  if (controllerUrl.pathname === "/" || controllerUrl.pathname === "") {
    controllerUrl.pathname = "/mcp";
  }
  return { controllerUrl, authEnv, token, serverVersion };
}

function requestTimeout(argumentsValue: unknown): number {
  if (
    typeof argumentsValue === "object" &&
    argumentsValue !== null &&
    "timeout_seconds" in argumentsValue
  ) {
    const seconds = (argumentsValue as Record<string, unknown>).timeout_seconds;
    if (typeof seconds === "number" && Number.isFinite(seconds) && seconds > 0) {
      return Math.min(
        MAX_REQUEST_TIMEOUT_MS,
        Math.max(DEFAULT_REQUEST_TIMEOUT_MS, (seconds + 30) * 1_000),
      );
    }
  }
  return DEFAULT_REQUEST_TIMEOUT_MS;
}

function safeErrorText(error: unknown, token: string): string {
  const raw = error instanceof Error ? error.message : String(error);
  return (token ? raw.replaceAll(token, "<redacted>") : raw).slice(
    0,
    MAX_ERROR_TEXT,
  );
}

function jsonObject(value: unknown): Record<string, JSONValue> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return {};
  }
  return value as Record<string, JSONValue>;
}

function toolConfig(tool: Tool) {
  return {
    ...(tool.title ? { title: tool.title } : {}),
    ...(tool.description ? { description: tool.description } : {}),
    inputSchema: fromJsonSchema<Record<string, JSONValue>>(
      tool.inputSchema as Record<string, unknown>,
    ),
    ...(tool.outputSchema
      ? {
          outputSchema: fromJsonSchema<Record<string, JSONValue>>(
            tool.outputSchema as Record<string, unknown>,
          ),
        }
      : {}),
    ...(tool.annotations ? { annotations: tool.annotations } : {}),
    ...(tool.icons ? { icons: tool.icons } : {}),
    ...(tool._meta ? { _meta: tool._meta } : {}),
  };
}

async function buildBridge(options: BridgeOptions): Promise<McpServer> {
  const remote = new Client(
    {
      name: "anvil-serving-mcp-bridge",
      version: options.serverVersion,
    },
    {
      capabilities: {},
      supportedProtocolVersions: [MODERN_PROTOCOL_VERSION],
      versionNegotiation: {
        mode: { pin: MODERN_PROTOCOL_VERSION },
      },
      cachePartition: "anvil-controller",
    },
  );
  const remoteTransport = new StreamableHTTPClientTransport(
    options.controllerUrl,
    {
      authProvider: {
        token: async () => options.token,
      },
      onInsufficientScope: "throw",
    },
  );
  await remote.connect(remoteTransport, {
    timeout: 30_000,
    maxTotalTimeout: 30_000,
  });

  const remoteIdentity = remote.getServerVersion();
  if (
    remoteIdentity?.name !== "anvil-serving" ||
    remoteIdentity.version !== options.serverVersion
  ) {
    await remote.close();
    usageError(
      `controller identity mismatch: expected anvil-serving ${options.serverVersion}`,
    );
  }

  const listed = await remote.listTools(undefined, {
    cacheMode: "refresh",
    timeout: 30_000,
    maxTotalTimeout: 30_000,
  });
  if (listed.tools.length === 0) {
    await remote.close();
    usageError("controller advertised no MCP tools");
  }

  const server = new McpServer(
    {
      name: "anvil-serving",
      version: options.serverVersion,
    },
    {
      capabilities: { tools: {} },
      instructions:
        "Operate Anvil Serving through explicit, bounded tools. " +
        "Media generation accepts named workflows only. " +
        "Mutating tools retain their dry-run, confirmation, and human gates.",
    },
  );
  for (const tool of listed.tools) {
    server.registerTool(
      tool.name,
      toolConfig(tool),
      async (argumentsValue): Promise<CallToolResult> => {
        try {
          return await remote.callTool(
            {
              name: tool.name,
              arguments: jsonObject(argumentsValue),
            },
            {
              timeout: requestTimeout(argumentsValue),
              maxTotalTimeout: requestTimeout(argumentsValue),
              toolDefinition: tool,
            },
          );
        } catch (error) {
          return {
            content: [
              {
                type: "text",
                text: safeErrorText(error, options.token),
              },
            ],
            isError: true,
          };
        }
      },
    );
  }
  server.server.onclose = () => {
    void remote.close();
  };
  return server;
}

export async function main(
  argv: string[] = process.argv.slice(2),
  env: NodeJS.ProcessEnv = process.env,
): Promise<void> {
  const options = parseOptions(argv, env);
  const handle = serveStdio(() => buildBridge(options), {
    legacy: "serve",
    onerror: (error) => {
      process.stderr.write(`${safeErrorText(error, options.token)}\n`);
    },
  });
  const shutdown = async () => {
    await handle.close();
  };
  process.once("SIGINT", () => {
    void shutdown();
  });
  process.once("SIGTERM", () => {
    void shutdown();
  });
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  main().catch((error) => {
    const tokenNameIndex = process.argv.indexOf("--auth-env");
    const tokenName =
      tokenNameIndex >= 0 ? process.argv[tokenNameIndex + 1] : undefined;
    const token = tokenName ? (process.env[tokenName] ?? "") : "";
    process.stderr.write(`${safeErrorText(error, token)}\n`);
    process.exitCode = 2;
  });
}
