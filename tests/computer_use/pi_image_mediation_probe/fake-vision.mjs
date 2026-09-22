import { createServer } from "node:http";

const BODY_LIMIT = 64 * 1024;
const BODY_TIMEOUT_MS = 1_000;

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
      } else chunks.push(chunk);
    });
    request.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    request.on("error", reject);
  });
}

function associations(messages = []) {
  return messages.flatMap((message) => {
    if (!Array.isArray(message.content)) return [];
    const question = message.content.filter((part) => part.type === "text").map((part) => part.text).join("\n");
    return message.content.filter((part) => part.type === "image_url").map((part) => ({
      question, image: part.image_url?.url || null,
    }));
  });
}

/** Start a synthetic vision provider that retains only synthetic request captures. */
export async function startFakeVision() {
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
      record({ body, parsed, associations: associations(parsed.messages) });
      if (parsed.fixture_mode === "hang") return;
      let inspection = structuredClone(parsed.fixture_inspection);
      if (parsed.fixture_mode === "extra_field") inspection.extra = true;
      if (parsed.fixture_mode === "malformed_fact") inspection.facts = [{ ...inspection.facts[0], region_ref: "forbidden" }];
      if (parsed.fixture_mode === "truncated") inspection.truncated = true;
      if (parsed.fixture_mode === "inconclusive") inspection = { ...inspection, status: "inconclusive", facts: [], reason: "fixture could not determine the result" };
      if (parsed.fixture_mode === "unsupported") inspection = { ...inspection, status: "unsupported", facts: [], reason: "fixture profile does not support this inspection" };
      if (parsed.fixture_mode === "media") inspection.facts = [{ ...inspection.facts[0], text: "data:image/png;base64,forbidden" }];
      if (parsed.fixture_mode === "reason_media") inspection = { ...inspection, status: "inconclusive", facts: [], reason: "data:image/png;base64,forbidden" };
      if (parsed.fixture_mode === "body_hang") { response.writeHead(200, { "content-type": "application/json" }); response.write("{"); return; }
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify(inspection));
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
    baseUrl: `http://127.0.0.1:${port}/v1`, captures,
    waitForCaptures: (count) => captures.length >= count ? Promise.resolve() : new Promise((resolve) => captureWaiters.push({ count, resolve })),
    waitForRequests: (count) => requests >= count ? Promise.resolve() : new Promise((resolve) => requestWaiters.push({ count, resolve })),
    close: async () => {
      const closed = new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
      for (const socket of sockets) socket.destroy();
      await closed;
    },
  };
}
