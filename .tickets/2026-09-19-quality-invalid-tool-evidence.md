# Retain bounded invalid tool arguments in quality evidence

Status: resolved for bounded diagnostics on 2026-09-19. Both built-in and
external tool suites now retain wire type and a bounded string excerpt without
changing validation. Capture examines at most four messages and sixteen calls
per message, retaining four excerpts of at most 2048 characters. Non-string
values are never serialized. Focused diagnostics and benchmark tests: 173 pass.
Independent review prompted the inspection-budget regression.

Fresh original-runtime evidence confirms `{"zip":98101}`; compatible runtime
`70434721b1ae29d0616f3de9b376c8a4d91590b5` returns `{"zip":"98101"}` and
passes the repeated core tool gate. See the separate
[runtime follow-up](../docs/findings/2026-09-19-qwen38-huihui-runtime-followup.md).
Generated excerpts remain subject to manual privacy review before publication.

## Original observation

The 2026-09-19 Huihui NInfer quality runs reject all three calls requiring ZIP
string `"98101"`, under both requested reasoning controls. The incumbent passes
3/3. The native artifacts retain `arguments=null` and the validator error
`missing required string argument: zip`, losing the invalid payload needed to
distinguish omitted values, numeric coercion, quoting, and parser failures.

The pinned NInfer parser's JSON interpretation of XML parameter text is a
possible cause, not a confirmed diagnosis. Do not attribute this result solely
to the checkpoint or serving engine without raw evidence.

Follow-up scope: preserve a bounded, explicitly sanitized raw tool-call argument
value and its original type on validation failures. Test numeric-looking string,
alphabetic string, CRLF, quoted JSON, and XML/metacharacter values independently.
Avoid leaking arbitrary prompts or credentials into public evidence. A corrected
runtime/template profile must pass fresh qualification before expensive context,
MTP, performance, or promotion work resumes.
