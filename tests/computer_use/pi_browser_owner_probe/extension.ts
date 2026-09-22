import { Type } from "typebox";
import { createFixtureObservationAdapter } from "../../../browser_owner/observation_adapter.mjs";

let adapter: any;
const text = (value: unknown) => ({ content: [{ type: "text", text: JSON.stringify(value) }] });
const mustAdapter = () => { if (!adapter) throw new Error("owner_closed"); return adapter; };

export default function (pi: any) {
  pi.registerTool({ name: "browser_capture", label: "Capture fixture", description: "Capture the fixed read-only synthetic fixture", parameters: Type.Object({}), async execute() { return text(await mustAdapter().execute({ operation: "capture" })); } });
  pi.registerTool({ name: "browser_resolve", label: "Resolve fixture entity", description: "Resolve a fresh fixture entity", parameters: Type.Object({ observation_id: Type.String(), entity_id: Type.String() }), async execute(args: any) { return text(await mustAdapter().execute({ operation: "resolve", args })); } });
  pi.registerTool({ name: "browser_release", label: "Release fixture observation", description: "Release a fixture observation", parameters: Type.Object({ observation_id: Type.String() }), async execute(args: any) { return text(await mustAdapter().execute({ operation: "release", args })); } });
  pi.on("session_start", async (_event: any, ctx: any) => {
    if (adapter) throw new Error("owner_already_open");
    const header = ctx.sessionManager.getHeader();
    if (!header?.id) throw new Error("missing_session_header");
    adapter = await createFixtureObservationAdapter({ piSessionId: header.id });
    pi.setActiveTools(["browser_capture", "browser_resolve", "browser_release"]);
    console.error(`PI_BROWSER_OWNER_READY:${JSON.stringify({ session_id: header.id, active: pi.getActiveTools().sort() })}`);
  });
  pi.on("session_shutdown", async () => { const current = adapter; adapter = undefined; await current?.close(); console.error("PI_BROWSER_OWNER_CLOSED"); });
}
