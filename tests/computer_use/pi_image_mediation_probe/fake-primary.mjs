import { createServer } from "node:http";

const BODY_LIMIT = 64 * 1024;
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
  const server = createServer(async (request, response) => {
    if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
      response.writeHead(404).end();
      return;
    }
    try {
      const body = await readBody(request);
      const parsed = JSON.parse(body);
      const capture = { body, parsed, cancelled: false };
      captures.push(capture);
      response.on("close", () => { capture.cancelled = !response.writableEnded; });
      if (parsed.fixture_delay) return;
      response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
      response.write(`data: ${JSON.stringify({ choices: [{ delta: { content: RESPONSE } }] })}\n\n`);
      response.end("data: [DONE]\n\n");
    } catch (error) {
      response.writeHead(error.status || 400, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: { message: error.message } }));
    }
  });
  await new Promise((resolve, reject) => server.once("error", reject).listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return {
    baseUrl: `http://127.0.0.1:${port}/v1`, captures, response: RESPONSE,
    close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
  };
}
