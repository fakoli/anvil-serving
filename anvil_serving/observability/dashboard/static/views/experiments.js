import {
  el,
  list,
  heading,
  field,
  select,
  button,
  notice,
  empty,
  kv,
  route,
  show,
  table,
  badge,
} from "./common.js";
import { request, query, getSession } from "./api.js";
import { previewAction, evidenceDialog } from "./operations.js";
import { settingInput, inputValue } from "./configuration.js";

const selections = { request: null, runtime: null };

async function experimentPanel(
  ctx,
  resources,
  id,
  { runtime = false, serveFallback = false } = {},
) {
  const kind = runtime ? "runtime" : "request";
  const title = runtime ? "Runtime candidate" : "Request-only experiment";
  const selected =
    resources.find((item) => item.id === id) ||
    resources.find((item) => item.id === selections[kind]) ||
    resources[0];
  const panel = el(
    "section",
    { class: "panel stack", "aria-label": title },
    el("h2", { text: title }),
  );
  if (!selected) {
    panel.append(
      empty(
        runtime
          ? "No owner-managed runtime candidate test is declared for this scope."
          : "No declared request-only test or serve is available for this scope.",
      ),
    );
    return panel;
  }
  selections[kind] = selected.id;
  panel.append(
    field(
      runtime
        ? "Runtime candidate test"
        : serveFallback
          ? "Declared serve"
          : "Declared managed test",
      select(
        resources.map((item) => [
          item.id,
          item.display_name || item.label || item.id,
        ]),
        selected.id,
        (event) => {
          selections[kind] = event.target.value;
          location.hash = route("experiments", event.target.value);
        },
      ),
    ),
  );
  let controls;
  try {
    controls = await request(query("controls", { resource: selected.id }), {
      signal: ctx.signal,
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    panel.append(notice(error.message, "warning"));
    return panel;
  }
  const catalog = list(controls.actions);
  const action =
    catalog.find((item) => item.id === "experiment.start") ||
    (serveFallback &&
      catalog.find((item) => item.id === "serve.probe" && item.supported));
  const classMatches = runtime
    ? controls.experiment_class === "runtime_candidate"
    : controls.experiment_class !== "runtime_candidate";
  const supported =
    classMatches &&
    !!action?.supported &&
    !!action?.permitted &&
    !!getSession()?.operate;
  const descriptors = list(controls.experiment_settings);
  const inputs = new Map();
  const fields = el("div", { class: "grid two" });
  for (const setting of descriptors) {
    const input = settingInput(
      setting,
      setting.configured ?? setting.default,
      () => {},
    );
    input.id = `${runtime ? "runtime-candidate" : "experiment"}-${setting.setting_id}`;
    input.disabled = !supported;
    inputs.set(setting.setting_id, input);
    fields.append(
      field(
        `${setting.label} ${setting.unit ? `(${setting.unit})` : ""}`,
        input,
        setting.help,
      ),
    );
  }
  const why = !getSession()?.operate
    ? "Operate access is required."
    : !classMatches
      ? "The owner test class does not match this catalog entry. Refresh the declared resources."
      : action?.reason ||
        "This owner does not expose an authorized operation for this test.";
  const helpId = `experiment-help-${crypto.randomUUID()}`;
  const review = button(
    runtime ? "Review runtime candidate" : "Review experiment",
    () => {
      for (const input of inputs.values()) if (!input.reportValidity()) return;
      const parameters = Object.fromEntries(
        [...inputs.entries()].map(([key, input]) => [key, inputValue(input)]),
      );
      previewAction(selected.id, action, ctx, { parameters });
    },
    "primary",
    !supported,
  );
  if (!supported) review.setAttribute("aria-describedby", helpId);
  panel.append(
    ...[
      notice(
        runtime
          ? "The owner checks the baseline, installs this temporary candidate, runs its fixed check, then restores and verifies the exact baseline. A successful comparison does not promote the candidate."
          : "Parameters apply to this test only. No prompt, model, endpoint, or fallback can be supplied by this browser.",
      ),
      kv([
        ["Target", selected.display_name || selected.label || selected.id],
        ["Model", selected.model],
        ["Engine", selected.engine],
        ["GPU ownership", list(selected.gpu_ids).join(", ")],
        [
          "Conflict limit",
          controls.experiment_limit ??
            "No conflict policy reported; review the owner's exact impact.",
        ],
      ]),
      fields,
      !descriptors.length
        ? notice(
            "This declared test uses fixed owner parameters. No request fields are editable here; review its exact impact before dispatch.",
          )
        : null,
      runtime
        ? notice(
            "Review the baseline and candidate digests, fixed alias, field changes, and restore steps before confirmation. Failed restoration requires explicit recovery; completion alone does not prove restoration.",
            "warning",
          )
        : null,
      !supported
        ? el("div", { id: helpId, class: "notice warning" }, why)
        : null,
      review,
    ].filter((node) => node !== null),
  );
  return panel;
}

export async function experimentsView(ctx, id) {
  const declared = list(ctx.fleet.control_resources).filter(
    (item) => item.kind === "experiment",
  );
  const scoped = (items) =>
    items.filter((item) => !ctx.host || item.host_id === ctx.host);
  const runtimeResources = scoped(
    declared.filter((item) => item.experiment_class === "runtime_candidate"),
  );
  const requestResources = scoped(
    declared.filter(
      (item) =>
        !item.experiment_class || item.experiment_class === "request_only",
    ),
  );
  const serveFallback = requestResources.length === 0;
  const requestTargets = serveFallback
    ? scoped(list(ctx.fleet.serves))
    : requestResources;
  const [requestPanel, runtimePanel] = await Promise.all([
    experimentPanel(ctx, requestTargets, id, { serveFallback }),
    experimentPanel(ctx, runtimeResources, id, { runtime: true }),
  ]);
  let evidence;
  try {
    evidence = await request("evidence", { signal: ctx.signal });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    evidence = { items: [], reason: error.message };
  }
  const retained = list(evidence.items);
  return el(
    "div",
    {},
    heading(
      "Experiments",
      "Managed, bounded checks. A successful test does not establish general model quality or promote a candidate.",
    ),
    el("div", { class: "grid two" }, requestPanel, runtimePanel),
    el(
      "div",
      { class: "section-heading" },
      el("h2", { text: "Retained comparisons" }),
    ),
    notice(
      "Compare host, GPU hardware, model revision, quantization, engine, context, concurrency, output constraints, warm/cold state, and correctness gates. Missing or different dimensions are not directly comparable.",
    ),
    retained.length
      ? el(
          "div",
          { class: "stack" },
          comparison(retained),
          el(
            "div",
            { class: "grid two" },
            retained.map((item) =>
              el(
                "div",
                { class: "panel" },
                el("h3", { text: item.label || item.id }),
                item.kind === "runtime_experiment"
                  ? kv([
                      ["Comparison state", badge(item.state || "unknown")],
                      ["Correctness", badge(item.correctness || "unknown")],
                      [
                        "Baseline restore",
                        badge(item.recovery?.status || "unknown"),
                      ],
                    ])
                  : null,
                el("p", {
                  class: "meta",
                  text:
                    item.comparable === true
                      ? "Comparison dimensions match."
                      : "Not directly comparable.",
                }),
                el("p", { class: "meta", text: show(item.limitations) }),
                button(
                  "View evidence",
                  () => evidenceDialog(item.id, ctx),
                  "quiet-button",
                ),
              ),
            ),
          ),
        )
      : empty(
          evidence.reason ||
            "No retained experiment comparison is reported. Failures and incomplete results belong in retained evidence.",
        ),
  );
}

function comparison(records) {
  const values = [records[0]?.id || "", records[1]?.id || records[0]?.id || ""];
  const result = el("div", {});
  const dimensionKeys = [
    "host",
    "hardware",
    "model_revision",
    "quantization",
    "engine",
    "context",
    "concurrency",
    "output_constraints",
    "warm_state",
    "correctness",
  ];
  const render = () => {
    const pair = values.map((id) => records.find((record) => record.id === id));
    const dimensions = pair.map((item) => item?.comparison_dimensions || {});
    const keys = [
      ...new Set([
        ...dimensionKeys,
        ...Object.keys(dimensions[0]),
        ...Object.keys(dimensions[1]),
      ]),
    ];
    let comparable = true;
    const rows = keys.map((key) => {
      const left = dimensions[0][key],
        right = dimensions[1][key];
      const known =
        left !== null &&
        left !== undefined &&
        right !== null &&
        right !== undefined;
      const same = known && JSON.stringify(left) === JSON.stringify(right);
      if (!same) comparable = false;
      return [
        key,
        show(left),
        show(right),
        same ? "Match" : known ? "Different" : "Unknown",
      ];
    });
    result.replaceChildren(
      notice(
        comparable
          ? "All required comparison dimensions match. Review correctness and limitations before interpreting performance."
          : "Not directly comparable. At least one required dimension differs or is unknown.",
        comparable ? "" : "warning",
      ),
      table(
        [
          "Dimension",
          pair[0]?.label || "Baseline",
          pair[1]?.label || "Candidate",
          "Comparison",
        ],
        rows,
        "Exact retained evidence dimensions; unknown is never inherited from another run.",
      ),
    );
  };
  render();
  return el(
    "section",
    { class: "panel stack" },
    el("h2", { text: "Compare retained evidence" }),
    el(
      "div",
      { class: "grid two" },
      ...["Baseline evidence", "Candidate evidence"].map((label, index) =>
        field(
          label,
          select(
            records.map((e) => [e.id, e.label || e.id]),
            values[index],
            (event) => {
              values[index] = event.target.value;
              render();
            },
          ),
        ),
      ),
    ),
    result,
  );
}
