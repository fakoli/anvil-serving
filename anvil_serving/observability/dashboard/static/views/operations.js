import {
  el,
  list,
  button,
  heading,
  section,
  notice,
  empty,
  kv,
  badge,
  timestamp,
  show,
  route,
  jsonDetails,
} from "./common.js";
import { request, query, getSession } from "./api.js";
const dialog = document.getElementById("operation-dialog");
let dialogAbort = null,
  returnFocus = null,
  pollTimer = null;
export function closeDialog() {
  dialogAbort?.abort();
  clearTimeout(pollTimer);
  dialog.close();
  if (returnFocus?.isConnected) returnFocus.focus();
}
dialog.addEventListener("cancel", () => {
  dialogAbort?.abort();
  clearTimeout(pollTimer);
});
dialog.addEventListener("close", () => {
  dialogAbort?.abort();
  clearTimeout(pollTimer);
  if (returnFocus?.isConnected) returnFocus.focus();
});
function shell(title, subtitle) {
  dialogAbort?.abort();
  clearTimeout(pollTimer);
  dialogAbort = new AbortController();
  if (!dialog.open) returnFocus = document.activeElement;
  const titleNode = el("h2", {
    id: "dialog-title",
    tabindex: "-1",
    text: title,
  });
  const body = el("div", { class: "dialog-body" }),
    foot = el("div", { class: "dialog-foot" }),
    // Orca treats role=status as browser UI text; the generic polite region
    // preserves live announcements without repeating unchanged owner polls.
    status = el("div", {
      id: "operation-status-announcement",
      class: "sr-only",
      "aria-live": "polite",
      "aria-atomic": "true",
    });
  dialog.replaceChildren(
    el(
      "div",
      { class: "dialog-header" },
      el("div", {}, titleNode, el("p", { class: "meta", text: subtitle })),
      button("×", closeDialog, "icon-button"),
    ),
    status,
    body,
    foot,
  );
  dialog
    .querySelector(".icon-button")
    .setAttribute("aria-label", "Close dialog");
  if (!dialog.open) dialog.showModal();
  titleNode.focus();
  return { body, foot, status, signal: dialogAbort.signal };
}
export function metadataDialog(title, pairs, extra) {
  const { body, foot } = shell(
    title,
    "Metadata · no generic lifecycle authority",
  );
  body.append(kv(pairs));
  if (extra) body.append(extra);
  foot.append(button("Close", closeDialog));
}
export async function evidenceDialog(id, ctx) {
  const { body, foot, signal } = shell(
    "Evidence",
    "Retained result, separate from current state",
  );
  body.append(el("p", { text: "Loading evidence…" }));
  foot.append(button("Close", closeDialog));
  try {
    const data = await request(`evidence/${encodeURIComponent(id)}`, {
      signal,
    });
    if (!signal.aborted) {
      body.replaceChildren(
        notice(
          "A successful check proves its stated test only. Missing dimensions remain unknown.",
        ),
        jsonDetails(data, "Evidence metadata"),
      );
      if (data.kind === "container_exec") {
        body.prepend(kv([["Diagnostic status", data.status], ["Exit code", data.exit_code], ["Output truncated", data.truncated ? "Yes" : "No"]]),
          el("pre", { class: "code-block", text: data.output || "The diagnostic returned no output." }));
      }
    }
  } catch (error) {
    if (error.name !== "AbortError")
      body.replaceChildren(notice(error.message, "danger"));
  }
}
export async function previewAction(resource, action, ctx, extra = {}) {
  const { body, foot, signal } = shell(
    "Review impact",
    `${resource} · ${action.label || action.id}`,
  );
  body.append(
    el("p", {
      role: "status",
      text: "Checking current owner, policy, and candidate…",
    }),
  );
  foot.append(button("Close", closeDialog));
  try {
    const preview = await request("previews", {
      method: "POST",
      body: { resource_id: resource, action_id: action.id, ...extra },
      signal,
    });
    if (signal.aborted) return;
    body.replaceChildren(
      notice(
        "Review this exact candidate. Applying creates a durable operation; observed state changes only after owner verification.",
      ),
      kv([
        ["Target", preview.resource_id],
        ["Workstation", preview.host_id],
        ["Action", preview.label || preview.action_id],
        ["Effect", show(preview.effect)],
        [
          "Affected aliases",
          list(preview.affected_aliases).join(", ") || "None reported",
        ],
        ["GPU ownership", list(preview.gpu_ids).join(", ") || "Not reported"],
        ["Workload impact", show(preview.workload_impact)],
        ["Stop semantics", show(preview.stop_semantics)],
        ["Recovery", show(preview.recovery)],
      ]),
    );
    if (preview.diagnostic) body.append(kv([
      ["Container", preview.diagnostic.container_id],
      ["Diagnostic", preview.diagnostic.command_id],
      ["Command arguments", el("code", { text: JSON.stringify(preview.diagnostic.argv) })],
      ["Timeout (seconds)", preview.diagnostic.timeout_seconds],
      ["Maximum output (bytes)", preview.diagnostic.max_output_bytes],
    ]));
    if (list(preview.diff).length)
      body.append(
        el(
          "section",
          {},
          el("h3", { text: "Candidate changes" }),
          el(
            "div",
            { class: "stack" },
            preview.diff.map((change) =>
              el(
                "div",
                { class: "detail-block" },
                el("h3", { text: change.field }),
                kv([
                  ["Before", change.before],
                  ["After", change.after],
                ]),
              ),
            ),
          ),
        ),
      );
    body.append(
      el(
        "section",
        {},
        el("h3", { text: "Planned steps" }),
        el(
          "ol",
          { class: "steps" },
          list(preview.planned_steps).map((step) =>
            el("li", { text: show(step) }),
          ),
        ),
      ),
      kv([
        [
          "Baseline digest",
          el("code", { text: show(preview.baseline_digest) }),
        ],
        [
          "Candidate digest",
          el("code", { text: show(preview.candidate_digest) }),
        ],
        ["Policy digest", el("code", { text: show(preview.policy_digest) })],
        ["Expires", timestamp(preview.expires_at_epoch_seconds, ctx.zone)],
      ]),
    );
    let submitted = false;
    const intent = crypto.randomUUID();
    const apply = button(
      "Apply change",
      async () => {
        if (submitted) return;
        if (Date.now() / 1000 >= preview.expires_at_epoch_seconds) {
          apply.disabled = true;
          body.prepend(
            notice(
              "This preview expired. Close and review current impact again.",
              "warning",
            ),
          );
          return;
        }
        submitted = true;
        apply.disabled = true;
        apply.textContent = "Submitting…";
        try {
          const operation = await request("operations", {
            method: "POST",
            body: { preview_id: preview.id, intent_key: intent },
            timeout: 15000,
          });
          closeDialog();
          location.hash = route("operations", operation.id);
        } catch (error) {
          if (!dialog.open) return;
          apply.textContent = "Submission locked";
          body.prepend(
            notice(
              error.code === "transport-unavailable"
                ? "Outcome unknown; reconciling with owner. Open Operations to find the accepted intent. This page will not repeat the mutation."
                : error.message,
              "danger",
              "alert",
            ),
          );
          foot.append(
            el("a", {
              href: route("operations"),
              text: "Check Operations",
              onClick: closeDialog,
            }),
          );
        }
      },
      "primary",
      !getSession()?.operate,
    );
    foot.replaceChildren(
      button("Back to editing", closeDialog),
      apply,
      el("p", {
        text: "Closing this panel does not cancel an accepted owner operation.",
      }),
    );
    const remaining = Math.max(
      0,
      preview.expires_at_epoch_seconds * 1000 - Date.now(),
    );
    const expiry = setTimeout(
      () => {
        apply.disabled = true;
        apply.textContent = "Preview expired";
      },
      Math.min(remaining, 2147483647),
    );
    signal.addEventListener("abort", () => clearTimeout(expiry), {
      once: true,
    });
  } catch (error) {
    if (error.name !== "AbortError")
      body.replaceChildren(notice(error.message, "danger", "alert"));
  }
}
export function actionButtons(resource, controls, ctx, { exclude = [] } = {}) {
  const node = el("div", { class: "actions" });
  for (const action of list(controls?.actions).filter(
    (a) => !exclude.includes(a.id),
  )) {
    const allowed =
      getSession()?.operate && action.supported && action.permitted;
    const why = !getSession()?.operate
      ? "Operate access is required."
      : action.reason || "This action is unavailable for the current owner.";
    const id = `action-help-${crypto.randomUUID()}`;
    const btn = button(
      action.label || action.id,
      () => previewAction(resource, action, ctx),
      "",
      !allowed,
    );
    if (!allowed) btn.setAttribute("aria-describedby", id);
    node.append(
      el(
        "div",
        { class: "small-stack" },
        btn,
        !allowed ? el("span", { id, class: "meta", text: why }) : null,
      ),
    );
  }
  if (!node.childElementCount)
    node.append(
      el("span", {
        class: "meta",
        text: "No supported actions are exposed for this resource.",
      }),
    );
  return node;
}
const operationStatus = (op) =>
  op.verification?.status === "failed" ? "verification failed" : op.status;
