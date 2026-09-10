import { badge, button, el, empty, heading, notice, route, section, table } from "./common.js";
import { request, query } from "./api.js";
import { actionButtons } from "./operations.js";
import { configurationView } from "./configuration.js";

export async function modelsView(ctx, resourceId) {
  const root = el("div", { class: "workbench-page stack", "data-story": "US-RECIPE-01" });
  if (resourceId) {
    root.append(button("← Models & recipes", () => { location.hash = route("models"); }, "quiet-button"), await configurationView(ctx, resourceId));
    return root;
  }
  root.append(heading("Models & recipes", "Inspect a deployment, adjust its recipe, and review changes with its resource owner."));
  const byId = new Map();
  for (const item of [...(ctx.fleet?.serves || []), ...(ctx.settings?.resources || []), ...(ctx.fleet?.control_resources || [])]) {
    byId.set(item.id, { ...byId.get(item.id), ...item });
  }
  const resources = [...byId.values()].filter(item => !ctx.host || item.host_id === ctx.host)
    .filter(item => ["recipe", "serve"].includes(item.kind) || (ctx.fleet?.serves || []).some(serve => serve.id === item.id));
  if (!resources.length) root.append(empty("No managed models or recipes are visible for this host."));
  const grid = el("div", { class: "model-grid" });
  for (const resource of resources) {
    const card = el("section", { class: "panel stack" }, section(resource.label || resource.display_name || resource.id, badge(resource.kind || "serve")),
      table(["Host", "Runtime", "Observed model"], [[resource.host_id, resource.engine || "Reported by owner", resource.observed_model || resource.model || "Reported by owner"]]),
      button("Inspect & edit recipe", () => { location.hash = route("models", resource.id); }, "secondary-button"));
    grid.append(card);
    ctx.jobs.push(async () => {
      try {
        const controls = await request(query("controls", { resource: resource.id }), { signal: ctx.signal });
        if (!ctx.signal.aborted) card.append(actionButtons(resource.id, controls, ctx, { exclude: ["configuration.apply"] }));
      } catch (error) { if (!ctx.signal.aborted) card.append(notice(error.message, "warning")); }
    });
  }
  root.append(grid);
  return root;
}
