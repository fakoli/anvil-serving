import {
  el,
  list,
  heading,
  field,
  select,
  button,
  notice,
  empty,
  show,
  badge,
  route,
  kv,
} from "./common.js";
import { request, query, getSession } from "./api.js";
import { previewAction, actionButtons } from "./operations.js";
const drafts = new Map();
export const hasDirtyDraft = () => [...drafts.values()].some((d) => d.dirty);
export const clearDrafts = () => drafts.clear();
window.addEventListener("beforeunload", (event) => {
  if (hasDirtyDraft()) {
    event.preventDefault();
    event.returnValue = "";
  }
});
function resources(ctx) {
  const declared = [
    ...list(ctx.settings?.resources),
    ...list(ctx.fleet.control_resources),
  ]
    .filter((r) => r.kind !== "experiment")
    .map((r) => ({
      ...r,
      category:
        r.category ||
        (["configuration", "gateway"].includes(r.kind)
          ? "gateway"
          : ["profile", "host"].includes(r.kind)
            ? "profile"
            : "serve"),
    }));
  const found = [
    ...list(ctx.fleet.serves).map((s) => ({
      id: s.id,
      host_id: s.host_id,
      label: s.display_name || s.id,
      category: "serve",
    })),
    ...declared,
  ];
  return [...new Map(found.map((r) => [r.id, r])).values()].filter(
    (r) => !ctx.host || r.host_id === ctx.host,
  );
}
export function settingInput(setting, value, onChange) {
  const constraints = setting.constraints || {};
  let input;
  if (list(constraints.choices).length)
    input = select(
      constraints.choices.map((choice) =>
        typeof choice === "object"
          ? [String(choice.value), choice.label || String(choice.value)]
          : [String(choice), String(choice)],
      ),
      String(value ?? ""),
      () => onChange(),
    );
  else if (setting.value_type === "boolean")
    input = select(
      [
        ["true", "Enabled"],
        ["false", "Disabled"],
      ],
      String(value ?? false),
      () => onChange(),
    );
  else
    input = el("input", {
      type: ["integer", "number", "float"].includes(setting.value_type)
        ? "number"
        : "text",
      value: show(value) === "Not reported" ? "" : String(value),
      min: constraints.minimum,
      max: constraints.maximum,
      step: constraints.step ?? (setting.value_type === "integer" ? 1 : "any"),
      onInput: () => onChange(),
      autocomplete: "off",
    });
  input.dataset.valueType = setting.value_type;
  return input;
}
export function inputValue(input) {
  if (input.dataset.valueType === "boolean") return input.value === "true";
  if (["number", "integer", "float"].includes(input.dataset.valueType))
    return input.value === "" ? null : Number(input.value);
  return input.value;
}
export async function configurationView(ctx, resourceId) {
  const catalog = resources(ctx);
  const selected =
    catalog.find((r) => r.id === resourceId) ||
    catalog.find((r) => drafts.get(r.id)?.dirty) ||
    catalog.find((r) => ["configuration", "recipe"].includes(r.kind)) ||
    catalog.find((r) => r.category === "serve") ||
    catalog[0];
  if (!selected)
    return el(
      "div",
      {},
      heading(
        "Configuration",
        "Versioned candidates, explicit impact, independently verified results.",
      ),
      empty("No configurable resources are authorized."),
    );
  const controls = await request(query("controls", { resource: selected.id }), {
    signal: ctx.signal,
  });
  if (selected.kind === "profile")
    return el(
      "div",
      {},
      heading(
        "Configuration",
        "Declared operating profile. Review its current owner impact before applying.",
      ),
      el(
        "div",
        { class: "filters" },
        field(
          "Resource",
          select(
            catalog.map((r) => [r.id, r.label || r.id]),
            selected.id,
            (event) => {
              location.hash = route("configuration", event.target.value);
            },
          ),
        ),
      ),
      el(
        "section",
        { class: "panel stack" },
        el("h2", { text: selected.label || selected.id }),
        notice("A profile transition belongs to its declared resource owner."),
        actionButtons(selected.id, controls, ctx),
      ),
    );
  let draft = drafts.get(selected.id);
  if (
    draft?.validated &&
    draft.validated.baseline_digest !== controls.baseline_digest
  ) {
    draft.validated = null;
  }
  if (!draft) {
    draft = {
      values: Object.fromEntries(
        list(controls.settings)
          .filter((s) => s.support === "supported")
          .map((s) => [s.setting_id, s.configured]),
      ),
      dirty: false,
      validated: null,
      id: null,
    };
    drafts.set(selected.id, draft);
  }
  const inputs = new Map(),
    errors = new Map();
  const errorSummary = el("div", {
    class: "form-errors",
    tabindex: "-1",
    role: "alert",
  });
  const info = el("div", { class: "draft-info" });
  const validate = button(
    "Validate",
    validateDraft,
    "primary",
    !getSession()?.operate,
  );
  const review = button(
    "Review impact",
    () =>
      previewAction(selected.id, applyAction, ctx, {
        draft_id: draft.validated.id,
      }),
    "",
    true,
  );
  const applyAction = list(controls.actions).find(
    (a) => a.id === "configuration.apply",
  );
  function update() {
    validate.disabled =
      !getSession()?.operate ||
      !applyAction?.supported ||
      !applyAction?.permitted;
    info.replaceChildren(
      el("strong", {
        text: draft.dirty ? "Unsaved candidate" : "Configured values",
      }),
      el("p", {
        text: draft.validated
          ? `Validated version ${draft.validated.version} · ${draft.validated.candidate_digest}`
          : "Draft remains in this browser until Validate writes a private candidate.",
      }),
    );
    review.disabled =
      !draft.validated ||
      !applyAction?.supported ||
      !applyAction?.permitted ||
      !getSession()?.operate;
  }
  async function validateDraft() {
    errorSummary.replaceChildren();
    for (const [id, input] of inputs) {
      input.removeAttribute("aria-invalid");
      errors.get(id).textContent = "";
    }
    const local = [];
    for (const [id, input] of inputs) {
      if (!input.disabled && !input.checkValidity())
        local.push({ field: id, message: input.validationMessage });
    }
    if (local.length) {
      showErrors(local);
      return;
    }
    validate.disabled = true;
    try {
      const payload = { resource_id: selected.id, values: draft.values };
      if (draft.id) payload.draft_id = draft.id;
      const response = await request("drafts", {
        method: "POST",
        body: payload,
        signal: ctx.signal,
      });
      draft.id = response.id;
      if (list(response.errors).length) {
        draft.validated = null;
        showErrors(response.errors);
      } else {
        draft.validated = response;
        ctx.announce(
          "Draft validated. Review its exact impact before applying.",
        );
      }
      update();
    } catch (error) {
      if (error.name !== "AbortError") {
        draft.validated = null;
        errorSummary.replaceChildren(
          el("h2", { text: "Validation could not complete" }),
          el("p", { text: error.message }),
        );
        errorSummary.focus();
        update();
      }
    } finally {
      update();
    }
  }
  function showErrors(items) {
    errorSummary.replaceChildren(
      el("h2", { text: "Resolve these fields before reviewing" }),
      el(
        "ul",
        {},
        items.map((error) => {
          const input = inputs.get(error.field);
          if (input) {
            input.setAttribute("aria-invalid", "true");
            errors.get(error.field).textContent = error.message;
            return el(
              "li",
              {},
              el("a", {
                href: `#${input.id}`,
                text: error.message,
                onClick: (event) => {
                  event.preventDefault();
                  input.focus();
                },
              }),
            );
          }
          return el("li", { text: error.message });
        }),
      ),
    );
    errorSummary.focus();
  }
  const editor = el("div", { class: "config-editor" });
  for (const setting of list(controls.settings)) {
    const id = `setting-${setting.setting_id}`;
    const error = el("span", { class: "field-error", id: `${id}-error` });
    const help = el("p", {
      id: `${id}-help`,
      text: setting.help || "No additional constraints reported.",
    });
    const input = settingInput(
      setting,
      draft.values[setting.setting_id] ?? setting.configured,
      () => {
        draft.values[setting.setting_id] = inputValue(input);
        draft.dirty = true;
        draft.validated = null;
        update();
      },
    );
    input.id = id;
    input.setAttribute("aria-describedby", `${id}-help ${id}-error`);
    input.setAttribute("aria-labelledby", `${id}-title ${id}-proposed`);
    input.disabled =
      !getSession()?.operate ||
      !applyAction?.permitted ||
      !applyAction?.supported ||
      ![true, "supported", "available"].includes(setting.support) ||
      ["read-only", "read_only"].includes(setting.effect);
    inputs.set(setting.setting_id, input);
    errors.set(setting.setting_id, error);
    editor.append(
      el(
        "div",
        { class: "setting-row" },
        el(
          "div",
          { class: "setting-description" },
          el("h3", {
            id: `${id}-title`,
            text: setting.label || setting.setting_id,
          }),
          help,
          el("p", {
            text: `Applies: ${show(setting.effect)} · ${setting.unit || "No unit"}`,
          }),
          input.disabled
            ? el("p", {
                text: !getSession()?.operate
                  ? "Operate access is required to propose changes."
                  : `Support: ${show(setting.support)}. This field is read-only.`,
              })
            : null,
        ),
        el(
          "div",
          { class: "setting-cell" },
          el("span", { class: "field-label", text: "Configured" }),
          el("span", {
            class: "setting-value",
            "data-configured": setting.setting_id,
            text: show(setting.configured),
          }),
        ),
        el(
          "div",
          { class: "setting-cell" },
          el("span", { class: "field-label", text: "Observed" }),
          el("span", {
            class: "setting-value",
            "data-observed": setting.setting_id,
            text: show(setting.observed),
          }),
          badge(setting.observed_status),
        ),
        el(
          "div",
          { class: "setting-cell setting-proposed" },
          el("label", {
            class: "field-label",
            id: `${id}-proposed`,
            for: id,
            text: "Proposed",
          }),
          input,
          error,
        ),
      ),
    );
  }
  update();
  const strip = el(
    "div",
    { class: "draft-strip" },
    info,
    button(
      "Discard draft",
      () => {
        drafts.delete(selected.id);
        ctx.refresh();
      },
      "quiet-button",
    ),
    validate,
    review,
  );
  const choices = select(
    catalog.map((r) => [r.id, r.label || r.id]),
    selected.id,
    (event) => {
      location.hash = route("configuration", event.target.value);
    },
  );
  return el(
    "div",
    {},
    heading(
      "Configuration",
      "Configured, observed, and proposed values remain distinct. Edits never change a running owner.",
    ),
    el(
      "div",
      { class: "tabs", "aria-label": "Configuration categories" },
      [
        ["gateway", "Gateway policy"],
        ["serve", "Serve/runtime recipe"],
        ["profile", "Declared operating profiles"],
      ].map(([category, label]) => {
        const resource = catalog.find((r) => r.category === category);
        return resource
          ? el("a", {
              href: route("configuration", resource.id),
              "aria-current":
                selected.category === category ? "page" : undefined,
              text: label,
            })
          : el("span", { class: "meta", text: `${label} · not declared` });
      }),
    ),
    el("div", { class: "filters" }, field("Resource", choices)),
    notice(
      "Drafts survive navigation in this session. Reloading or signing out discards local edits; applying requires a fresh impact preview.",
    ),
    el(
      "div",
      { class: "detail-block" },
      kv([
        [
          "Installed baseline",
          el("code", { text: controls.baseline_digest || "Not reported" }),
        ],
      ]),
    ),
    errorSummary,
    editor.childElementCount
      ? editor
      : empty("This resource exposes no editable setting descriptors."),
    !applyAction?.supported
      ? notice(
          applyAction?.reason ||
            "Configuration installation is not supported by this owner.",
          "warning",
        )
      : null,
    strip,
  );
}
