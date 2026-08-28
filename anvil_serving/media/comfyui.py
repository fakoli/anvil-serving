"""Bounded standard-library adapter for an explicitly selected ComfyUI service."""

from __future__ import annotations

import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..transports import _urlopen_no_proxy_no_redirect
from .backends import BackendOutput, BackendStatus
from .contracts import RenderedWorkflow, WorkflowDescriptor
from .errors import MediaError


MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_METADATA_ITEMS = 4096
MAX_ERROR_BYTES = 4096
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
_PROMPT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


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
        self._opener = opener or _urlopen_no_proxy_no_redirect

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

    def compatibility(
        self,
        workflow: WorkflowDescriptor,
        *,
        qualification: bool = False,
    ) -> WorkflowCompatibility:
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
        policy_reasons = () if qualification else workflow.unavailable_reasons
        return WorkflowCompatibility(
            workflow.id,
            workflow.version,
            True,
            not reasons and (workflow.available or qualification),
            missing_features,
            missing_nodes,
            missing_models,
            reasons or policy_reasons,
        )

    def submit(self, workflow: RenderedWorkflow, *, job_id: str) -> str:
        client_id = "anvil-" + job_id
        response = self.request_json(
            "POST",
            "/prompt",
            {"prompt": workflow.graph, "client_id": client_id},
            max_bytes=64 * 1024,
        )
        prompt_id = response.get("prompt_id") if isinstance(response, Mapping) else None
        if not isinstance(prompt_id, str) or not _PROMPT_ID_RE.fullmatch(prompt_id):
            raise MediaError("backend_response_invalid", "ComfyUI did not return a valid prompt identity", status=502)
        return prompt_id

    def queue(self) -> dict[str, int]:
        response = self.request_json("GET", "/queue")
        if not isinstance(response, Mapping):
            raise MediaError("backend_response_invalid", "ComfyUI queue response is invalid", status=502)
        running = response.get("queue_running", [])
        pending = response.get("queue_pending", [])
        if not isinstance(running, list) or not isinstance(pending, list):
            raise MediaError("backend_response_invalid", "ComfyUI queue response is invalid", status=502)
        if len(running) + len(pending) > MAX_METADATA_ITEMS:
            raise MediaError("backend_metadata_too_large", "ComfyUI queue response is unbounded", status=502)
        return {"running": len(running), "pending": len(pending)}

    def history(self, prompt_id: str) -> BackendStatus:
        if not isinstance(prompt_id, str) or not _PROMPT_ID_RE.fullmatch(prompt_id):
            raise MediaError("invalid_backend_prompt", "backend prompt identity is invalid")
        response = self.request_json("GET", "/history/" + urllib.parse.quote(prompt_id, safe=""))
        if not isinstance(response, Mapping):
            raise MediaError("backend_response_invalid", "ComfyUI history response is invalid", status=502)
        record = response.get(prompt_id)
        if record is None:
            return BackendStatus(prompt_id, "queued")
        if not isinstance(record, Mapping):
            raise MediaError("backend_response_invalid", "ComfyUI history record is invalid", status=502)
        status = record.get("status")
        status_text = status.get("status_str") if isinstance(status, Mapping) else None
        completed = status.get("completed") if isinstance(status, Mapping) else None
        messages = status.get("messages") if isinstance(status, Mapping) else None
        if completed is True:
            state = "completed"
        elif status_text in {"error", "failed"}:
            state = "failed"
        else:
            state = "running"
        outputs_raw = record.get("outputs", {})
        if not isinstance(outputs_raw, Mapping) or len(outputs_raw) > MAX_METADATA_ITEMS:
            raise MediaError("backend_response_invalid", "ComfyUI output history is invalid or unbounded", status=502)
        outputs: list[BackendOutput] = []
        for node, node_outputs in outputs_raw.items():
            if not isinstance(node, str) or not isinstance(node_outputs, Mapping):
                raise MediaError("backend_response_invalid", "ComfyUI output record is invalid", status=502)
            for key in ("images", "gifs", "videos"):
                items = node_outputs.get(key, [])
                if not isinstance(items, list) or len(items) > 128:
                    raise MediaError("backend_response_invalid", "ComfyUI output list is invalid or unbounded", status=502)
                for item in items:
                    if not isinstance(item, Mapping):
                        raise MediaError("backend_response_invalid", "ComfyUI output item is invalid", status=502)
                    filename = item.get("filename")
                    subfolder = item.get("subfolder", "")
                    storage_type = item.get("type", "output")
                    if not all(isinstance(value, str) and len(value) <= 512 for value in (filename, subfolder, storage_type)):
                        raise MediaError("backend_response_invalid", "ComfyUI output coordinates are invalid", status=502)
                    outputs.append(BackendOutput(node, filename, subfolder, storage_type))
                    if len(outputs) > 128:
                        raise MediaError("backend_metadata_too_large", "ComfyUI output history is unbounded", status=502)
        error_code = ""
        if state == "failed" and isinstance(messages, list) and messages:
            error_code = "execution_failed"
        return BackendStatus(prompt_id, state, outputs=tuple(outputs), error_code=error_code)

    def fetch_output(self, output: BackendOutput, *, max_bytes: int) -> bytes:
        query = urllib.parse.urlencode(
            {"filename": output.filename, "subfolder": output.subfolder, "type": output.storage_type}
        )
        return self.request_bytes("GET", "/view?" + query, max_bytes=max_bytes)

    def upload_input(self, filename: str, content: bytes, *, max_bytes: int = MAX_UPLOAD_BYTES) -> str:
        if (
            not isinstance(filename, str)
            or not filename
            or len(filename) > 128
            or filename != filename.replace("\\", "/").rsplit("/", 1)[-1]
        ):
            raise MediaError("invalid_upload", "upload filename is invalid")
        if not isinstance(content, bytes) or not content or len(content) > max_bytes:
            raise MediaError("invalid_upload", "upload content is empty or exceeds policy", status=413)
        boundary = "anvil" + secrets.token_hex(16)
        prefix = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("ascii")
        body = prefix + content + f"\r\n--{boundary}--\r\n".encode("ascii")
        response = self.request_bytes(
            "POST",
            "/upload/image",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"},
            max_bytes=64 * 1024,
        )
        try:
            value = json.loads(response.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise MediaError("backend_response_invalid", "ComfyUI upload response is invalid", status=502) from exc
        stored = value.get("name") if isinstance(value, Mapping) else None
        if not isinstance(stored, str) or not stored or len(stored) > 256:
            raise MediaError("backend_response_invalid", "ComfyUI upload response lacks a bounded name", status=502)
        return stored

    def request_bytes(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        max_bytes: int,
    ) -> bytes:
        parsed = urllib.parse.urlsplit(path)
        if not parsed.path.startswith("/") or parsed.fragment:
            raise MediaError("invalid_backend_request", "ComfyUI request path is invalid")
        if max_bytes < 1 or max_bytes > 1024 * 1024 * 1024:
            raise MediaError("invalid_backend_request", "ComfyUI response byte limit is invalid")
        request = urllib.request.Request(self.base_url + path, data=body, headers=dict(headers or {}), method=method)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                payload = response.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise MediaError("backend_http_error", f"ComfyUI returned HTTP {exc.code}", status=502, details={"status": exc.code}) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise MediaError("backend_unavailable", "ComfyUI is unavailable", status=503) from exc
        if not isinstance(payload, bytes) or len(payload) > max_bytes:
            raise MediaError("backend_response_too_large", "ComfyUI response exceeds its byte limit", status=502)
        return payload

    def delete_queued_prompt(self, prompt_id: str) -> None:
        if not isinstance(prompt_id, str) or not _PROMPT_ID_RE.fullmatch(prompt_id):
            raise MediaError("invalid_backend_prompt", "backend prompt identity is invalid")
        body = json.dumps({"delete": [prompt_id]}, separators=(",", ":")).encode("utf-8")
        self.request_bytes(
            "POST",
            "/queue",
            body=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            max_bytes=64 * 1024,
        )

    def interrupt_exclusive_prompt(self) -> None:
        self.request_bytes(
            "POST",
            "/interrupt",
            body=b"{}",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            max_bytes=64 * 1024,
        )


__all__ = ["ComfyUIClient", "WorkflowCompatibility"]