export function operationRows(items, ctx) {
  if (!items.length)
    return empty("No operations are reported in retained history.");
  return el(
    "div",
    {},
    items.map((op) =>
      el(
        "article",
        { class: "operation-row" },
        el(
          "div",
          {},
          el(
            "a",
            { href: route("operations", op.id) },
            el("h3", { text: op.label || op.action_id }),
          ),
          el("p", {
            text: `${op.resource_id} · ${op.actor || op.service_identity || "Identity not reported"}`,
          }),
          el("p", {}, timestamp(op.submitted_at, ctx.zone)),
        ),
        el(
          "div",
          {},
          el("span", { class: "mobile-label", text: "Operation" }),
          badge(operationStatus(op)),
          el("p", { text: wordsNative(op.native_state) }),
        ),
        el(
          "div",
          {},
          el("span", { class: "mobile-label", text: "Verification" }),
          badge(op.verification?.status),
          el("p", {
            text: op.verification?.message || "Verification not reported",
          }),
        ),
        el(
          "div",
          {},
          el("span", { class: "mobile-label", text: "Recovery" }),
          badge(op.recovery?.status),
          el("p", { text: op.recovery?.message || "No recovery reported" }),
        ),
      ),
    ),
  );
}
const wordsNative = (value) =>
  value ? `Owner: ${value}` : "Owner phase not reported";
