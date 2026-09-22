import { Type } from "typebox";

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
      return { content: [{ type: "image", mimeType: "image/png", data: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=" }], details: {} };
    },
  });
  pi.on("session_start", () => {
    const all = pi.getAllTools().map((tool: any) => tool.name).sort();
    pi.setActiveTools(["fixture_image"]);
    console.error(`PI_FIXTURE_TOOLS:${JSON.stringify({ all, active: pi.getActiveTools().sort() })}`);
  });
  const inspected = new Map<string, string>();
  const fail = (ctx: any, code: string) => {
    console.error(`PI_IMAGE_MEDIATION_ERROR:${code}`);
    ctx.abort();
    throw new Error(code);
  };
  pi.on("context", async (event: any, ctx: any) => {
    if (process.env.PI_FIXTURE_GUARD_THROW === "1") fail(ctx, "guard_exception");
    const messages = [];
    let lastQuestion = "";
    for (const message of event.messages) {
      if (!Array.isArray(message.content)) {
        messages.push(message);
        continue;
      }
      const ownQuestion = message.content.filter((part: any) => part.type === "text").map((part: any) => part.text).join("\n");
      const question = ownQuestion || lastQuestion;
      if (ownQuestion) lastQuestion = ownQuestion;
      const content = [];
      for (const part of message.content) {
        if (part.type !== "image") {
          content.push(part);
          continue;
        }
        if (part.mimeType !== "image/png" || typeof part.data !== "string") fail(ctx, "unsupported_image");
        const key = `${part.mimeType}:${part.data}:${question}`;
        let answer = inspected.get(key);
        if (!answer) {
          const response = await fetch(`${process.env.PI_FIXTURE_VISION_URL}/chat/completions`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ model: "fixture-vision", messages: [{ role: "user", content: [{ type: "text", text: question }, { type: "image_url", image_url: { url: `data:${part.mimeType};base64,${part.data}` } }] }] }),
          });
          if (!response.ok) fail(ctx, "vision_failed");
          answer = (await response.json()).choices?.[0]?.message?.content;
          if (typeof answer !== "string") fail(ctx, "vision_invalid");
          inspected.set(key, answer);
        }
        content.push({ type: "text", text: `[vision] ${answer}` });
      }
      messages.push({ ...message, content });
    }
    return { messages };
  });
}
