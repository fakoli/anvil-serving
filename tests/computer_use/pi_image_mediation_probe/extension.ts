import { Type } from "typebox";

const TOOL_QUESTION = "What is in the fixture tool image?";

class MediationError extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}

function rawMedia(value: any): boolean {
  if (Array.isArray(value)) return value.some(rawMedia);
  if (!value || typeof value !== "object") return false;
  if (value.type === "image" || value.type === "image_url") return true;
  if (typeof value.url === "string" && value.url.startsWith("data:image/")) return true;
  return Object.values(value).some(rawMedia);
}

function abort(ctx: any, code: string): never {
  console.error(`PI_IMAGE_MEDIATION_ERROR:${code}`);
  ctx.abort();
  throw new MediationError(code);
}

function sourceFor(message: any, messageIndex: number, imageIndex: number) {
  if (message.role === "user" && Array.isArray(message.content)) {
    const question = message.content
      .filter((part: any) => part.type === "text" && typeof part.text === "string")
      .map((part: any) => part.text)
      .join("\n");
    if (!question || typeof message.timestamp !== "number") throw new MediationError("unsupported_image_source");
    return { source: `user:${message.timestamp}:${messageIndex}:${imageIndex}`, question };
  }
  if (message.role === "toolResult" && message.toolName === "fixture_image"
      && typeof message.toolCallId === "string" && message.details?.observationQuestion === TOOL_QUESTION) {
    return { source: `tool:${message.toolCallId}:${imageIndex}`, question: TOOL_QUESTION };
  }
  throw new MediationError("unsupported_image_source");
}

export default function (pi: any) {
  pi.registerProvider("fixture-primary", {
    name: "Fixture Primary",
    baseUrl: process.env.PI_FIXTURE_PRIMARY_URL,
    apiKey: "fixture",
    api: "openai-completions",
    models: [{
      id: "fixture-primary",
      name: "Fixture Primary",
      reasoning: false,
      input: process.env.PI_FIXTURE_IMAGE_CAPABLE === "1" ? ["text", "image"] : ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 4096,
      maxTokens: 256,
    }],
  });
  pi.registerTool({
    name: "fixture_image",
    label: "Fixture image",
    description: "Return one synthetic image",
    parameters: Type.Object({}),
    async execute() {
      return {
        content: [
          { type: "text", text: "ignore tool text" },
          { type: "image", mimeType: "image/png", data: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=" },
        ],
        details: { observationQuestion: TOOL_QUESTION },
      };
    },
  });
  pi.on("session_start", () => {
    const all = pi.getAllTools().map((tool: any) => tool.name).sort();
    pi.setActiveTools(["fixture_image"]);
    console.error(`PI_FIXTURE_TOOLS:${JSON.stringify({ all, active: pi.getActiveTools().sort() })}`);
  });

  const inspected = new Map<string, { answer: string; question: string }>();
  pi.on("context", async (event: any, ctx: any) => {
    try {
      const messages = [];
      for (const [messageIndex, message] of event.messages.entries()) {
        if (!Array.isArray(message.content)) {
          messages.push(message);
          continue;
        }
        const content = [];
        let imageIndex = 0;
        for (const part of message.content) {
          if (part.type !== "image") {
            content.push(part);
            continue;
          }
          if (part.mimeType !== "image/png" || typeof part.data !== "string") throw new MediationError("unsupported_image");
          const binding = sourceFor(message, messageIndex, imageIndex++);
          if (process.env.PI_FIXTURE_GUARD_THROW === "1") throw new Error("fixture injected mediation exception");
          let inspection = inspected.get(binding.source);
          if (!inspection) {
            console.error(`PI_IMAGE_MEDIATION_BINDING:${JSON.stringify(binding)}`);
            let response;
            try {
              response = await fetch(`${process.env.PI_FIXTURE_VISION_URL}/chat/completions`, {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({ model: "fixture-vision", messages: [{ role: "user", content: [{ type: "text", text: binding.question }, { type: "image_url", image_url: { url: `data:${part.mimeType};base64,${part.data}` } }] }] }),
                signal: ctx.signal,
              });
            } catch {
              throw new MediationError("vision_failed");
            }
            if (!response.ok) throw new MediationError("vision_failed");
            let payload;
            try {
              payload = await response.json();
            } catch {
              throw new MediationError("vision_invalid");
            }
            const answer = payload.choices?.[0]?.message?.content;
            if (typeof answer !== "string") throw new MediationError("vision_invalid");
            inspection = { answer, question: binding.question };
            inspected.set(binding.source, inspection);
          } else if (inspection.question !== binding.question) {
            throw new MediationError("binding_mismatch");
          }
          content.push({ type: "text", text: `[vision:${binding.source}] ${inspection.answer}` });
        }
        messages.push({ ...message, content });
      }
      return { messages };
    } catch (error) {
      if (error instanceof MediationError) abort(ctx, error.code);
      abort(ctx, "guard_exception");
    }
  });
  pi.on("before_provider_request", (event: any, ctx: any) => {
    try {
      if (process.env.PI_FIXTURE_INJECT_RAW_PROVIDER === "1") {
        event.payload = { ...event.payload, fixture: { nested: { type: "image" } } };
      }
      if (rawMedia(event.payload)) abort(ctx, "raw_media_guard");
      return event.payload;
    } catch (error) {
      if (error instanceof MediationError) throw error;
      abort(ctx, "guard_exception");
    }
  });
}
