# Shared-prefix tool failure analysis

This is the public-safe projection of the protected offline review. It uses
the exact preflight source at
`dc576b25083d7cfdab1554db89379feee82346b5` and makes no root-cause claim.

## Finding

The preflight result is a valid miss against the frozen harness's
`tool_choice: "auto"` reliability expectation, not an evidence-counting or
response-validation bug. It is not a terminal candidate judgment and does not
establish an Ignis engine/model defect. Four of five named checks passed, but
the shared-prefix tools check passed only 3/4 requests. The failed request
returned assistant prose, `finish_reason: "stop"`, and no `tool_calls`. The
frozen validator correctly reported `response did not include tool_calls`.

This is not proof of an OpenAI-protocol violation. The request used
`tool_choice: "auto"`, which permits the model to choose whether to call a
tool. It is nevertheless a real failure of the declared reliability gate: the
user message explicitly said `Use the tool`, and the batch required every
response to contain a schema-valid `get_weather` call with a non-empty string
`city` argument.

## Exact request construction

Each of the four batch requests used the non-streaming Chat Completions path,
the same 400-line system prefix, and the user message `What's the weather in
Oakland? Use the tool.`. The tools array declared one function,
`get_weather(city: string)`, with `city` required. The body included
`tool_choice: "auto"`, `max_tokens: 4608`, and `temperature: 0.6`. Thinking
mode was `default`, so neither `chat_template_kwargs` nor `reasoning_effort`
was sent. The timeout was 60 seconds and retries were zero.

The body builder is at `anvil_serving/preflight.py@dc576b25:43-60`; the tool
schema, prompt, choice, and validator are at lines 354-409; and the four calls
are submitted concurrently in one phase at lines 413-426.

## Engine correlation

Prompt, completion, and cache-token counts uniquely map the failed harness
observation to engine request ID 5: 5,521 prompt tokens, 13 completion tokens,
and 5,519 reused prompt tokens. The other three batch members map to request
IDs 3, 4, and 6. Two of those successful requests also reused 5,519 prompt
tokens, so the evidence does not support a cache-only explanation.

The engine records internal `finish_reason: "stop"` for all nine done events,
including requests that the API represented as successful `tool_calls`.
Therefore the internal field cannot explain the prose response and is not
treated as the API finish reason.

Every native observation has a corresponding admitted, first-token, and done
engine event. The artifacts do not share a request ID, but the token-count
mapping is unique in this run. Neither artifact retains an explicit HTTP status
code, so this review does not relabel the evidence as literal `HTTP 200` proof.

## Request-count correction

The execution made nine HTTP requests in six sequential phases: one smoke, one
JSON, four concurrent tools, one streaming-tools, one tool-result initial, and
one tool-result continuation request. The earlier six-request statement
counted phases rather than calls. The command itself did not change.

## Disposition and later follow-up

The separately frozen phase-two work retained this result unchanged and used
official Ignis 0.8 tool/template/thinking guidance to test the supported
mandatory-tool policy. The same runtime/model/profile passed `required` with
default thinking across the mechanism cell, full five-check preflight, and a
different-seed four-call repeat. That follow-up appears in
[`tool-choice-followup.json`](tool-choice-followup.json).

The phase-one `auto` miss is therefore neither a terminal candidate rejection
nor an engine/model defect. The candidate remains unpromoted. Further causal
diagnosis of the stochastic automatic-choice miss would need separately
authorized evidence that joins the exact request body, API response, and
engine request identity; this review makes no hypothesis about the cause.
