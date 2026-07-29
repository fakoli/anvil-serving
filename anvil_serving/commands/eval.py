"""Command declarations for the eval family."""

from .family import command_family
from .common import CONFIRM_OPTIONS
from .spec import CommandNode, _node, _handler, _remote, _resource_node


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
