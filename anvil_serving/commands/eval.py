"""Command declarations for the eval family."""

from ..benchmarking.profiles import PROFILE_NAMES
from .family import command_family
from .common import CONFIRM_OPTIONS
from .spec import CommandNode, _node, _handler, _option, _remote, _resource_node


_JOB_READ_OPTIONS = (
    _option("--run-id", summary="Portable benchmark run identifier.", value_name="ID"),
)


def _benchmark_suite(name: str, summary: str) -> CommandNode:
    # A 256 KiB spec plus 1 MiB retained logs is returned twice by operation status.
    common_remote = {"fixed": (("suite", name),), "max_response_bytes": 4 * 1024 * 1024}
    # --dry-run is inherited from the suite's plan surface.
    confirm_options = tuple(option for option in CONFIRM_OPTIONS if "--dry-run" not in option.flags)
    return _node(
        name,
        summary,
        handler=_handler(
            "anvil_serving.benchmarking.jobs_cli", argv_prefix=(name, "plan")
        ),
        options=(
            _option("--dry-run", summary="Inspect the plan without requests or writes."),
            _option(
                "--profile",
                summary="Versioned benchmark profile.",
                value_name="|".join(sorted(PROFILE_NAMES)),
            ),
            _option(
                "--observed-context",
                summary="Observed model context capacity used to enforce output headroom.",
                value_name="TOKENS",
            ),
        ),
        children=(
            _resource_node(
                "prepare",
                f"Prepare digest-pinned {name} harness assets on the benchmark worker.",
                "anvil_serving.benchmarking.jobs_cli",
                role="evaluation",
                mutation="mutate",
                options=confirm_options + ((_option("--spec", summary="Client-side Aider selection job spec.", value_name="PATH"),) if name == "aider" else ()) + (
                    _option("--run-id", summary="Portable benchmark run identifier.", value_name="ID"),
                    _option("--ownership-id", summary="Benchmark ownership identifier.", value_name="ID"),
                    _option("--offline", summary="Require exact assets to exist in cache."),
                    _option(
                        "--max-download-bytes",
                        summary="Maximum prepared repository bytes.",
                        value_name="BYTES",
                    ),
                ),
                argv_prefix=(name, "prepare"),
                remote_operation=_remote(
                    "benchmark_harness_prepare",
                    fixed=(("suite", name),),
                    confirmed=(("confirm", True),),
                    allowed=(
                        "profile",
                        "run_id",
                        "ownership_id",
                        "offline",
                        "max_download_bytes",
                    ),
                ),
            ),
            _resource_node(
                "assets",
                f"Inspect prepared {name} harness assets.",
                "anvil_serving.benchmarking.jobs_cli",
                role="evaluation",
                options=(
                    _option("--run-id", summary="Portable benchmark run identifier.", value_name="ID"),
                    _option("--ownership-id", summary="Benchmark ownership identifier.", value_name="ID"),
                ),
                argv_prefix=(name, "assets"),
                remote_operation=_remote(
                    "benchmark_harness_status",
                    fixed=(("suite", name),),
                    allowed=("run_id", "ownership_id"),
                ),
            ),
            *(() if name != "swe" else (_resource_node(
                "images",
                "Preview or prepare only the frozen SWE task images from one job specification.",
                "anvil_serving.benchmarking.jobs_cli",
                role="evaluation",
                mutation="mutate",
                options=confirm_options + (
                    _option("--spec", summary="Frozen SWE job specification JSON file.", value_name="PATH"),
                    _option(
                        "--worker",
                        summary="Local worker ID named by the frozen specification.",
                        value_name="ID",
                    ),
                    _option("--min-free-bytes", summary="Required free disk reserve (minimum 100 GiB).", value_name="BYTES"),
                ),
                argv_prefix=(name, "images"),
                forward_confirm_flag=True,
                execution_host_os=("linux",),
            ),)),
            _resource_node(
                "cleanup",
                f"Clean only the owned work directory for a {name} benchmark run.",
                "anvil_serving.benchmarking.jobs_cli",
                role="evaluation",
                mutation="mutate",
                options=_JOB_READ_OPTIONS
                + confirm_options
                + (
                    _option("--ownership-id", summary="Benchmark ownership identifier.", value_name="ID"),
                ),
                argv_prefix=(name, "cleanup"),
                remote_operation=_remote(
                    "benchmark_harness_cleanup",
                    fixed=(("suite", name),),
                    confirmed=(("confirm", True),),
                    allowed=("run_id", "ownership_id"),
                ),
            ),
            _resource_node(
                "preflight",
                f"Validate the endpoint and worker for a {name} benchmark job.",
                "anvil_serving.benchmarking.jobs_cli",
                role="evaluation",
                options=(
                    _option("--spec", summary="Client-side benchmark job specification JSON file (exclusive with --spec-json).", value_name="PATH"),
                    _option(
                        "--spec-json",
                        summary="Portable benchmark job specification JSON.",
                        value_name="JSON",
                    ),
                    _option(
                        "--requirements-json",
                        summary="Worker and harness requirement JSON.",
                        value_name="JSON",
                    ),
                ),
                argv_prefix=(name, "preflight"),
                remote_operation=_remote(
                    "benchmark_job_preflight",
                    fixed=(("suite", name),),
                    allowed=("spec_json", "requirements_json"),
                ),
            ),
            _resource_node(
                "submit",
                f"Submit a durable {name} benchmark job.",
                "anvil_serving.benchmarking.jobs_cli",
                role="evaluation",
                mutation="mutate",
                options=confirm_options + (
                    _option("--spec", summary="Client-side benchmark job specification JSON file (exclusive with --spec-json).", value_name="PATH"),
                    _option(
                        "--spec-json",
                        summary="Portable benchmark job specification JSON.",
                        value_name="JSON",
                    ),
                    _option("--follow", summary="Follow the submitted job."),
                    _option("--detach", summary="Return after durable submission."),
                ),
                argv_prefix=(name, "submit"),
                remote_operation=_remote(
                    "benchmark_job_submit",
                    confirmed=(("confirm", True),),
                    allowed=("spec_json", "follow", "detach"),
                    **common_remote,
                ),
            ),
            _resource_node(
                "status",
                f"Read durable {name} benchmark job status.",
                "anvil_serving.benchmarking.jobs_cli",
                role="evaluation",
                options=_JOB_READ_OPTIONS,
                argv_prefix=(name, "status"),
                remote_operation=_remote(
                    "benchmark_job_status",
                    allowed=("run_id",),
                    **common_remote,
                ),
            ),
            _resource_node(
                "logs",
                f"Read bounded cursor logs for a {name} benchmark job.",
                "anvil_serving.benchmarking.jobs_cli",
                role="evaluation",
                options=_JOB_READ_OPTIONS + (
                    _option("--cursor", summary="First log cursor.", value_name="N"),
                    _option("--limit", summary="Maximum returned log entries.", value_name="N"),
                    _option("--follow", summary="Continue following job logs.", output_policy="follow"),
                ),
                argv_prefix=(name, "logs"),
                output_policy="follow",
                remote_operation=_remote(
                    "benchmark_job_logs",
                    allowed=("run_id", "cursor", "limit", "follow"),
                    **common_remote,
                ),
            ),
            _resource_node(
                "cancel",
                f"Cancel a {name} benchmark job after recording partial evidence.",
                "anvil_serving.benchmarking.jobs_cli",
                role="evaluation",
                mutation="mutate",
                options=_JOB_READ_OPTIONS + confirm_options,
                argv_prefix=(name, "cancel"),
                remote_operation=_remote(
                    "benchmark_job_cancel",
                    confirmed=(("confirm", True),),
                    allowed=("run_id",),
                    **common_remote,
                ),
            ),
            _resource_node(
                "artifact",
                f"Read the terminal or partial {name} benchmark artifact.",
                "anvil_serving.benchmarking.jobs_cli",
                role="evaluation",
                options=_JOB_READ_OPTIONS + (
                    _option(
                        "--path",
                        summary="Digest-bound stage evidence path from the job artifact.",
                        value_name="PATH",
                    ),
                ),
                argv_prefix=(name, "artifact"),
                remote_operation=_remote(
                    "benchmark_job_artifact",
                    allowed=("run_id", "path"),
                    # A 1 MiB evidence file can expand sixfold through JSON escaping.
                    **{**common_remote, "max_response_bytes": 8 * 1024 * 1024},
                ),
            ),
        ),
        docs_anchor="docs/cli/eval.md#benchmark",
    )


