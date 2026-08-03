"""Validation, integrity checks, and atomic writes for benchmark artifacts."""

import copy
import datetime as dt
import hashlib
import ipaddress
import json
import os
import re
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


CROSS_SUITE_EVIDENCE_SCHEMA = "anvil-serving.benchmark-evidence/v1"
EVIDENCE_KINDS = frozenset({"measured", "external_prior"})
EVIDENCE_COMPLETENESS = frozenset({"completed", "incomplete", "failed", "cancelled"})
EVIDENCE_FAILURE_CLASSES = frozenset({
    "routing",
    "authentication",
    "serving_runtime",
    "resource_exhaustion",
    "worker_runtime",
    "harness",
    "model_behavior",
    "grading",
    "cancellation",
})
REQUIRED_MEASURED_IDENTITIES = frozenset({
    "model",
    "served_model",
    "runtime",
    "image",
    "hardware",
    "topology",
    "context",
    "concurrency",
    "harnesses",
    "dataset",
})
PROMOTION_DISCLAIMER = "Benchmark evidence does not authorize model promotion; promotion is a separate human decision."
FAILURE_CLASS_ALIASES = {
    "route_failure": "routing",
    "model_mismatch": "routing",
    "missing_credential": "authentication",
    "authorization_denied": "authentication",
    "model_failure": "model_behavior",
    "reasoning_failure": "model_behavior",
    "protocol_failure": "model_behavior",
    "resource_exhaustion": "resource_exhaustion",
    "timeout": "worker_runtime",
    "infrastructure_failure": "worker_runtime",
    "image_failure": "worker_runtime",
    "broken_harness": "harness",
    "harness_failure": "harness",
    "test_failure": "grading",
    "grader_failure": "grading",
    "cancelled": "cancellation",
}
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEY_RE = re.compile(
    r"(?:authorization|proxy-authorization|api[_-]?key|access[_-]?token|password|secret|credential|cookie)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?:\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{12,}|\bhf_[A-Za-z0-9]{12,}|\bgithub_pat_[A-Za-z0-9_]{12,})",
    re.IGNORECASE,
)
_IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_TAILNET_RE = re.compile(r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net\b")


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


def evidence_file_reference(path: str, *, root: str) -> dict[str, Any]:
    """Return a relative, content-bound reference to retained stage evidence."""
    root_path = real_path(root)
    target = real_path(path)
    if not os.path.isfile(target) or not path_is_within(target, root_path):
        raise BenchmarkArtifactError(
            "unsafe_evidence_reference",
            "evidence reference must be a file within the declared artifact root",
        )
    digest = hashlib.sha256()
    size = 0
    with open(target, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return {
        "path": os.path.relpath(target, root_path).replace("\\", "/"),
        "sha256": digest.hexdigest(),
        "bytes": size,
    }


def normalize_evidence_failure_class(value: str) -> str:
    """Map suite-level failure detail into the stable cross-suite taxonomy."""
    if value in EVIDENCE_FAILURE_CLASSES:
        return value
    normalized = FAILURE_CLASS_ALIASES.get(value)
    if normalized is None:
        raise BenchmarkArtifactError(
            "bad_failure_class", f"unknown benchmark failure class {value!r}"
        )
    return normalized


def _redact_private_networks(text: str) -> str:
    def replace_ip(match: re.Match[str]) -> str:
        value = match.group(0)
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return value
        if value in {"127.0.0.1", "100.64.0.10"}:
            return value
        if address.is_private or address.is_loopback or address in ipaddress.ip_network("100.64.0.0/10"):
            return "100.64.0.10"
        return value

    return _TAILNET_RE.sub("100.64.0.10", _IPV4_RE.sub(replace_ip, text))


def sanitize_publishable_evidence(value: Any) -> Any:
    """Return a deep redacted copy suitable for public evidence validation."""
    def sanitize(item: Any, *, parent: str = "") -> Any:
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                key_text = str(key)
                if _SECRET_KEY_RE.search(key_text):
                    result[key_text] = "[REDACTED]"
                elif parent.lower() in {"env", "environment_values", "headers"}:
                    result[key_text] = "[REDACTED]"
                else:
                    result[key_text] = sanitize(child, parent=key_text)
            return result
        if isinstance(item, list):
            return [sanitize(child, parent=parent) for child in item]
        if isinstance(item, str):
            redacted = _SECRET_VALUE_RE.sub("[REDACTED]", item)
            return _redact_private_networks(redacted)
        return copy.deepcopy(item)

    return sanitize(value)


def _validate_reference(value: Any, *, root: str | None, verify_files: bool) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256", "bytes"}:
        raise BenchmarkArtifactError("bad_evidence_reference", "stage evidence reference is invalid")
    path = value.get("path")
    digest = value.get("sha256")
    size = value.get("bytes")
    if (
        not isinstance(path, str)
        or not path
        or os.path.isabs(path)
        or "\x00" in path
        or ".." in re.split(r"[/\\]+", path)
        or real_path(path, base=root or os.getcwd()) == real_path(root or os.getcwd())
        or not isinstance(digest, str)
        or not _HEX64_RE.fullmatch(digest)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
    ):
        raise BenchmarkArtifactError("bad_evidence_reference", "stage evidence reference is invalid")
    if verify_files:
        if not root:
            raise BenchmarkArtifactError("missing_evidence_root", "file hash validation requires artifact_root")
        target = real_path(path, base=root)
        if not path_is_within(target, real_path(root)) or not os.path.isfile(target):
            raise BenchmarkArtifactError("unsafe_evidence_reference", "referenced evidence file is absent or outside its root")
        observed = evidence_file_reference(target, root=root)
        if observed["sha256"] != digest or observed["bytes"] != size:
            raise BenchmarkArtifactError("evidence_digest_mismatch", "referenced evidence content does not match")
    return copy.deepcopy(value)


def validate_cross_suite_evidence(
    value: Any,
    *,
    artifact_root: str | None = None,
    verify_files: bool = True,
    publishable: bool = False,
) -> dict[str, Any]:
    """Validate measured/prior separation, completeness, stages, and file hashes."""
    if not isinstance(value, dict) or value.get("schema") != CROSS_SUITE_EVIDENCE_SCHEMA:
        raise BenchmarkArtifactError("bad_evidence_schema", "cross-suite evidence schema is invalid")
    evidence = copy.deepcopy(value)
    kind = evidence.get("evidence_kind")
    if kind not in EVIDENCE_KINDS:
        raise BenchmarkArtifactError("bad_evidence_kind", "evidence kind must be measured or external_prior")
    promotion = evidence.get("promotion")
    if not isinstance(promotion, dict) or promotion.get("authorized") is not False or promotion.get("message") != PROMOTION_DISCLAIMER:
        raise BenchmarkArtifactError("missing_promotion_boundary", "evidence must preserve the human promotion boundary")
    if publishable and sanitize_publishable_evidence(evidence) != evidence:
        raise BenchmarkArtifactError("unsafe_publishable_evidence", "evidence contains secrets or private topology")
    if kind == "external_prior":
        if "run" in evidence or "stages" in evidence or "identities" in evidence:
            raise BenchmarkArtifactError("prior_claims_measurement", "external priors cannot carry local run identities or stages")
        source = evidence.get("source")
        if not isinstance(source, dict) or not isinstance(source.get("url"), str) or not source["url"].startswith("https://"):
            raise BenchmarkArtifactError("bad_external_prior", "external prior requires an HTTPS source")
        if evidence.get("locally_measured") is not False:
            raise BenchmarkArtifactError("prior_claims_measurement", "external prior must state locally_measured=false")
        return evidence

    completeness = evidence.get("completeness")
    if completeness not in EVIDENCE_COMPLETENESS:
        raise BenchmarkArtifactError("bad_evidence_completeness", "measured evidence completeness is invalid")
    created_at = evidence.get("created_at")
    try:
        parsed_created_at = dt.datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise BenchmarkArtifactError("bad_evidence_timestamp", "measured evidence created_at is invalid") from exc
    if parsed_created_at.tzinfo is None or parsed_created_at.utcoffset() is None:
        raise BenchmarkArtifactError("bad_evidence_timestamp", "measured evidence created_at requires a UTC offset")
    run = evidence.get("run")
    if not isinstance(run, dict) or not all(isinstance(run.get(key), str) and run[key] for key in (
        "run_id", "ownership_id", "suite", "profile", "spec_sha256"
    )) or not _HEX64_RE.fullmatch(run["spec_sha256"]):
        raise BenchmarkArtifactError("missing_evidence_identity", "measured run identity is incomplete")
    identities = evidence.get("identities")
    if not isinstance(identities, dict) or set(identities) != REQUIRED_MEASURED_IDENTITIES:
        raise BenchmarkArtifactError("missing_evidence_identity", "measured artifact identities are incomplete")
    if any(value is None for value in identities.values()):
        raise BenchmarkArtifactError("missing_evidence_identity", "identity unavailability must be explicit")
    stages = evidence.get("stages")
    if not isinstance(stages, list) or not stages:
        raise BenchmarkArtifactError("missing_evidence_stage", "measured artifact requires ordered stages")
    for sequence, stage in enumerate(stages):
        if not isinstance(stage, dict) or stage.get("sequence") != sequence:
            raise BenchmarkArtifactError("bad_stage_order", "evidence stages must be contiguous and ordered")
        if stage.get("status") not in {"completed", "failed", "cancelled", "incomplete"}:
            raise BenchmarkArtifactError("bad_stage_status", "evidence stage status is invalid")
        if not isinstance(stage.get("name"), str) or not stage["name"]:
            raise BenchmarkArtifactError("bad_stage_status", "evidence stage name is required")
        references = stage.get("evidence")
        if not isinstance(references, list):
            raise BenchmarkArtifactError("bad_evidence_reference", "stage evidence must be a reference list")
        stage["evidence"] = [
            _validate_reference(item, root=artifact_root, verify_files=verify_files)
            for item in references
        ]
    failure = evidence.get("failure")
    if completeness == "completed":
        if failure is not None or any(stage["status"] != "completed" for stage in stages):
            raise BenchmarkArtifactError("false_completion", "completed evidence requires successful stages and no failure")
    else:
        summary = evidence.get("summary")
        if isinstance(summary, dict) and (
            summary.get("completed") is True or summary.get("status") in {"completed", "passed"}
        ):
            raise BenchmarkArtifactError("false_completion", "partial evidence cannot make completed-run assertions")
    if failure is not None:
        if not isinstance(failure, dict) or failure.get("class") not in EVIDENCE_FAILURE_CLASSES:
            raise BenchmarkArtifactError("bad_failure_class", "benchmark failure taxonomy is invalid")
    elif completeness in {"failed", "cancelled"}:
        raise BenchmarkArtifactError("missing_failure", "failed and cancelled evidence require failure identity")
    return evidence


def build_measured_evidence(
    *,
    run: dict[str, Any],
    identities: dict[str, Any],
    stages: list[dict[str, Any]],
    completeness: str,
    summary: dict[str, Any],
    failure: dict[str, Any] | None = None,
    created_at: str | None = None,
    artifact_root: str | None = None,
    verify_files: bool = True,
) -> dict[str, Any]:
    """Build and validate the common envelope for a locally measured run."""
    normalized_failure = copy.deepcopy(failure)
    if normalized_failure is not None and isinstance(normalized_failure.get("class"), str):
        normalized_failure["class"] = normalize_evidence_failure_class(
            normalized_failure["class"]
        )
    value = {
        "schema": CROSS_SUITE_EVIDENCE_SCHEMA,
        "evidence_kind": "measured",
        "completeness": completeness,
        "created_at": created_at or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "run": copy.deepcopy(run),
        "identities": copy.deepcopy(identities),
        "stages": copy.deepcopy(stages),
        "summary": copy.deepcopy(summary),
        "failure": normalized_failure,
        "promotion": {"authorized": False, "message": PROMOTION_DISCLAIMER},
    }
    return validate_cross_suite_evidence(
        value,
        artifact_root=artifact_root,
        verify_files=verify_files,
    )


def build_external_prior_record(*, source: dict[str, Any], claims: dict[str, Any]) -> dict[str, Any]:
    """Build an explicitly non-measured record for a dated external benchmark prior."""
    value = {
        "schema": CROSS_SUITE_EVIDENCE_SCHEMA,
        "evidence_kind": "external_prior",
        "locally_measured": False,
        "source": copy.deepcopy(source),
        "claims": copy.deepcopy(claims),
        "promotion": {"authorized": False, "message": PROMOTION_DISCLAIMER},
    }
    return validate_cross_suite_evidence(value, verify_files=False)


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
            break
        current = parent
    package_root = real_path(os.path.join(os.path.dirname(__file__), "..", ".."))
    return package_root if has_workspace_marker(package_root) else ""


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
