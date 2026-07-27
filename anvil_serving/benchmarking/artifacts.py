"""Validation and atomic writes for benchmark artifacts."""

import json
import os
import sys
import tempfile
from typing import Any


class BenchmarkArtifactError(Exception):
    """Typed benchmark artifact validation failure."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def atomic_write_json(path, value):
    """Atomically replace a JSON artifact without leaving a truncated target."""
    out = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(out) or os.getcwd()
    if not os.path.isdir(parent):
        raise OSError("output directory does not exist: %s" % parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=parent,
                prefix=".%s." % os.path.basename(out), suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            json.dump(
                value,
                handle,
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, out)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def validate_write_target(path, *, label="output"):
    """Fail before live work when a requested artifact cannot be replaced safely."""
    if not path or path == "-":
        return None
    out = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(out) or os.getcwd()
    if not os.path.isdir(parent):
        raise OSError("%s directory does not exist: %s" % (label, parent))
    if os.path.islink(out):
        raise OSError("%s path cannot be a symbolic link: %s" % (label, out))
    if os.path.exists(out) and not os.path.isfile(out):
        raise OSError("%s path is not a regular file: %s" % (label, out))
    if not os.access(parent, os.W_OK):
        raise OSError("%s directory is not writable: %s" % (label, parent))
    return out


def console_safe(value):
    """Render a value without failing on a restricted console encoding."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(value).encode(encoding, errors="backslashreplace").decode(encoding)


WORKSPACE_ROOT_ENVS = ("ANVIL_WORKSPACE_ROOT",)


BENCHMARK_EVIDENCE_DIR_ENVS = (
    "ANVIL_BENCHMARK_EVIDENCE_DIR",
    "ANVIL_EVIDENCE_DIR",
)


def real_path(path: str, *, base: str | None = None) -> str:
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(base or os.getcwd(), expanded)
    return os.path.realpath(os.path.abspath(expanded))


def path_is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath(
            [os.path.normcase(path), os.path.normcase(root)]
        ) == os.path.normcase(root)
    except ValueError:
        return False


def is_filesystem_root(path: str) -> bool:
    norm = os.path.normpath(path)
    return os.path.dirname(norm) == norm


def has_workspace_marker(path: str) -> bool:
    pyproject = os.path.join(path, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject, encoding="utf-8") as handle:
                text = handle.read(4096)
        except OSError:
            return False
        if "anvil-serving" in text:
            return True
    readme = os.path.join(path, "README.md")
    if os.path.isfile(readme):
        try:
            with open(readme, encoding="utf-8") as handle:
                text = handle.read(4096)
        except OSError:
            return False
        if "# anvil-serving" in text or "local-model serving" in text:
            return True
    return False


def discover_workspace_root(start: str | None = None) -> str:
    for env_name in WORKSPACE_ROOT_ENVS:
        raw = (os.environ.get(env_name) or "").strip()
        if raw:
            root = real_path(raw)
            if (
                is_filesystem_root(root)
                or not os.path.isdir(root)
                or not has_workspace_marker(root)
            ):
                raise BenchmarkArtifactError(
                    "bad_workspace_root",
                    ("%s must point to an anvil-serving workspace, not a broad filesystem root")
                    % env_name,
                    {"env": env_name, "workspace": root},
                )
            return root

    current = real_path(start or os.getcwd())
    while True:
        if has_workspace_marker(current):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return ""
        current = parent


def configured_benchmark_evidence_roots() -> list[str]:
    roots = []
    for env_name in BENCHMARK_EVIDENCE_DIR_ENVS:
        raw = os.environ.get(env_name, "")
        for item in raw.split(os.pathsep):
            item = item.strip()
            if item:
                root = real_path(item)
                if is_filesystem_root(root):
                    raise BenchmarkArtifactError(
                        "bad_evidence_dir",
                        "%s must not point at a broad filesystem root" % env_name,
                        {"env": env_name, "evidence_dir": root},
                    )
                roots.append(root)
    return roots


