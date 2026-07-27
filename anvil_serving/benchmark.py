#!/usr/bin/env python
"""Public compatibility facade for benchmark workflows."""

import os
import sys
import urllib.request as _urllib_request

if __package__ in (None, ""):  # direct ``python anvil_serving/benchmark.py``
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "anvil_serving"

from .benchmarking import cli as _cli
from .benchmarking.evaluation import (
    aggregate_attempts as _aggregate_attempts,
    attempt_passed as _attempt_passed,
    eval_budget,
    failure_class as _failure_class,
    request_control_kwargs as _request_control_kwargs,
    resolve_thinking_settings,
)
from .benchmarking.recipes import build_recipe, emit_recipe, serve_recipes
from .benchmarking.requests import (
    CHARS_PER_TOKEN,
    DEFAULT_CTX_MARGIN,
    FILLER,
    _choice_messages,
    build_body,
    clamp_ctx,
    ctx_cap,
    detect_max_model_len,
    est_tokens,
    make_prompt,
    post_chat,
    resolve_api_key,
    response_observation,
    stream_chat,
    validate_function_tool_call,
)
from .benchmarking.runner import (
    BAKEOFF_TOOL,
    INTELLIGENCE_PROMPTS,
    SESSION_RECALL_PROMPT,
    SUBAGENT_CTX,
    cached_fraction,
    percentile,
    result_metrics as _result_metrics,
    run_bakeoff as _run_bakeoff,
    sample_ctx,
    validate_eval_work_plan,
)
from .benchmarking.specs import (
    evaluate_text_checks,
    load_control_evidence,
    load_suite_spec,
    parse_csv,
    parse_context_targets,
)

__all__ = [
    "BAKEOFF_TOOL",
    "CHARS_PER_TOKEN",
    "DEFAULT_CTX_MARGIN",
    "FILLER",
    "INTELLIGENCE_PROMPTS",
    "SESSION_RECALL_PROMPT",
    "SUBAGENT_CTX",
    "_aggregate_attempts",
    "_attempt_passed",
    "_choice_messages",
    "_failure_class",
    "_request_control_kwargs",
    "_result_metrics",
    "_serve_recipes",
    "build_body",
    "build_recipe",
    "cached_fraction",
    "clamp_ctx",
    "ctx_cap",
    "detect_max_model_len",
    "emit_recipe",
    "est_tokens",
    "eval_budget",
    "evaluate_text_checks",
    "load_control_evidence",
    "load_suite_spec",
    "main",
    "make_prompt",
    "parse_csv",
    "parse_context_targets",
    "pctile",
    "resolve_api_key",
    "resolve_thinking_settings",
    "post_chat",
    "response_observation",
    "run_bakeoff",
    "sample_ctx",
    "stream_chat",
    "urllib",
    "validate_eval_work_plan",
    "validate_function_tool_call",
]

urllib = sys.modules[_urllib_request.__package__]
_serve_recipes = serve_recipes
pctile = percentile


def main(argv=None, *, prog=None):
    """Delegate CLI coordination while preserving facade monkeypatch seams."""
    return _cli.main(
        argv,
        prog=prog,
        post_request=post_chat,
        stream_request=stream_chat,
        detect_context_limit=detect_max_model_len,
    )


def run_bakeoff(args, api_key):
    """Run quality orchestration while preserving facade transport patch points."""
    return _run_bakeoff(
        args,
        api_key,
        post_request=post_chat,
        stream_request=stream_chat,
        detect_context_limit=detect_max_model_len,
    )


if __name__ == "__main__":
    raise SystemExit(main())
