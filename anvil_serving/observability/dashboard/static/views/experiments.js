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
export async function experimentsView(ctx, id) {
  const declared = list(ctx.fleet.control_resources)
    .filter((r) => r.kind === "experiment")
    .map((r) => ({ ...r, display_name: r.label || r.id }));
  const serves = (declared.length ? declared : list(ctx.fleet.serves)).filter(
    (s) => !ctx.host || s.host_id === ctx.host,
  );
  const selected = serves.find((s) => s.id === id) || serves[0];
  if (!selected)
    return el(
      "div",
      {},
      heading(
        "Experiments",
        "Bounded tests on one declared serve, dispatched by its resource owner.",
      ),
      empty("No declared serves are available for managed experiments."),
    );
  const controls = await request(query("controls", { resource: selected.id }), {
    signal: ctx.signal,
  });
  const catalog = list(controls.actions);
  const action =
    catalog.find((a) => a.id === "experiment.start") ||
    (!declared.length &&
      catalog.find((a) => a.id === "serve.probe" && a.supported));
  const inputs = new Map();
  const fields = el("div", { class: "grid two" });
  const descriptors = list(controls.experiment_settings);
  for (const setting of descriptors) {
    const input = settingInput(
      setting,
      setting.configured ?? setting.default,
      () => {},
    );
    input.id = `experiment-${setting.setting_id}`;
    inputs.set(setting.setting_id, input);
    fields.append(
      field(
        `${setting.label} ${setting.unit ? `(${setting.unit})` : ""}`,
        input,
        setting.help,
      ),
    );
  }
  const error = el("div", { role: "alert" });
  const supported =
    !!action?.supported && !!action?.permitted && !!getSession()?.operate;
  const review = button(
    "Review experiment",
    () => {
      error.replaceChildren();
      for (const input of inputs.values()) if (!input.reportValidity()) return;
      const parameters = Object.fromEntries(
        [...inputs.entries()].map(([key, input]) => [key, inputValue(input)]),
      );
      previewAction(selected.id, action, ctx, { parameters });
    },
    "primary",
    !supported,
  );
  const choices = select(
    serves.map((s) => [s.id, s.display_name || s.id]),
    selected.id,
    (event) => {
      location.hash = route("experiments", event.target.value);
    },
  );
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
    el(
      "div",
      { class: "filters" },
      field(
        declared.length ? "Declared managed test" : "Declared serve",
        choices,
      ),
    ),
    el(
      "div",
      { class: "grid two" },
      el(
        "section",
        { class: "panel stack" },
        el("h2", { text: "Request-only experiment" }),
        notice(
          "Parameters apply to this test only. No prompt, model, endpoint, or fallback can be supplied by this browser.",
        ),
        kv([
          ["Target", selected.display_name || selected.id],
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
        descriptors.length
          ? null
          : notice(
              "This declared test uses fixed owner parameters. No request fields are editable here; review its exact impact before dispatch.",
            ),
        error,
        !supported
          ? notice(
              !getSession()?.operate
                ? "Operate access is required."
                : action?.reason ||
                    "This owner does not expose an authorized managed experiment operation.",
              "warning",
            )
          : null,
        review,
      ),
      el(
        "section",
        { class: "panel stack" },
        el("h2", { text: "Runtime candidate" }),
        el("p", {
          class: "muted",
          text: "An explicit recipe revision must be reviewed and installed through Configuration before a supported comparison. Preserve the prior revision and recovery plan.",
        }),
        el("a", {
          href: route("configuration", selected.id),
          text: "Open configuration →",
        }),
      ),
    ),
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
            retained.map((e) =>
              el(
                "div",
                { class: "panel" },
                el("h3", { text: e.label || e.id }),
                el("p", {
                  class: "meta",
                  text:
                    e.comparable === true
                      ? "Comparison dimensions match."
                      : "Not directly comparable.",
                }),
                el("p", { class: "meta", text: show(e.limitations) }),
                button(
                  "View evidence",
                  () => evidenceDialog(e.id, ctx),
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
