#!/usr/bin/env python
"""Capacity and repeated-quality benchmark runners for local model endpoints.

Use ``anvil-serving eval benchmark capacity`` for performance evidence and
``anvil-serving eval benchmark quality`` for protocol-v3 correctness evidence.
The runtime remains stdlib-only (urllib, threads, and atomic JSON writes).
"""
import argparse
import json
import math
import random
import re
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..model_controls import REASONING_EFFORT_CHOICES, validate_reasoning_control
from .artifacts import (
    atomic_write_json as _atomic_write_json,
    console_safe as _console_safe,
    validate_write_target as _validate_write_target,
)
from .evaluation import (
    MAX_QUALITY_COMPLETION_TOKENS as _MAX_QUALITY_COMPLETION_TOKENS,
    request_control_kwargs as _request_control_kwargs,
    resolve_thinking_settings,
)
from .limits import (
    MAX_EVAL_REPETITIONS as _MAX_EVAL_REPETITIONS,
    MAX_TOTAL_CAPACITY_PROMPT_TOKENS as _MAX_TOTAL_CAPACITY_PROMPT_TOKENS,
)
from .recipes import emit_recipe
from .requests import (
    DEFAULT_CTX_MARGIN,
    VISIBLE_CONTENT_CAPTURE_LIMIT,
    clamp_ctx,
    ctx_cap,
    detect_max_model_len,
    make_shared_prefix,
    make_prompt,
    output_contract_observation,
    post_chat,
    resolve_api_key,
    stream_chat,
    validate_stream_result,
)
from .runner import (
    cached_fraction,
    result_metrics as _result_metrics,
    result_timings as _result_timings,
    run_bakeoff,
    sample_ctx,
    validate_eval_work_plan,
)
from .specs import load_control_evidence, load_suite_spec, parse_csv