def _campaign() -> CommandNode:
    children = []
    for action, summary in (
        ("plan", "Validate explicit campaign cells without writes or traffic."),
        ("prepare", "Prepare actual assets and freeze child specifications."),
        ("run", "Run one frozen arm with bounded C1 or C4 batches."),
        ("report", "Report independent scores and matched populations."),
        ("cancel", "Persist cancellation and stop only campaign-owned children."),
    ):
        options = (
            (_option("--spec", summary="Explicit ordered campaign specification JSON.", value_name="PATH"),)
            if action in {"plan", "prepare"} else
            (_option("--ownership-id", summary="Campaign owner (or configured ANVIL_BENCHMARK_OWNERSHIP_ID).", value_name="ID"),)
        )
        if action == "run":
            options += (_option("--arm", summary="One frozen arm to run in the foreground.", value_name="ID"),)
        if action == "prepare":
            options += (_option("--offline", summary="Require prepared dependencies in cache."),)
        common = {"options": options, "docs_anchor": "docs/benchmarks/context-agentic-swe.md#frozen-campaigns"}
        if action in {"prepare", "run", "cancel"}:
            common["options"] += (_option("--confirm", summary="Confirm the campaign operation.", requires_confirmation=True),)
            children.append(_resource_node(action, summary, "anvil_serving.benchmarking.campaigns_cli",
                role="evaluation", mutation="mutate", argv_prefix=(action,), forward_confirm_flag=True, **common))
        else:
            children.append(_node(action, summary,
                handler=_handler("anvil_serving.benchmarking.campaigns_cli", argv_prefix=(action,)), **common))
    return _node("campaign", "Prepare and execute frozen batches over durable benchmark jobs.",
                 children=tuple(children), docs_anchor="docs/benchmarks/context-agentic-swe.md#frozen-campaigns")