export async function operationsView(ctx) {
  const data = await request("operations", { signal: ctx.signal });
  return el(
    "div",
    {},
    heading(
      "Operations",
      "Durable operator intents and linked owner progress. Historical outcomes do not establish current runtime state.",
      [button("Refresh", ctx.refresh, "quiet-button")],
    ),
    data.truncated
      ? notice(
          "History is bounded. Older terminal operations may not be included.",
          "warning",
        )
      : null,
    el(
      "div",
      { class: "panel" },
      operationRows(
        list(data.items).filter(
          (item) => !ctx.host || item.host_id === ctx.host,
        ),
        ctx,
      ),
    ),
  );
}
export async function openOperation(id, ctx) {
  const { body, foot, status, signal } = shell(
    "Operation",
    "Retrieving durable status; this never submits an action.",
  );
  body.append(el("p", { text: "Reading owner progress…" }));
  foot.append(button("Close", closeDialog));
  let announcedState = null;
  let restoreButton = null;
  async function update() {
    if (signal.aborted || document.hidden) return;
    try {
      const op = await request(`operations/${encodeURIComponent(id)}`, {
        signal,
      });
      if (signal.aborted) return;
      const title =
        op.verification?.status === "failed"
          ? "Verification failed"
          : op.label || "Operation";
      const titleNode = document.getElementById("dialog-title");
      if (titleNode.textContent !== title) titleNode.textContent = title;
      body.replaceChildren(
        el(
          "div",
          { class: "status-row" },
          badge(operationStatus(op)),
          badge(`owner: ${op.native_state || "unknown"}`),
        ),
        ...(/unknown|unresolved|reconcil/.test(op.status)
          ? [
              notice(
                "Outcome unknown; reconciling with owner. No automatic second mutation will be sent.",
                "warning",
                "note",
              ),
            ]
          : []),
        kv([
          ["Target", op.resource_id],
          ["Workstation", op.host_id],
          ["Actor", op.actor],
          ["Service identity", op.service_identity],
          ["Submitted", timestamp(op.submitted_at, ctx.zone)],
          ["Updated", timestamp(op.updated_at, ctx.zone)],
          ["Execution outcome", show(op.execution_outcome)],
          ["Verification", badge(op.verification?.status)],
          ["Verification detail", op.verification?.message],
          ["Recovery", badge(op.recovery?.status)],
          ["Recovery detail", op.recovery?.message],
          [
            "Owner operation",
            el("code", { text: show(op.owner_operation_id) }),
          ],
          ["Intent ID", el("code", { text: op.id })],
        ]),
        el(
          "section",
          {},
          el("h3", { text: "Owner events" }),
          el(
            "ol",
            { class: "timeline" },
            list(op.events).map((event) =>
              el(
                "li",
                {},
                el(
                  "div",
                  { class: "meta" },
                  timestamp(event.at, ctx.zone),
                  ` · ${event.source} · ${event.phase}`,
                ),
                el("p", { text: event.message }),
              ),
            ),
          ),
        ),
      );
      if (op.evidence_id)
        body.append(
          button(
            "View retained evidence",
            () => evidenceDialog(op.evidence_id, ctx),
            "quiet-button",
          ),
        );
      const currentState = [
        operationStatus(op) || "unknown",
        op.native_state || "unknown",
        op.execution_outcome || "unknown",
        op.verification?.status || "unknown",
        op.recovery?.status || "unknown",
      ].map((value) => String(value).replaceAll("_", " "));
      const fingerprint = JSON.stringify(currentState);
      if (fingerprint !== announcedState) {
        announcedState = fingerprint;
        status.textContent = `Operation ${currentState[0]}. Owner ${currentState[1]}. Execution ${currentState[2]}. Verification ${currentState[3]}. Recovery ${currentState[4]}.`;
      }
      let restoreAction = null;
      if (
        ["manual_recovery_required", "outcome_unknown"].includes(op.status) &&
        getSession()?.operate
      ) {
        try {
          const controls = await request(
            query("controls", { resource: op.resource_id }),
            { signal },
          );
          restoreAction = list(controls.actions).find(
            (action) =>
              action.id === "operation.recover" &&
              action.supported &&
              action.permitted,
          );
        } catch (error) {
          if (error.name === "AbortError") return;
        }
      }
      if (signal.aborted) return;
      if (restoreAction && !restoreButton) {
        restoreButton = button(
          "Review restore",
          () =>
            previewAction(op.resource_id, restoreAction, ctx, {
              operation_id: op.id,
            }),
          "primary",
        );
        foot.append(restoreButton);
      } else if (!restoreAction && restoreButton) {
        restoreButton.remove();
        restoreButton = null;
      }
      if (
        !["succeeded", "failed", "cancelled", "completed", "rejected"].includes(
          op.status,
        )
      )
        pollTimer = setTimeout(update, 2000);
    } catch (error) {
      if (error.name !== "AbortError") {
        body.prepend(notice(error.message, "warning", "note"));
        pollTimer = setTimeout(update, 10000);
      }
    }
  }
  const wake = () => {
    if (!document.hidden && !signal.aborted) {
      clearTimeout(pollTimer);
      update();
    }
  };
  document.addEventListener("visibilitychange", wake);
  signal.addEventListener(
    "abort",
    () => document.removeEventListener("visibilitychange", wake),
    { once: true },
  );
  await update();
}
