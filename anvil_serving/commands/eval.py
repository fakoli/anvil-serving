"""Command declarations for the eval family."""

from .family import command_family
from .common import CONFIRM_OPTIONS
from .spec import CommandNode, _node, _handler, _option, _remote, _resource_node


_JOB_READ_OPTIONS = (
    _option("--run-id", summary="Portable benchmark run identifier.", value_name="ID"),
)


def _benchmark_suite(name: str, summary: str) -> CommandNode:
    common_remote = {"fixed": (("suite", name),)}
    return _node(
        name,
        summary,
        handler=_handler(
            "anvil_serving.benchmarking.jobs_cli", argv_prefix=(name, "plan")
        ),
        options=(
            _option(
                "--profile",
                summary="Versioned benchmark profile.",
                value_name="smoke|scout|deep",
            ),
            _option(
                "--observed-context",
                summary="Observed model context capacity used to enforce output headroom.",
                value_name="TOKENS",
            ),
        ),
        children=(
            _resource_node(
                "preflight",
                f"Validate the endpoint and worker for a {name} benchmark job.",
                "anvil_serving.benchmarking.jobs_cli",
                role="evaluation",
                options=(
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
                options=CONFIRM_OPTIONS + (
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
                options=_JOB_READ_OPTIONS + CONFIRM_OPTIONS,
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
                options=_JOB_READ_OPTIONS,
                argv_prefix=(name, "artifact"),
                remote_operation=_remote(
                    "benchmark_job_artifact",
                    allowed=("run_id",),
                    **common_remote,
                ),
            ),
        ),
        docs_anchor="docs/cli/eval.md#benchmark",
    )


@command_family(category="Quality loop")
def commands() -> CommandNode:
    return _node(
        "eval",
        "Run quality evaluation workflows.",
        children=(
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
                        "tool_batch",
                        "checks",
                        "image_path",
                        "image_expect",
                        "ocr_expect",
                        "video_path",
                        "video_expect",
                        "no_thinking",
                        "thinking_mode",
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
            _node(
                "benchmark",
                "Run or import benchmark evidence.",
                children=(
                    _benchmark_suite(
                        "context", "Measure retrieval and reasoning degradation by context depth."
                    ),
                    _benchmark_suite(
                        "agentic", "Run deterministic tool-use and software-solving scenarios."
                    ),
                    _benchmark_suite(
                        "swe", "Run pinned repository problem-solving benchmarks."
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