@command_family(category="Quality loop")
def commands() -> CommandNode:
    return _node(
        "eval",
        "Run quality evaluation workflows.",
        children=(
            _resource_node(
                "runtime-source-inspect",
                "Read fixed accounting source hashes from a bound managed runtime.",
                "anvil_serving.runtime_source_inspection",
                role="model-serve", argv_prefix=(), execution_host_os=("linux",),
                options=(
                    _option("--registry", summary="Managed recipe registry.", value_name="PATH"),
                    _option("--container", summary="Managed recipe container name.", value_name="NAME"),
                    _option("--plan", summary="Fixed profile and expected generation pins.", value_name="PATH"),
                ),
                docs_anchor="docs/cli/runtime-source-inspection.md",
            ),
            _resource_node(
                "reasoning-limit-probe",
                "Preview or execute an isolated, bounded reasoning-boundary diagnostic.",
                "anvil_serving.reasoning_probe",
                role="evaluation", mutation="mutate", argv_prefix=(),
                forward_confirm_flag=True,
                options=(
                    _option("--router-config", summary="Unchanged source router configuration.", value_name="PATH"),
                    _option("--plan", summary="Frozen two-case probe plan.", value_name="PATH"),
                    _option("--boundary-config", summary="Verified model, configuration hash and marker IDs.", value_name="PATH"),
                    _option("--output", summary="New private evidence directory.", value_name="PATH"),
                    _option("--confirm", summary="Send the bounded probe; omission previews offline.", requires_confirmation=True),
                ),
                docs_anchor="docs/benchmarks/reasoning-limit-probe.md",
            ),
            _resource_node(
                "reasoning-limit-inspect",
                "Read pinned GLM runtime hashes and tokenizer markers without inference.",
                "anvil_serving.reasoning_probe_runtime",
                role="model-serve", argv_prefix=(), execution_host_os=("linux",),
                options=(
                    _option("--registry", summary="Managed recipe registry.", value_name="PATH"),
                    _option("--container", summary="Exact running recipe container.", value_name="NAME"),
                    _option("--plan", summary="Frozen runtime pins.", value_name="PATH"),
                ),
                docs_anchor="docs/benchmarks/reasoning-limit-probe.md",
            ),
            _resource_node(
                "usage",
                "Write usage and role summaries from recorded sessions.",
                "anvil_serving.profile",
                role="evaluation",
                options=CONFIRM_OPTIONS,
                mutation="mutate",
                argv_prefix=(),
            ),
            _resource_node(
                "preflight",
                "Preflight an endpoint.",
                "anvil_serving.preflight",
                role="evaluation",
                options=CONFIRM_OPTIONS,
                mutation="mutate",
                argv_prefix=(),
                remote_operation=_remote(
                    "preflight_probe",
                    confirmed=(("confirm", True),),
                    allowed=(
                        "base_url",
                        "model",
                        "api_key_env",
                        "needle_ctx",
                        "temperature",
                        "tool_batch",
                        "tool_choice_mode",
                        "seed",
                        "capture_tool_transport",
                        "checks",
                        "image_path",
                        "image_expect",
                        "ocr_expect",
                        "video_path",
                        "video_expect",
                        "no_thinking",
                        "thinking_mode",
                        "force_nonempty_content",
                        "reasoning_effort",
                        "reasoning_evidence",
                        "visible_answer_tokens",
                        "reasoning_headroom_tokens",
                        "allowed_finish_reasons",
                        "timeout_seconds",
                        "dry_run",
                    ),
                ),
            ),
            _resource_node(
                "routed",
                "Run fail-closed router and real-client acceptance for one alias.",
                "anvil_serving.routed_eval",
                role="gateway",
                options=CONFIRM_OPTIONS + (
                    _option("--base-url", summary="Authenticated router API base URL.", value_name="URL"),
                    _option("--model", summary="Public router alias to evaluate.", value_name="ALIAS"),
                    _option("--api-key-env", summary="Router credential environment variable.", value_name="ENV"),
                    _option("--expected-served-model", summary="Exact served identity expected from router readiness.", value_name="MODEL"),
                    _option("--expected-config-fingerprint", summary="Exact routed configuration fingerprint.", value_name="ID"),
                    _option("--expected-router-config-sha256", summary="Exact router configuration hash expected during reconciliation.", value_name="SHA256"),
                    _option("--min-context-tokens", summary="Minimum routed context capacity.", value_name="TOKENS"),
                    _option("--clients", summary="Comma-separated real clients: openclaw,hermes.", value_name="LIST"),
                    _option("--openclaw-provider", summary="OpenClaw provider selected for the invocation.", value_name="ID"),
                    _option("--hermes-provider", summary="Hermes provider selector for the invocation.", value_name="ID"),
                    _option("--hermes-expected-provider", summary="Provider identity expected in Hermes usage evidence.", value_name="ID"),
                    _option("--no-harness-sync", summary="Skip OpenClaw/Pi catalog reconciliation."),
                    _option("--openclaw-config", summary="OpenClaw catalog to reconcile.", value_name="PATH"),
                    _option("--pi-models", summary="Pi provider catalog to reconcile.", value_name="PATH"),
                    _option("--pi-settings", summary="Pi settings catalog to reconcile.", value_name="PATH"),
                    _option("--client-state-path", summary="Client reconciliation state file.", value_name="PATH"),
                    _option("--client-backup-root", summary="Private client catalog backup directory.", value_name="PATH"),
                    _option("--timeout-seconds", summary="Per-router and per-client timeout.", value_name="SECONDS"),
                    _option("--run-id", summary="Portable routed-evaluation identifier.", value_name="ID"),
                    _option("--output", summary="Atomic JSON evidence destination.", value_name="PATH"),
                ),
                mutation="mutate",
                argv_prefix=(),
                remote_operation=_remote(
                    "routed_eval",
                    confirmed=(("confirm", True),),
                    allowed=(
                        "base_url",
                        "model",
                        "api_key_env",
                        "expected_served_model",
                        "expected_config_fingerprint",
                        "expected_router_config_sha256",
                        "min_context_tokens",
                        "clients",
                        "openclaw_provider",
                        "hermes_provider",
                        "hermes_expected_provider",
                        "no_harness_sync",
                        "openclaw_config",
                        "pi_models",
                        "pi_settings",
                        "client_state_path",
                        "client_backup_root",
                        "timeout_seconds",
                        "run_id",
                        "output",
                        "dry_run",
                    ),
                    max_response_bytes=1024 * 1024,
                ),
                docs_anchor="docs/cli/eval.md#routed-client-evaluation",
            ),
            _node(
                "benchmark",
                "Run or import benchmark evidence.",
                children=(
                    _campaign(),
                    _node(
                        "jev",
                        "Replay selected evidence through a shadow Jev pilot.",
                        handler=_handler("anvil_serving.benchmarking.jev_pilot", argv_prefix=()),
                        options=(
                            _option("--config", summary="Pilot configuration (default: jev-pilot.json).", value_name="PATH"),
                            _option("--allow-export", summary="Permit selected sanitized text export to Jev."),
                            _option("--no-jev", summary="Use baseline selection without provider calls."),
                        ),
                        docs_anchor="docs/benchmarks/jev-pilot.md",
                    ),
                    _resource_node(
                        "stability",
                        "Replay bounded mixed-load scenarios and retain coverage and failure evidence.",
                        "anvil_serving.benchmarking.stability",
                        role="evaluation",
                        options=CONFIRM_OPTIONS + (
                            _option("--scenario", summary="Reviewed stability scenario JSON.", value_name="PATH"),
                            _option("--output", summary="New private evidence artifact.", value_name="PATH"),
                        ),
                        mutation="mutate",
                        argv_prefix=(),
                        forward_confirm_flag=True,
                    ),
                    _benchmark_suite(
                        "context", "Measure retrieval and reasoning degradation by context depth."
                    ),
                    _benchmark_suite(
                        "agentic", "Run deterministic tool-use and software-solving scenarios."
                    ),
                    _benchmark_suite(
                        "swe", "Run pinned repository problem-solving benchmarks."
                    ),
                    _benchmark_suite(
                        "aider", "Run pinned Aider Polyglot edits with contained tests."
                    ),
                    _benchmark_suite(
                        "performance", "Run pinned LIL or AIPerf performance measurements."
                    ),
                    _resource_node(
                        "capacity",
                        "Measure endpoint latency, throughput, context, and cache behavior.",
                        "anvil_serving.benchmark",
                        role="evaluation",
                        options=CONFIRM_OPTIONS,
                        mutation="mutate",
                        argv_prefix=("capacity",),
                    ),
                    _resource_node(
                        "quality",
                        "Run repeated quality suites and retain comparison evidence.",
                        "anvil_serving.benchmark",
                        role="evaluation",
                        options=CONFIRM_OPTIONS,
                        mutation="mutate",
                        argv_prefix=("quality",),
                    ),
                    _resource_node(
                        "multimodal",
                        "Run a hashed deterministic image/video corpus.",
                        "anvil_serving.benchmark",
                        role="evaluation",
                        options=CONFIRM_OPTIONS,
                        mutation="mutate",
                        argv_prefix=("multimodal",),
                    ),
                    _node(
                        "evidence",
                        "Inspect retained local benchmark evidence.",
                        children=(
                            _node(
                                "list",
                                "List retained local benchmark artifacts.",
                                handler=_handler(
                                    "anvil_serving.benchmark_evidence", argv_prefix=("list",)
                                ),
                            ),
                            _node(
                                "show",
                                "Show a normalized benchmark artifact summary.",
                                handler=_handler(
                                    "anvil_serving.benchmark_evidence", argv_prefix=("show",)
                                ),
                            ),
                            _node(
                                "compare",
                                "Compare artifacts and flag workload mismatches.",
                                handler=_handler(
                                    "anvil_serving.benchmark_evidence", argv_prefix=("compare",)
                                ),
                            ),
                        ),
                        docs_anchor="docs/cli/eval.md#benchmark-evidence",
                    ),
                    _node(
                        "report",
                        "Generate or check an offline recipe report from retained artifacts.",
                        handler=_handler("anvil_serving.benchmarking.report", argv_prefix=(), forward_confirm_flag=True),
                        options=(
                            _option("--format", summary="Output format: markdown or json.", value_name="FORMAT"),
                            _option("--root", summary="Root containing the catalog and retained evidence.", value_name="PATH"),
                            _option("--output", summary="Generated report destination; writes require --confirm.", value_name="PATH"),
                            _option("--check", summary="Check existing generated output without writing."),
                            _option("--confirm", summary="Confirm writing the named generated report."),
                        ),
                        docs_anchor="docs/cli/eval.md#benchmark-recipe-report",
                    ),
                    _node(
                        "external",
                        "Manage external benchmark evidence.",
                        children=(
                            _resource_node(
                                "init",
                                "Initialize benchmark evidence storage.",
                                "anvil_serving.external_benchmarks.cli",
                                role="evaluation",
                                options=CONFIRM_OPTIONS,
                                mutation="mutate",
                                argv_prefix=("init",),
                            ),
                            _resource_node(
                                "sources",
                                "List benchmark sources.",
                                "anvil_serving.external_benchmarks.cli",
                                role="evaluation",
                                argv_prefix=("sources",),
                                remote_operation=_remote("external_bench_sources"),
                            ),
                            _resource_node(
                                "fetch",
                                "Fetch and import benchmark evidence.",
                                "anvil_serving.external_benchmarks.cli",
                                role="evaluation",
                                options=CONFIRM_OPTIONS,
                                mutation="mutate",
                                argv_prefix=("fetch",),
                            ),
                            _resource_node(
                                "import",
                                "Import saved benchmark evidence.",
                                "anvil_serving.external_benchmarks.cli",
                                role="evaluation",
                                options=CONFIRM_OPTIONS,
                                mutation="mutate",
                                argv_prefix=("import",),
                            ),
                            _resource_node(
                                "list",
                                "List normalized benchmark evidence.",
                                "anvil_serving.external_benchmarks.cli",
                                role="evaluation",
                                argv_prefix=("list",),
                                remote_operation=_remote("external_bench_list"),
                            ),
                            _resource_node(
                                "report",
                                "Render a benchmark report.",
                                "anvil_serving.external_benchmarks.cli",
                                role="evaluation",
                                argv_prefix=("report",),
                                remote_operation=_remote("external_bench_report"),
                            ),
                            _resource_node(
                                "export",
                                "Export benchmark evidence.",
                                "anvil_serving.external_benchmarks.cli",
                                role="evaluation",
                                options=CONFIRM_OPTIONS,
                                mutation="mutate",
                                argv_prefix=("export",),
                            ),
                            _resource_node(
                                "compare",
                                "Compare local benchmark evidence.",
                                "anvil_serving.external_benchmarks.cli",
                                role="evaluation",
                                argv_prefix=("compare",),
                                remote_operation=_remote("external_bench_compare"),
                            ),
                            _node(
                                "notebook",
                                "Record, list, or render model-bakeoff notebook runs.",
                                children=(
                                    _resource_node(
                                        "add",
                                        "Record a bakeoff evidence run.",
                                        "anvil_serving.external_benchmarks.cli",
                                        role="evaluation",
                                        options=CONFIRM_OPTIONS,
                                        mutation="mutate",
                                        argv_prefix=("notebook", "add"),
                                    ),
                                    _resource_node(
                                        "list",
                                        "List recorded bakeoff runs.",
                                        "anvil_serving.external_benchmarks.cli",
                                        role="evaluation",
                                        argv_prefix=("notebook", "list"),
                                    ),
                                    _resource_node(
                                        "render",
                                        "Render the bakeoff comparison.",
                                        "anvil_serving.external_benchmarks.cli",
                                        role="evaluation",
                                        argv_prefix=("notebook", "render"),
                                    ),
                                ),
                            ),
                        ),
                        docs_anchor="docs/cli/eval.md#external-benchmarks",
                    ),
                ),
                docs_anchor="docs/cli/eval.md#benchmark",
            ),
        ),
        docs_anchor="docs/cli/eval.md",
    )
