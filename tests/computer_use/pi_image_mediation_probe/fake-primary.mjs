import { createServer } from "node:http";

const BODY_LIMIT = 64 * 1024;
const BODY_TIMEOUT_MS = 1_000;
const RESPONSE = "fixture primary response";

function readBody(request) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    let rejected = false;
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > BODY_LIMIT) {
        if (!rejected) reject(Object.assign(new Error("request body exceeds fixture limit"), { status: 413 }));
        rejected = true;
      } else {
        chunks.push(chunk);
      }
    });
    request.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    request.on("error", reject);
  });
}

/** Start a synthetic OpenAI-compatible streaming provider on loopback only. */
export async function startFakePrimary() {
  const captures = [];
  const captureWaiters = [];
  let requests = 0;
  const requestWaiters = [];
  const sockets = new Set();
  const record = (capture) => {
    captures.push(capture);
    while (captureWaiters.length && captures.length >= captureWaiters[0].count) captureWaiters.shift().resolve();
  };
  const server = createServer(async (request, response) => {
    requests += 1;
    while (requestWaiters.length && requests >= requestWaiters[0].count) requestWaiters.shift().resolve();
    if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
      response.writeHead(404).end();
      return;
    }
    try {
      request.setTimeout(BODY_TIMEOUT_MS, () => request.destroy());
      const body = await readBody(request);
      const parsed = JSON.parse(body);
      let closed;
      const capture = { body, parsed, cancelled: false, closed: new Promise((resolve) => { closed = resolve; }) };
      response.on("close", () => { capture.cancelled = !response.writableEnded; closed(); });
      record(capture);
      if (parsed.fixture_delay) return;
      response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
      const wantsTool = JSON.stringify(parsed.messages).includes("[fixture-tool]") && !parsed.messages.some((message) => message.role === "tool");
      if (wantsTool) {
        response.write(`data: ${JSON.stringify({ id: "fixture-primary", object: "chat.completion.chunk", choices: [{ index: 0, delta: { role: "assistant", tool_calls: [{ index: 0, id: "fixture-image-call", type: "function", function: { name: "fixture_image", arguments: "{}" } }] }, finish_reason: null }] })}\n\n`);
        response.write(`data: ${JSON.stringify({ id: "fixture-primary", object: "chat.completion.chunk", choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }] })}\n\n`);
        response.end("data: [DONE]\n\n");
        return;
      }
      response.write(`data: ${JSON.stringify({ id: "fixture-primary", object: "chat.completion.chunk", choices: [{ index: 0, delta: { role: "assistant", content: RESPONSE }, finish_reason: null }] })}\n\n`);
      response.write(`data: ${JSON.stringify({ id: "fixture-primary", object: "chat.completion.chunk", choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })}\n\n`);
      response.end("data: [DONE]\n\n");
    } catch (error) {
      response.writeHead(error.status || 400, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: { message: error.message } }));
    }
  });
  server.on("connection", (socket) => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
  });
  await new Promise((resolve, reject) => server.once("error", reject).listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return {
    baseUrl: `http://127.0.0.1:${port}/v1`, captures, response: RESPONSE,
    waitForCaptures: (count) => captures.length >= count ? Promise.resolve() : new Promise((resolve) => captureWaiters.push({ count, resolve })),
    waitForRequests: (count) => requests >= count ? Promise.resolve() : new Promise((resolve) => requestWaiters.push({ count, resolve })),
    close: async () => {
      const closed = new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
      for (const socket of sockets) socket.destroy();
      await closed;
    },
  };
}