def main(
    argv=None,
    *,
    prog=None,
    post_request=post_chat,
    stream_request=stream_chat,
    detect_context_limit=detect_max_model_len,
):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "multimodal":
        from . import multimodal

        return multimodal.main(argv[1:])
    workload = argv.pop(0) if argv and argv[0] in {"capacity", "quality"} else None
    if prog is None:
        prog = "anvil-serving eval benchmark"
        if workload is not None:
            prog += " " + workload
    if workload is not None:
        prog = "anvil-serving eval benchmark %s" % workload
    if argv and argv[0] == "external":
        from ..external_benchmarks import cli as external_bench
        return external_bench.main(
            argv[1:], prog="anvil-serving eval benchmark external"
        )

    if workload == "capacity":
        description = (
            "Measure bounded endpoint latency, throughput, context, and prefix-cache behavior."
        )
        examples = (
            "Examples:\n"
            "  anvil-serving eval benchmark capacity --tier primary --requests 10 "
            "--concurrency 1 --output primary-capacity.json --confirm\n"
            "  anvil-serving eval benchmark capacity --base-url "
            "http://127.0.0.1:30002/v1 --model MODEL --engine vllm "
            "--gpu GPU_ROLE --output run.json --confirm"
        )
    elif workload == "quality":
        description = (
            "Run repeated, bounded quality suites and retain comparison-grade evidence."
        )
        examples = (
            "Examples:\n"
            "  anvil-serving eval benchmark quality --tier primary --suite-file suite.json "
            "--candidate-id MODEL --config-id primary-v1 --control-status verified "
            "--control-evidence evidence/control.json --output quality.json --confirm\n"
            "  anvil-serving eval benchmark quality --base-url "
            "http://127.0.0.1:30002/v1 --model MODEL --engine vllm --gpu GPU_ROLE "
            "--suite intelligence --candidate-id MODEL --config-id direct "
            "--output quality.json --confirm"
        )
    else:
        description = "Benchmark a direct endpoint or a serves-manifest tier."
        examples = (
            "Compatibility parser: prefer `eval benchmark capacity` or "
            "`eval benchmark quality`."
        )

    def visible_for(*workloads):
        return workload is None or workload in workloads

    def help_for(text, *workloads):
        return text if visible_for(*workloads) else argparse.SUPPRESS

    ap = argparse.ArgumentParser(
        prog=prog,
        description=description + "\n\n" + examples,
        epilog=(
            "Configuration precedence: command flags, referenced serves manifest, then "
            "the bundled reference manifest. Direct targets require both --base-url and --model."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    endpoint = ap.add_argument_group("direct endpoint input")
    endpoint.add_argument("--base-url", help="OpenAI-compatible endpoint base URL")
    endpoint.add_argument("--model", help="served model id")
    endpoint.add_argument(
        "--engine",
        help="engine identity recorded in evidence (inferred from --tier when available)",
    )
    endpoint.add_argument(
        "--gpu",
        help="stable GPU or hardware-role identity recorded in comparable evidence",
    )
    manifest = ap.add_argument_group("serves manifest input")
    manifest.add_argument("--manifest", help="serves manifest TOML (used with --tier)")
    manifest.add_argument("--tier", help="serve name in the manifest; fills endpoint and model")
    recipe = ap.add_argument_group("serve recipe input")
    recipe.add_argument("--recipe", help="recorded recipe model selector")
    recipe.add_argument("--registry", help="serve-recipe registry used with --recipe")
    ap.add_argument("--api-key-env", default=None,
                    help="read the bearer token from this environment variable")
    ap.add_argument("--requests", type=int, default=60,
                    help=help_for("number of requests (1..10000; default %(default)s)", "capacity"))
    ap.add_argument("--concurrency", type=int, default=20,
                    help=help_for("parallel requests (1..256; default %(default)s)", "capacity"))
    ap.add_argument("--burst", type=int, default=0,
                    help=help_for("shared-prefix burst size (0..256; 0 disables)", "capacity"))
    ap.add_argument("--shared-prefix-tokens", type=int, default=8000,
                    help=help_for("shared prefix size in estimated tokens", "capacity", "quality"))
    ap.add_argument("--ctx-tokens", type=int, default=0,
                    help=help_for("fixed context; 0 samples the measured distribution", "capacity"))
    ap.add_argument("--seed", type=int, default=0,
                    help=help_for("seed for reproducible sampled contexts (default %(default)s)",
                                  "capacity"))
    ap.add_argument("--max-tokens", type=int, default=64,
                    help=help_for("capacity generation cap; quality context-probe cap", "capacity", "quality"))
    ap.add_argument("--response-words", type=int, default=0,
                    help=help_for("request an exact repeated-word output for controlled decode; "
                         "0 keeps the short-summary workload", "capacity"))
    ap.add_argument("--prompt-cache-mode", choices=("shared", "unique"), default="shared",
                    help=help_for("shared permits reusable prompt prefixes; unique inserts a "
                         "request-specific prefix before filler", "capacity"))
    ap.add_argument("--request-canaries", action="store_true",
                    help=help_for("require every response to begin with its own synthetic request "
                         "marker and contain no peer marker", "capacity"))
    ap.add_argument(
        "--controlled-output-policy",
        choices=("observe", "strict"),
        default="observe",
        help=help_for(
            "observe exact --response-words adherence without rejecting historical-style "
            "runs, or require exact adherence with strict", "capacity"
        ),
    )
    ap.add_argument(
        "--clock-domain-id",
        help=help_for(
            "explicit public-safe identifier for a shared host clock; required before "
            "nanosecond timestamps may align independent replica artifacts", "capacity"
        ),
    )
    ap.add_argument(
        "--configuration-fingerprint",
        help=help_for(
            "operator-declared sha256 fingerprint of the normalized replica configuration; "
            "matching values declare equivalence, not independently verified identity", "capacity"
        ),
    )
    ap.add_argument("--max-model-len", type=int, default=0,
                    help=help_for("context window override; 0 discovers /v1/models. Requests are "
                                  "clamped below the limit using --margin.",
                                  "capacity", "quality"))
    ap.add_argument("--margin", type=int, default=DEFAULT_CTX_MARGIN,
                    help=help_for("token headroom below max_model_len (default %(default)s)",
                                  "capacity", "quality"))
    ap.add_argument("--no-thinking", action="store_true",
                    help="compatibility alias for --thinking-mode disabled; valid only for "
                         "chat-template-controlled model families")
    ap.add_argument("--thinking-mode", choices=("default", "enabled", "disabled", "unsupported"),
                    default=None,
                    help="record/request thinking behavior for benchmark evidence. "
                         "disabled maps to chat_template_kwargs={'enable_thinking': False}; "
                         "enabled maps to {'enable_thinking': True}; unsupported records that "
                         "the serve has no supported thinking control.")
    ap.add_argument("--reasoning-effort", choices=REASONING_EFFORT_CHOICES,
                    default=None,
                    help="send the OpenAI-compatible reasoning_effort field for model families "
                         "that do not use chat_template_kwargs (for example GPT-OSS or Mistral). "
                         "Cannot be combined with --no-thinking or an explicit thinking mode.")
    ap.add_argument("--visible-answer-tokens", type=int, default=None,
                    help=help_for("override suite visible-answer allocation (default 256)", "quality"))
    ap.add_argument("--reasoning-headroom-tokens", type=int, default=None,
                    help=help_for("reasoning headroom added to visible output (default 0)", "quality"))
    ap.add_argument("--eval-repetitions", type=int, default=3,
                    help=help_for("attempts per quality check (1..20; default %(default)s)", "quality"))
    ap.add_argument("--eval-min-pass-rate", type=float, default=1.0,
                    help=help_for("minimum attempt pass rate (0..1; default %(default)s)", "quality"))
    capacity_output_flags = ("--json-out", "--output") if workload == "capacity" else ("--json-out",)
    ap.add_argument(*capacity_output_flags, dest="json_out", default=None,
                    help=help_for("write the capacity artifact atomically", "capacity"))
    # --- GENERATE a serve recipe as a side effect of benchmarking a live serve ------
    # (READ them back with `anvil-serving models recipes list|show`.) All optional.
    ap.add_argument("--recipe-out", default=None,
                    help=help_for("after the run, record a [[recipe]] block: PATH to append to the "
                         "serve-recipe registry, or '-' for stdout. Captures the live serve's "
                         "reproducible docker config + THIS run's measured numbers.", "legacy"))
    ap.add_argument("--recipe-from-container", default=None, metavar="NAME",
                    help=help_for("docker container to capture for legacy recipe output", "legacy"))
    ap.add_argument("--recipe-fit", default=None, metavar="CSV",
                    help=help_for("workload-fit labels retained with the recipe", "capacity"))
    ap.add_argument("--recipe-status", default="verified",
                    help=help_for("legacy recipe provenance status", "legacy"))
    ap.add_argument("--recipe-model", default=None, metavar="NAME",
                    help=help_for("legacy recipe model", "legacy"))
    # --- Fast-tier bakeoff evidence mode: target an already-loaded endpoint -----
    ap.add_argument("--bakeoff", action="store_true",
                    help=help_for("legacy quality-mode selector", "legacy"))
    ap.add_argument("--candidate-id", default=None,
                    help=help_for("candidate identifier recorded in quality evidence", "quality"))
    ap.add_argument("--config-id", default=None,
                    help=help_for("serve/config identifier recorded in quality evidence", "quality"))
    ap.add_argument("--context-targets", default="32768",
                    help=help_for("comma-separated quality context targets", "quality"))
    ap.add_argument("--suite", action="append",
                    help=help_for("repeatable/comma-separated: chat, context, tool, session, "
                                  "intelligence, voice", "quality"))
    ap.add_argument("--suite-file", default=None, metavar="SPECS_JSON",
                    help=help_for("externally-authored quality suite; runs only that suite unless "
                                  "--suite also selects built-in checks", "quality"))
    quality_output_flags = ("--evidence-out", "--output") if workload == "quality" else ("--evidence-out",)
    ap.add_argument(*quality_output_flags, dest="evidence_out", default=None,
                    help=help_for("write the quality artifact atomically", "quality"))
    ap.add_argument("--notebook", default=None,
                    help=help_for("also append the run to this bakeoff notebook", "quality"))
    ap.add_argument("--notebook-task", default=None,
                    help=help_for("notebook task key", "quality"))
    ap.add_argument("--notebook-hardware", default=None,
                    help=help_for("notebook hardware key", "quality"))
    ap.add_argument("--source-recipe", default=None,
                    help=help_for("immutable recipe/config source reference", "quality"))
    ap.add_argument(
        "--control-status",
        choices=("verified", "supported", "requested_unverified"),
        help=help_for(
            "verification state for an explicit thinking/reasoning control; verified or "
            "supported requires --control-evidence", "quality"
        ),
    )
    ap.add_argument(
        "--control-evidence",
        help=help_for("stable path or URL proving the declared thinking-control status",
                      "quality"),
    )
    ap.add_argument("--serve-command", default=None,
                    help=help_for("serve command recorded for reproduction", "quality"))
    ap.add_argument("--voice-latency-ms", type=float, default=None,
                    help=help_for("external total voice latency in milliseconds", "quality"))
    ap.add_argument("--stt-latency-ms", type=float, default=None,
                    help=help_for("external STT latency in milliseconds", "quality"))
    ap.add_argument("--tts-latency-ms", type=float, default=None,
                    help=help_for("external TTS latency in milliseconds", "quality"))
    ap.add_argument("--timeout", "--timeout-seconds", dest="timeout", type=float, default=900.0,
                    help="request timeout in seconds (default %(default)s)")
    ap.add_argument(
        "--dry-run", action="store_true",
        help="validate and print the resolved workload without sending requests or writing",
    )
    a = ap.parse_args(argv)
    if workload == "quality":
        a.bakeoff = True
    elif workload == "capacity":
        a.bakeoff = False
    canonical_forbidden = {
        "capacity": {
            "--bakeoff", "--suite", "--suite-file", "--candidate-id", "--config-id",
            "--evidence-out", "--notebook", "--voice-latency-ms", "--stt-latency-ms",
            "--tts-latency-ms", "--control-status", "--control-evidence",
            "--eval-repetitions", "--eval-min-pass-rate", "--visible-answer-tokens",
            "--reasoning-headroom-tokens", "--context-targets", "--source-recipe",
            "--serve-command", "--notebook-task", "--notebook-hardware",
        },
        "quality": {
            "--bakeoff", "--requests", "--concurrency", "--burst", "--ctx-tokens",
            "--json-out", "--recipe-out", "--recipe-from-container", "--recipe-fit",
            "--recipe-status", "--recipe-model", "--seed", "--response-words",
            "--prompt-cache-mode", "--request-canaries", "--controlled-output-policy",
            "--clock-domain-id", "--configuration-fingerprint",
        },
    }
    if workload is not None:
        supplied_flags = {token.partition("=")[0] for token in argv if token.startswith("--")}
        forbidden = sorted(supplied_flags & canonical_forbidden[workload])
        if forbidden:
            ap.error("%s does not accept %s" % (workload, ", ".join(forbidden)))
    for label, target in (
            ("JSON output", a.json_out),
            ("evidence output", a.evidence_out),
            ("recipe output", a.recipe_out),
            ("notebook", a.notebook)):
        try:
            _validate_write_target(target, label=label)
        except OSError as exc:
            ap.error(str(exc))
    from ..eval import resolve_endpoint_target
    try:
        a.base_url, a.model, selected = resolve_endpoint_target(
            tier=a.tier,
            manifest=a.manifest,
            base_url=a.base_url,
            model=a.model,
            recipe=a.recipe,
            registry=a.registry,
        )
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    if selected:
        a.engine = a.engine or selected.get("engine")
        a.gpu = a.gpu or selected.get("gpu_role")
        if a.source_recipe is None and a.tier:
            a.source_recipe = "%s#%s" % (a.manifest or "resolved-manifest", a.tier)
        elif a.source_recipe is None and a.recipe:
            a.source_recipe = selected.get("source_recipe")
    try:
        api_key = resolve_api_key(a.api_key_env)
    except ValueError as exc:
        ap.error(str(exc))

    visible_answer_tokens = (
        a.visible_answer_tokens if a.visible_answer_tokens is not None else 256
    )
    reasoning_headroom_tokens = (
        a.reasoning_headroom_tokens if a.reasoning_headroom_tokens is not None else 0
    )
    if not 0 < visible_answer_tokens <= _MAX_QUALITY_COMPLETION_TOKENS:
        ap.error(
            "--visible-answer-tokens must be from 1 through %d"
            % _MAX_QUALITY_COMPLETION_TOKENS
        )
    if not 0 <= reasoning_headroom_tokens <= _MAX_QUALITY_COMPLETION_TOKENS:
        ap.error(
            "--reasoning-headroom-tokens must be from 0 through %d"
            % _MAX_QUALITY_COMPLETION_TOKENS
        )
    if (visible_answer_tokens + reasoning_headroom_tokens
            > _MAX_QUALITY_COMPLETION_TOKENS):
        ap.error(
            "visible-answer plus reasoning-headroom allocation cannot exceed %d"
            % _MAX_QUALITY_COMPLETION_TOKENS
        )
    if not 0 < a.eval_repetitions <= _MAX_EVAL_REPETITIONS:
        ap.error("--eval-repetitions must be from 1 through %d" % _MAX_EVAL_REPETITIONS)
    if not math.isfinite(a.eval_min_pass_rate) or not 0 < a.eval_min_pass_rate <= 1:
        ap.error("--eval-min-pass-rate must be greater than 0 and at most 1")
    explicit_thinking = a.no_thinking or a.thinking_mode not in (None, "default")
    if a.reasoning_effort is not None and explicit_thinking:
        ap.error(
            "--reasoning-effort cannot be combined with --no-thinking or an explicit "
            "--thinking-mode"
        )
    if a.control_status in {"verified", "supported"} and not a.control_evidence:
        ap.error("--control-status verified/supported requires --control-evidence")
    if a.control_status is not None and not (
            explicit_thinking or a.reasoning_effort is not None):
        ap.error("--control-status requires an explicit thinking or reasoning control")
    if a.control_evidence and a.control_status not in {"verified", "supported"}:
        ap.error("--control-evidence requires --control-status verified or supported")
    try:
        validate_reasoning_control(
            a.model,
            thinking_mode=a.thinking_mode,
            no_thinking=a.no_thinking,
            reasoning_effort=a.reasoning_effort,
        )
    except ValueError as exc:
        ap.error(str(exc))
    a.control_evidence_sha256 = None
    if a.control_evidence:
        mechanism = (
            "reasoning_effort" if a.reasoning_effort is not None
            else "chat_template_kwargs"
        )
        try:
            a.control_evidence, a.control_evidence_sha256 = load_control_evidence(
                a.control_evidence,
                status=a.control_status,
                mechanism=mechanism,
            )
        except ValueError as exc:
            ap.error("--control-evidence: %s" % exc)

    if a.suite_file and not a.bakeoff:
        ap.error("--suite-file requires --bakeoff (it runs through the bakeoff evidence engine)")
    if not math.isfinite(a.timeout) or not 0 < a.timeout <= 3600:
        ap.error("--timeout must be greater than 0 and at most 3600 seconds")
    for flag, value in (
        ("--voice-latency-ms", a.voice_latency_ms),
        ("--stt-latency-ms", a.stt_latency_ms),
        ("--tts-latency-ms", a.tts_latency_ms),
    ):
        if value is not None and (not math.isfinite(value) or value < 0):
            ap.error("%s must be a finite, non-negative number" % flag)
    if a.max_model_len and a.max_model_len <= a.max_tokens + a.margin:
        ap.error("--max-model-len must exceed --max-tokens plus --margin")

    # Dry-run plans are an operator contract, not just a request-count preview.
    # Resolve the effective control through the same helper used by live runs so
    # a reviewer can prove which reasoning mode will be sent before any request.
    _, _, planned_thinking = resolve_thinking_settings(a)

    if a.bakeoff:
        known_suites = {"chat", "context", "tool", "session", "intelligence", "voice"}
        selected_suites = parse_csv(
            a.suite,
            default=[] if a.suite_file or workload == "quality" else ["chat"],
        )
        if workload == "quality" and not selected_suites and not a.suite_file:
            ap.error("quality requires --suite-file or at least one explicit --suite")
        unknown_suites = sorted(set(selected_suites) - known_suites)
        if unknown_suites:
            ap.error("--suite: unknown value(s): %s" % ", ".join(unknown_suites))
        if "voice" in selected_suites and a.voice_latency_ms is None:
            ap.error("--suite voice requires --voice-latency-ms")
        if not a.candidate_id or not a.config_id:
            ap.error(
                "quality requires --candidate-id and --config-id"
                if workload == "quality"
                else "--bakeoff requires --candidate-id and --config-id"
            )
        a.suite_spec = None
        if a.suite_file:
            # validate BEFORE any request is sent: a malformed spec is an operator
            # error (exit 2 + message), never partial evidence.
            try:
                a.suite_spec = load_suite_spec(a.suite_file)
            except (OSError, ValueError) as exc:
                ap.error("--suite-file: %s" % exc)
        if a.notebook:
            if not a.notebook_task or not a.notebook_hardware:
                ap.error("--notebook requires --notebook-task and --notebook-hardware")
            if not a.suite_spec:
                ap.error(
                    "--notebook requires an explicit ranking --suite-file with a "
                    "strong validator"
                )
            if (a.suite_spec.get("evidence_use") != "ranking"
                    or a.suite_spec.get("validator_strength") not in {
                        "exact_choice", "typed_structure"
                    }):
                ap.error(
                    "--notebook requires evidence_use=ranking and validator_strength "
                    "exact_choice or typed_structure"
                )
        try:
            quality_plan = validate_eval_work_plan(a, a.suite_spec)
        except ValueError as exc:
            ap.error(str(exc))
        if a.dry_run:
            print(json.dumps({
                "schema": "anvil-serving.eval-plan/v1",
                "workload": "quality",
                "target": {
                    "base_url": a.base_url,
                    "model": a.model,
                    "engine": a.engine,
                    "gpu": a.gpu,
                    "tier": a.tier,
                    "manifest": a.manifest,
                },
                "quality": {
                    "suites": selected_suites,
                    "suite_file": a.suite_file,
                    "repetitions": a.eval_repetitions,
                    "minimum_pass_rate": a.eval_min_pass_rate,
                    "visible_answer_tokens": visible_answer_tokens,
                    "reasoning_headroom_tokens": reasoning_headroom_tokens,
                },
                "thinking": planned_thinking,
                "output": a.evidence_out,
                "deferred": ["endpoint identity", "model requests", "artifact write"],
            }, indent=2, sort_keys=True, ensure_ascii=True))
            return 0
        try:
            return run_bakeoff(
                a,
                api_key,
                plan=quality_plan,
                post_request=post_request,
                stream_request=stream_request,
                detect_context_limit=detect_context_limit,
            )
        except ValueError as exc:
            ap.error(str(exc))

    bounds = (
        ("--requests", a.requests, 1, 10000),
        ("--concurrency", a.concurrency, 1, 256),
        ("--burst", a.burst, 0, 256),
        ("--shared-prefix-tokens", a.shared_prefix_tokens, 0, 262144),
        ("--ctx-tokens", a.ctx_tokens, 0, 1048576),
        ("--max-tokens", a.max_tokens, 1, 65536),
        ("--response-words", a.response_words, 0, 65536),
        ("--max-model-len", a.max_model_len, 0, 1048576),
        ("--margin", a.margin, 0, 1048576),
    )
    for flag, value, minimum, maximum in bounds:
        if not minimum <= value <= maximum:
            ap.error("%s must be from %d through %d" % (flag, minimum, maximum))
    if a.ctx_tokens and a.ctx_tokens < 32:
        ap.error("--ctx-tokens must be 0 or at least 32")
    if a.request_canaries and a.max_tokens < 16:
        ap.error("--request-canaries requires --max-tokens of at least 16")
    if a.controlled_output_policy == "strict" and not a.response_words:
        ap.error("--controlled-output-policy strict requires --response-words")
    if a.clock_domain_id and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", a.clock_domain_id):
        ap.error("--clock-domain-id must use 1..64 ASCII letters, digits, dot, dash, or underscore")
    if a.configuration_fingerprint and not re.fullmatch(
        r"sha256:[0-9a-f]{64}", a.configuration_fingerprint
    ):
        ap.error("--configuration-fingerprint must be sha256 followed by 64 lowercase hex digits")
    if a.burst and a.prompt_cache_mode == "unique":
        ap.error("--burst requires --prompt-cache-mode shared")
    effective_shared_prefix_tokens = (
        a.shared_prefix_tokens if a.prompt_cache_mode == "shared" else 0
    )
    if a.dry_run:
        print(json.dumps({
            "schema": "anvil-serving.eval-plan/v1",
            "workload": "capacity",
            "target": {
                "base_url": a.base_url,
                "model": a.model,
                "engine": a.engine,
                "gpu": a.gpu,
                "tier": a.tier,
                "manifest": a.manifest,
            },
            "capacity": {
                "requests": a.burst or a.requests,
                "concurrency": a.burst or a.concurrency,
                "context_tokens": a.ctx_tokens or "measured-distribution",
                "context_seed": a.seed,
                "max_tokens": a.max_tokens,
                "response_words": a.response_words,
                "prompt_cache_mode": a.prompt_cache_mode,
                "shared_prefix_tokens": effective_shared_prefix_tokens,
                "request_canaries": bool(a.request_canaries),
                "controlled_output_policy": a.controlled_output_policy,
                "clock_domain_id": a.clock_domain_id,
                "configuration_fingerprint": a.configuration_fingerprint,
                "timeout_seconds": a.timeout,
            },
            "thinking": planned_thinking,
            "output": a.json_out,
            "deferred": ["endpoint identity", "context-window probe", "requests", "artifact write"],
        }, indent=2, sort_keys=True, ensure_ascii=True))
        return 0

    # Resolve the serve's context window: explicit flag wins; else best-effort probe /v1/models.
    max_model_len = a.max_model_len or detect_context_limit(
        a.base_url, a.model, api_key
    )
    try:
        cap = ctx_cap(max_model_len, a.max_tokens, a.margin)
    except ValueError as exc:
        ap.error(str(exc))
    ctk, reasoning_effort, thinking = resolve_thinking_settings(a)
    control_kwargs = _request_control_kwargs(ctk, reasoning_effort)

    n = a.burst if a.burst else a.requests
    conc = a.burst if a.burst else a.concurrency
    planned_context = a.ctx_tokens or min(cap or 262144, 262144)
    if n * planned_context > _MAX_TOTAL_CAPACITY_PROMPT_TOKENS:
        ap.error(
            "capacity workload exceeds %d aggregate prompt tokens; reduce "
            "--requests/--burst or --ctx-tokens"
            % _MAX_TOTAL_CAPACITY_PROMPT_TOKENS
        )
    shared = (
        make_shared_prefix(effective_shared_prefix_tokens)
        if a.prompt_cache_mode == "shared"
        else ""
    )
    rng = random.Random(a.seed)
    planned_contexts = [
        clamp_ctx(a.ctx_tokens or sample_ctx(rng.random()), cap)
        for _ in range(n)
    ]
    canaries = [f"ANVIL_REQ_{a.seed}_{i:05d}" for i in range(n)]

    def run_request(i):
        ctx = planned_contexts[i]
        request_started = time.perf_counter()
        result = None

        def failed(exc, failure_class):
            message = str(exc) or "<empty exception message>"
            failure = {
                "request_index": i,
                "planned_context_tokens": ctx,
                "failure_class": failure_class,
                "error_type": type(exc).__name__,
                "error_message": message[:2048],
                "error_message_truncated": len(message) > 2048,
                "response_received": isinstance(result, dict),
                "performance_eligible": False,
                "elapsed_ms": (time.perf_counter() - request_started) * 1000.0,
            }
            if isinstance(result, dict):
                safe_result = {
                    **result,
                    "request_index": i,
                    "planned_context_tokens": ctx,
                }
                timing = _result_timings([safe_result])
                failure["response_timing"] = timing[0] if timing else None
                usage = result.get("usage")
                failure["response_metadata"] = {
                    "output_token_source": result.get("output_token_source"),
                    "content_chunks": result.get("content_chunks"),
                    "reasoning_chunks": result.get("reasoning_chunks"),
                    "finish_reasons": result.get("finish_reasons") or [],
                    "stream_terminal_observed": result.get("stream_terminal_observed"),
                    "visible_content_truncated": bool(
                        result.get("visible_content_truncated")
                    ),
                    "usage": {
                        key: usage.get(key)
                        for key in (
                            "prompt_tokens", "completion_tokens", "total_tokens"
                        )
                        if isinstance(usage, dict) and usage.get(key) is not None
                    },
                }
                if result.get("request_canary") is not None:
                    failure["request_canary"] = result["request_canary"]
                if result.get("controlled_output") is not None:
                    failure["controlled_output"] = result["controlled_output"]
            return {"failure": failure}

        try:
            prompt = make_prompt(
                shared,
                ctx,
                i,
                max_prompt_tokens=cap,
                marker=canaries[i] if a.request_canaries else None,
                response_words=a.response_words,
                unique_prefix=a.prompt_cache_mode == "unique",
            )
            result = stream_request(
                a.base_url, a.model, prompt, api_key, a.max_tokens,
                timeout=a.timeout, **control_kwargs,
            )
        except Exception as exc:
            return failed(exc, "request_error")

        result = {
            **result,
            "request_index": i,
            "planned_context_tokens": ctx,
        }
        try:
            validate_stream_result(result)
        except Exception as exc:
            return failed(exc, "stream_validation_failed")

        observations = output_contract_observation(
            result.get("visible_content"),
            expected_marker=canaries[i] if a.request_canaries else None,
            response_words=a.response_words,
            controlled_output_policy=a.controlled_output_policy,
            visible_content_truncated=bool(result.get("visible_content_truncated")),
        )
        result.update(observations)
        if a.request_canaries and not result["request_canary"]["passed"]:
            return failed(
                ValueError("request canary validation failed"),
                "request_canary_failed",
            )
        if (
            a.controlled_output_policy == "strict"
            and result.get("controlled_output")
            and not result["controlled_output"]["passed"]
        ):
            return failed(
                ValueError("controlled output validation failed"),
                "controlled_output_failed",
            )
        return {"result": result}

    capnote = f" max_model_len={max_model_len}(ctx<={cap})" if cap is not None else ""
    thinknote = "" if thinking["mode"] == "default" else f" thinking={thinking['mode']}"
    if reasoning_effort is not None:
        thinknote += f" reasoning_effort={reasoning_effort}"
    print(_console_safe(
        f"BENCH {a.base_url} model={a.model}  n={n} concurrency={conc} "
        f"{'BURST(shared-prefix)' if a.burst else 'mixed'} "
        f"max_tokens={a.max_tokens} cache={a.prompt_cache_mode} "
        f"canaries={bool(a.request_canaries)} response_words={a.response_words}"
        f"{capnote}{thinknote}"
    ))
    started_at_unix_ns = time.time_ns()
    started_at = started_at_unix_ns / 1_000_000_000
    t0 = time.perf_counter()
    results = []
    failures = []
    with ThreadPoolExecutor(max_workers=conc) as ex:
        # Each worker builds at most one prompt, so memory scales with
        # concurrency rather than request count.
        futures = {ex.submit(run_request, i): i for i in range(n)}
        for future in as_completed(futures):
            try:
                outcome = future.result()
                if outcome.get("result") is not None:
                    results.append(outcome["result"])
                else:
                    failure = outcome["failure"]
                    failures.append(failure)
                    print(
                        "  req error: %s: %s"
                        % (failure["error_type"], failure["error_message"])
                    )
            except Exception as exc:
                error_message = str(exc) or "<empty exception message>"
                failure = {
                    "request_index": futures[future],
                    "error_type": type(exc).__name__,
                    "error_message": error_message[:2048],
                    "error_message_truncated": len(error_message) > 2048,
                    "response_received": False,
                    "performance_eligible": False,
                    "failure_class": "runner_error",
                }
                failures.append(failure)
                print(
                    "  req error: %s: %s"
                    % (failure["error_type"], failure["error_message"])
                )
    wall = time.perf_counter() - t0
    finished_at_unix_ns = time.time_ns()
    finished_at = finished_at_unix_ns / 1_000_000_000
    metrics = _result_metrics(results)
    request_timings = _result_timings(results)
    out_tot = metrics["output_tokens"]
    performance_eligible = len(results) == n and not failures
    throughput_eligible = bool(performance_eligible and out_tot is not None and wall)
    cfs = [cached_fraction(r.get("usage")) for r in results]
    cfs = [c for c in cfs if c is not None]
    context_distribution = dict(Counter(str(tokens) for tokens in planned_contexts))
    token_sources = dict(Counter(
        result.get("output_token_source", "unknown") for result in results
    ))
    p99_sample_counts = {
        name: metrics[f"{name}_samples"]
        for name in (
            "ttft", "e2e", "effective_prefill_tok_s", "decode_tok_s", "tpot"
        )
    }
    print("-"*60)
    print(f"completed:        {len(results)}/{n} in {wall:.1f}s")
    p99_note = (
        "tail estimate; N>=100" if len(results) >= 100
        else "descriptive only; N<100"
    )
    if metrics["reasoning_chunks"]:
        print(
            "TTFO  p50/p95:    %.2fs / %.2fs (reasoning or visible)"
            % (
                metrics["time_to_first_output_p50_ms"] / 1000.0,
                metrics["time_to_first_output_p95_ms"] / 1000.0,
            )
        )
    if metrics["ttft_p50_ms"] is not None:
        print(
            "TTFT  mean/p50/p95/p99 (%s): %.2fs / %.2fs / %.2fs / %.2fs"
            % (p99_note, metrics["ttft_mean_ms"] / 1000.0, metrics["ttft_p50_ms"] / 1000.0,
               metrics["ttft_p95_ms"] / 1000.0, metrics["ttft_p99_ms"] / 1000.0)
        )
    if metrics["e2e_p50_ms"] is not None:
        print(
            "E2E   mean/p50/p95/p99 (%s): %.2fs / %.2fs / %.2fs / %.2fs"
            % (p99_note, metrics["e2e_mean_ms"] / 1000.0, metrics["e2e_p50_ms"] / 1000.0,
               metrics["e2e_p95_ms"] / 1000.0, metrics["e2e_p99_ms"] / 1000.0)
        )
    if metrics["generation_p50_ms"] is not None:
        print(
            "GEN   p50/p95:    %.2fs / %.2fs"
            % (
                metrics["generation_p50_ms"] / 1000.0,
                metrics["generation_p95_ms"] / 1000.0,
            )
        )
    if metrics["effective_prefill_tok_s_p50"] is not None:
        print(
            "prefill mean/p50/p95/p99: %.0f / %.0f / %.0f / %.0f effective input tok/s"
            % (
                metrics["effective_prefill_tok_s_mean"],
                metrics["effective_prefill_tok_s_p50"],
                metrics["effective_prefill_tok_s_p95"],
                metrics["effective_prefill_tok_s_p99"],
            )
        )
    if metrics["decode_tok_s_p50"] is not None:
        print(
            "decode mean/p50/p95/p99: %.1f / %.1f / %.1f / %.1f tok/s per request"
            % (metrics["decode_tok_s_mean"], metrics["decode_tok_s_p50"],
               metrics["decode_tok_s_p95"], metrics["decode_tok_s_p99"])
        )
    if metrics["completion_tokens_p50"] is not None:
        print(
            "completion tokens mean/p50/p95/p99: %.1f / %.0f / %.0f / %.0f"
            % (metrics["completion_tokens_mean"], metrics["completion_tokens_p50"],
               metrics["completion_tokens_p95"], metrics["completion_tokens_p99"])
        )
    if throughput_eligible:
        print(f"throughput:       {(out_tot / wall if wall else 0.0):.0f} output tok/s (aggregate)")
    elif out_tot is not None:
        print("throughput:       withheld (incomplete request population)")
    else:
        print("throughput:       unavailable (endpoint omitted exact token usage)")
    summary = {
        "schema": "anvil-serving.benchmark/v1",
        "measurement_protocol": (
            "capacity-v4-reasoning" if metrics["reasoning_chunks"] else "capacity-v3"
        ),
        "run_id": time.strftime("benchmark-%Y%m%dT%H%M%SZ", time.gmtime(started_at)),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(finished_at)),
        "started_at_unix_ns": started_at_unix_ns,
        "finished_at_unix_ns": finished_at_unix_ns,
        "timestamp_precision": "nanosecond-unix-fields; ISO labels are second precision",
        "clock_domain_id": a.clock_domain_id,
        "configuration_fingerprint": a.configuration_fingerprint,
        "wall_clock_ms": wall * 1000.0,
        "base_url": a.base_url,
        "model": a.model,
        "engine": a.engine,
        "gpu": a.gpu,
        "tier": a.tier,
        "manifest": a.manifest,
        "source_recipe": a.source_recipe,
        "requests": n,
        "completed": len(results),
        "responses_received": len(results) + sum(
            bool(item.get("response_received")) for item in failures
        ),
        "failed": len(failures),
        "failures": sorted(failures, key=lambda item: item["request_index"]),
        "concurrency": conc,
        "context_tokens": a.ctx_tokens or None,
        "context_seed": a.seed,
        "context_distribution": context_distribution,
        "max_context_tokens": max_model_len,
        "max_tokens": a.max_tokens,
        "response_words": a.response_words,
        "prompt_cache_mode": a.prompt_cache_mode,
        "shared_prefix_tokens": effective_shared_prefix_tokens,
        "request_canaries": bool(a.request_canaries),
        "controlled_output_policy": a.controlled_output_policy,
        "performance_eligible": performance_eligible,
        "metric_population": {
            "attempted_requests": n,
            "successful_requests": len(results),
            "performance_eligible_requests": len(results),
            "excluded_requests": len(failures),
            "aggregate_throughput_eligible": throughput_eligible,
            "p99_minimum_recommended_samples": 100,
            "p99_interpretation": (
                "tail-estimate" if len(results) >= 100
                else "descriptive-nearest-rank-only"
            ),
            "p99_samples_by_metric": p99_sample_counts,
            "p99_interpretation_by_metric": {
                name: (
                    "tail-estimate" if samples >= 100
                    else "descriptive-nearest-rank-only"
                )
                for name, samples in p99_sample_counts.items()
            },
            "failure_policy": (
                "failed or contract-invalid responses retain sanitized timing and "
                "metadata but are excluded from performance distributions"
            ),
        },
        "serve_flags": {
            "shared_prefix_burst": bool(a.burst),
            "no_thinking": bool(a.no_thinking),
            "thinking_mode": thinking["mode"],
            "reasoning_effort": reasoning_effort,
        },
        "timing_methodology": {
            "clock": "client time.perf_counter",
            "artifact_timestamps": (
                "time.time_ns Unix timestamps; independent artifacts may align them "
                "only when the operator supplied the same explicit clock_domain_id"
            ),
            "ttft": (
                "request start through the first non-empty streamed content delta"
            ),
            "time_to_first_output": (
                "request start through the first non-empty streamed reasoning or "
                "content delta"
            ),
            "effective_prefill": (
                "usage.prompt_tokens divided by client-observed time to first "
                "output; includes queueing, scheduling, prefill, and first-token work"
            ),
            "generation": "client-observed E2E minus time to first output",
            "decode": (
                "usage completion tokens after the first token divided by "
                "client-observed generation time; completion tokens may include "
                "reasoning tokens"
            ),
            "mean_inter_token_latency": (
                "legacy key for a TPOT proxy: client-observed generation time divided "
                "by completion tokens after the first token; not raw per-token ITL"
            ),
            "tpot": (
                "client-observed mean time per output token after the first output; "
                "identical to the legacy mean_inter_token_latency key"
            ),
            "distributions": (
                "request-level mean, sample standard deviation, normal-approximation "
                "95% confidence interval for the mean, min, p25, p50, p75, p90, "
                "p95, p99, and max using the nearest-rank percentile method"
            ),
            "p99": (
                "numeric nearest-rank p99 is always retained for compatibility; it is "
                "descriptive only below 100 eligible samples"
            ),
            "request_canaries": (
                "when enabled, each response must begin with its own synthetic marker "
                "and contain no other ANVIL_REQ marker; bounded/truncated captures fail "
                "closed and are excluded from performance metrics"
            ),
            "controlled_output": (
                "--response-words adherence is recorded; observe preserves historical "
                "behavior while strict rejects non-exact or truncated completions; "
                f"visible response capture is capped at {VISIBLE_CONTENT_CAPTURE_LIMIT} characters"
            ),
        },
        "request_timings": request_timings,
        "metrics": {
            **metrics,
            "throughput_tok_s": (
                (out_tot / wall) if throughput_eligible else None
            ),
            "throughput_performance_eligible": throughput_eligible,
            "content_chunks_s": (
                metrics["content_chunks"] / wall
                if out_tot is None and wall else None
            ),
            "output_token_sources": token_sources,
            "prefix_cache_hit_avg": statistics.mean(cfs) if cfs else None,
        },
    }
    if cfs:
        print(f"prefix-cache hit: {statistics.mean(cfs)*100:.1f}% avg cached prompt tokens (KEY KPI)")
    else:
        print("prefix-cache hit: endpoint did not return prompt_tokens_details.cached_tokens")
    print("-"*60)
    print("Tip: run once cold, then immediately again -- TTFT should drop sharply on the 2nd run if prefix cache works.")
    if a.json_out:
        _atomic_write_json(a.json_out, summary)
        print("wrote JSON summary: " + a.json_out)
    if len(results) != n:
        if a.recipe_out:
            print(
                "skipping serve recipe: benchmark completed %d/%d requests" % (len(results), n),
                file=sys.stderr,
            )
        return 1
    # Benchmarking a serve ALSO records its reproducible recipe when asked.
    if a.recipe_out:
        emit_recipe(a, summary)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
