"""Bounded standard-library adapter for an explicitly selected ComfyUI service."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .contracts import WorkflowDescriptor
from .errors import MediaError


MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_METADATA_ITEMS = 4096
MAX_ERROR_BYTES = 4096


def _base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise MediaError("invalid_backend", "ComfyUI base URL must be HTTP(S)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise MediaError("invalid_backend", "ComfyUI base URL contains forbidden components")
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _bounded_strings(value: Any, *, label: str) -> frozenset[str]:
    if isinstance(value, Mapping):
        values = value.keys()
    elif isinstance(value, list):
        values = value
    else:
        raise MediaError("backend_metadata_invalid", f"ComfyUI {label} inventory is invalid")
    result: set[str] = set()
    for item in values:
        if len(result) >= MAX_METADATA_ITEMS:
            raise MediaError("backend_metadata_too_large", f"ComfyUI {label} inventory is unbounded")
        if not isinstance(item, str) or not item or len(item) > 256:
            raise MediaError("backend_metadata_invalid", f"ComfyUI {label} inventory contains an invalid value")
        result.add(item)
    return frozenset(result)


@dataclass(frozen=True)
class WorkflowCompatibility:
    workflow_id: str
    version: str
    ready: bool
    available: bool
    missing_features: tuple[str, ...] = ()
    missing_nodes: tuple[str, ...] = ()
    missing_models: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.workflow_id,
            "version": self.version,
            "ready": self.ready,
            "available": self.available,
            "missingFeatures": list(self.missing_features),
            "missingNodes": list(self.missing_nodes),
            "missingModels": list(self.missing_models),
            "reasons": list(self.reasons),
        }


class ComfyUIClient:
    """All backend route knowledge is confined to this bounded client."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 5.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.base_url = _base_url(base_url)
        if timeout <= 0 or timeout > 3600:
            raise MediaError("invalid_backend", "ComfyUI timeout is outside policy")
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def request_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        max_bytes: int = MAX_METADATA_BYTES,
    ) -> Any:
        if not path.startswith("/") or "?" in path or "#" in path:
            raise MediaError("invalid_backend_request", "ComfyUI request path is invalid")
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
            if len(data) > MAX_METADATA_BYTES:
                raise MediaError("backend_request_too_large", "ComfyUI request exceeds its byte limit")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                body = response.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(MAX_ERROR_BYTES + 1)
            except Exception:
                raw = b""
            raise MediaError(
                "backend_http_error",
                f"ComfyUI returned HTTP {exc.code}",
                status=502,
                details={"status": exc.code, "bodyBytes": min(len(raw), MAX_ERROR_BYTES), "truncated": len(raw) > MAX_ERROR_BYTES},
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise MediaError("backend_unavailable", "ComfyUI is unavailable", status=503) from exc
        if not isinstance(body, bytes) or len(body) > max_bytes:
            raise MediaError("backend_response_too_large", "ComfyUI response exceeds its byte limit", status=502)
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise MediaError("backend_response_invalid", "ComfyUI response is not valid JSON", status=502) from exc

    def compatibility(self, workflow: WorkflowDescriptor) -> WorkflowCompatibility:
        stats = self.request_json("GET", "/system_stats")
        ready = isinstance(stats, Mapping) and isinstance(stats.get("system"), Mapping)
        if not ready:
            return WorkflowCompatibility(workflow.id, workflow.version, False, False, reasons=("not_ready",))
        features_raw = self.request_json("GET", "/features")
        nodes_raw = self.request_json("GET", "/object_info")
        features = _bounded_strings(features_raw, label="feature")
        nodes = _bounded_strings(nodes_raw, label="node")
        models: set[str] = set()
        for folder in ("diffusion_models", "text_encoders", "vae", "checkpoints"):
            inventory = self.request_json("GET", f"/models/{folder}")
            models.update(_bounded_strings(inventory, label="model"))
            if len(models) > MAX_METADATA_ITEMS:
                raise MediaError("backend_metadata_too_large", "ComfyUI model inventory is unbounded")
        missing_features = tuple(sorted(set(workflow.required_features) - features))
        missing_nodes = tuple(sorted(set(workflow.required_nodes) - nodes))
        missing_models = tuple(sorted(set(workflow.required_models) - models))
        reasons = tuple(
            reason
            for condition, reason in (
                (missing_features, "missing_feature"),
                (missing_nodes, "missing_node"),
                (missing_models, "missing_model"),
            )
            if condition
        )
        return WorkflowCompatibility(
            workflow.id,
            workflow.version,
            True,
            not reasons and workflow.available,
            missing_features,
            missing_nodes,
            missing_models,
            reasons or workflow.unavailable_reasons,
        )


__all__ = ["ComfyUIClient", "WorkflowCompatibility"]