def resolve_benchmark_artifact_path(path: str) -> tuple[str, list[str]]:
    if not path:
        raise BenchmarkArtifactError(
            "missing_argument",
            "missing required argument 'artifact_path'",
        )
    if path == "-":
        raise BenchmarkArtifactError(
            "bad_artifact_path",
            "artifact_path must be a file path, not '-'",
        )
    if "\x00" in path:
        raise BenchmarkArtifactError(
            "bad_artifact_path",
            "artifact_path must not contain NUL bytes",
        )

    workspace = discover_workspace_root()
    roots = [workspace] if workspace else []
    roots.extend(root for root in configured_benchmark_evidence_roots() if root not in roots)
    if not roots:
        raise BenchmarkArtifactError(
            "missing_artifact_root",
            ("artifact_path requires an anvil-serving workspace or configured evidence directory"),
            {
                "workspace_envs": list(WORKSPACE_ROOT_ENVS),
                "evidence_dir_envs": list(BENCHMARK_EVIDENCE_DIR_ENVS),
            },
        )

    if os.path.isabs(os.path.expanduser(path)):
        artifact_path = real_path(path)
    elif workspace:
        artifact_path = real_path(path, base=workspace)
    elif len(roots) == 1:
        artifact_path = real_path(path, base=roots[0])
    else:
        raise BenchmarkArtifactError(
            "bad_artifact_path",
            (
                "relative artifact_path requires a workspace when multiple "
                "evidence roots are configured"
            ),
        )
    if not any(path_is_within(artifact_path, root) for root in roots):
        raise BenchmarkArtifactError(
            "unsafe_artifact_path",
            ("artifact_path must be inside the workspace or configured evidence directory"),
            {
                "artifact_path": artifact_path,
                "workspace": workspace or None,
                "evidence_dirs": roots[1:] if workspace else roots,
                "workspace_envs": list(WORKSPACE_ROOT_ENVS),
                "evidence_dir_envs": list(BENCHMARK_EVIDENCE_DIR_ENVS),
            },
        )
    if os.path.isdir(artifact_path):
        raise BenchmarkArtifactError(
            "bad_artifact_path",
            "artifact_path points at a directory",
            {"artifact_path": artifact_path},
        )
    return artifact_path, roots


def benchmark_key_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    metrics = summary.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    keys = (
        "ttft_p50_ms",
        "ttft_p95_ms",
        "e2e_p50_ms",
        "e2e_p95_ms",
        "throughput_tok_s",
        "output_tokens",
        "prefix_cache_hit_avg",
    )
    return {
        "requests": summary.get("requests"),
        "completed": summary.get("completed"),
        "concurrency": summary.get("concurrency"),
        "context_tokens": summary.get("context_tokens"),
        "max_context_tokens": summary.get("max_context_tokens"),
        "max_tokens": summary.get("max_tokens"),
        **{key: metrics.get(key) for key in keys},
    }


def metric_delta(local_value: Any, external_value: Any) -> dict[str, Any]:
    def as_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    local_num = as_float(local_value)
    external_num = as_float(external_value)
    delta_abs = None
    delta_pct = None
    if local_num is not None and external_num not in (None, 0.0):
        delta_abs = local_num - external_num
        delta_pct = (delta_abs / external_num) * 100.0
    return {
        "local": local_num,
        "external": external_num,
        "delta_abs": delta_abs,
        "delta_pct": delta_pct,
    }


def read_benchmark_artifact(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        raise BenchmarkArtifactError(
            "artifact_not_written",
            "benchmark completed but JSON artifact was not written",
            {"artifact_path": path},
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            summary = json.load(f)
    except OSError as exc:
        raise BenchmarkArtifactError(
            "artifact_read_failed",
            str(exc),
            {"artifact_path": path},
        )
    except ValueError as exc:
        raise BenchmarkArtifactError(
            "bad_benchmark_artifact",
            "benchmark artifact is not valid JSON",
            {"artifact_path": path, "error": str(exc)},
        )
    if not isinstance(summary, dict):
        raise BenchmarkArtifactError(
            "bad_benchmark_artifact",
            "benchmark artifact must be a JSON object",
            {"artifact_path": path},
        )
    return summary
