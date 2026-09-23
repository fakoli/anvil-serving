import { Type } from "typebox";
import { createFixtureObservationAdapter } from "../../../browser_owner/observation_adapter.mjs";

let adapter: any;
const rawMedia = (value: any): boolean => Array.isArray(value) ? value.some(rawMedia) : !value || typeof value !== "object" ? typeof value === "string" && /^data:image\//i.test(value) : value.type === "image" || value.type === "image_url" || typeof value.url === "string" && value.url.startsWith("data:image/") || Object.values(value).some(rawMedia);
const text = (value: unknown) => ({ content: [{ type: "text", text: JSON.stringify(value) }] });
const mustAdapter = () => { if (!adapter) throw new Error("owner_closed"); return adapter; };
const parse = (result: any) => JSON.parse(result.content[0].text);

export default function (pi: any) {
  pi.registerProvider("fixture-browser", { name: "Fixture browser", baseUrl: process.env.PI_BROWSER_FIXTURE_PROVIDER_URL, apiKey: "fixture", api: "openai-completions", models: [{ id: "fixture-browser", name: "Fixture browser", reasoning: false, input: process.env.PI_BROWSER_IMAGE_CAPABLE === "1" ? ["text", "image"] : ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 4096, maxTokens: 32 }] });
  const capture = async (_toolCallId: string, _params: any, signal: AbortSignal, _onUpdate: any, _ctx: any) => text(await mustAdapter().execute({ operation: "capture" }, { signal }));
  const resolve = async (_toolCallId: string, params: any, _signal: AbortSignal, _onUpdate: any, _ctx: any) => text(await mustAdapter().execute({ operation: "resolve", args: params }));
  const release = async (_toolCallId: string, params: any, _signal: AbortSignal, _onUpdate: any, _ctx: any) => text(await mustAdapter().execute({ operation: "release", args: params }));
  pi.registerTool({ name: "browser_capture", label: "Capture fixture", description: "Capture the fixed read-only synthetic fixture", parameters: Type.Object({}), execute: capture });
  pi.registerTool({ name: "browser_resolve", label: "Resolve fixture entity", description: "Resolve a fresh fixture entity", parameters: Type.Object({ observation_id: Type.String(), entity_id: Type.String() }), execute: resolve });
  pi.registerTool({ name: "browser_release", label: "Release fixture observation", description: "Release a fixture observation", parameters: Type.Object({ observation_id: Type.String() }), execute: release });
  pi.registerCommand("browser_fixture_proof", { description: "Run the synthetic browser callback proof", async handler(args: string, ctx: any) {
    const live = args.trim() === "live", oldId = live ? "" : args.trim();
    const prior = /^[0-9a-f-]{36}$/.test(oldId) ? parse(await resolve("fixture-prior", { observation_id: oldId, entity_id: "e-1" }, new AbortController().signal, () => {}, ctx)) : null;
    const captureResult = parse(await capture("fixture-capture", {}, new AbortController().signal, () => {}, ctx));
    const disabled = captureResult.result.entities.find((entity: any) => entity.text === "Disabled capture");
    const resolved = parse(await resolve("fixture-resolve", { observation_id: captureResult.result.observation_id, entity_id: disabled.id }, new AbortController().signal, () => {}, ctx));
    const released = live ? null : parse(await release("fixture-release", { observation_id: captureResult.result.observation_id }, new AbortController().signal, () => {}, ctx));
    const stale = live ? null : parse(await resolve("fixture-stale", { observation_id: captureResult.result.observation_id, entity_id: disabled.id }, new AbortController().signal, () => {}, ctx));
    const cancelledController = new AbortController(); cancelledController.abort();
    const cancelled = parse(await capture("fixture-cancelled", {}, cancelledController.signal, () => {}, ctx));
    const widened = parse(await resolve("fixture-widened", { observation_id: captureResult.result.observation_id, entity_id: disabled.id, url: "https://example.test/" }, new AbortController().signal, () => {}, ctx));
    console.error(`PI_BROWSER_OWNER_CALLBACKS:${JSON.stringify({ prior: prior?.code ?? null, live, capture: { status: captureResult.status, observation_id: captureResult.result.observation_id, binding: captureResult.binding, entities: captureResult.result.entities.map((entity: any) => ({ role: entity.role, text: entity.text, enabled: entity.enabled })), hasImage: JSON.stringify(captureResult).includes("iVBOR") }, resolve: { status: resolved.status, enabled: resolved.result.enabled }, release: released?.status ?? null, stale: stale?.code ?? null, cancelled: cancelled.code, widened: widened.code })}`);
  } });
  pi.registerCommand("browser_fixture_reload", { description: "Reload the synthetic fixture extension", async handler(_args: string, ctx: any) { await ctx.reload(); } });
  pi.on("context", () => { console.error("PI_BROWSER_OWNER_CONTEXT"); });
  pi.on("before_provider_request", (event: any) => { if (rawMedia(event.payload)) throw new Error("raw_media_guard"); console.error("PI_BROWSER_OWNER_BEFORE_PROVIDER"); return event.payload; });
  pi.on("session_start", async (event: any, ctx: any) => {
    if (adapter) throw new Error("owner_already_open");
    const header = ctx.sessionManager.getHeader();
    if (!header?.id) throw new Error("missing_session_header");
    adapter = await createFixtureObservationAdapter({ piSessionId: header.id });
    pi.setActiveTools(["browser_capture", "browser_resolve", "browser_release"]);
    console.error(`PI_BROWSER_OWNER_READY:${JSON.stringify({ reason: event.reason, session_id: header.id, active: pi.getActiveTools().sort() })}`);
  });
  pi.on("session_shutdown", async (event: any) => { const current = adapter; adapter = undefined; await current?.close(); console.error(`PI_BROWSER_OWNER_CLOSED:${JSON.stringify({ reason: event.reason })}`); });
}
