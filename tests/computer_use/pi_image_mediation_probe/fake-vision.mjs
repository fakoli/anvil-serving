import { createServer } from "node:http";

const BODY_LIMIT = 64 * 1024;
const RESPONSE = "fixture vision response";

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
  const server = createServer(async (request, response) => {
    if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
      response.writeHead(404).end();
      return;
    }
    try {
      const body = await readBody(request);
      const parsed = JSON.parse(body);
      captures.push({ body, parsed, associations: associations(parsed.messages) });
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ choices: [{ message: { role: "assistant", content: RESPONSE } }] }));
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
