"""Explicit immutable media-workflow registry and bounded graph renderer."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .contracts import ParameterBinding, ParameterSpec, RenderedWorkflow, WorkflowDescriptor
from .errors import MediaError


REGISTRY_SCHEMA = "anvil-serving.media-workflow-registry/v1"
WORKFLOW_SCHEMA = "anvil-serving.media-workflow/v1"
MAX_DESCRIPTOR_BYTES = 256 * 1024
MAX_GRAPH_BYTES = 2 * 1024 * 1024
MAX_WORKFLOWS = 64
MAX_GRAPH_NODES = 512


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_json(path: Path, *, max_bytes: int) -> Any:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise MediaError("workflow_file_unavailable", "workflow file is unavailable") from exc
    if size < 2 or size > max_bytes:
        raise MediaError("workflow_file_invalid", "workflow file size is outside policy")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MediaError("workflow_file_invalid", "workflow file is not valid UTF-8 JSON") from exc


def _safe_child(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or os.path.isabs(name) or Path(name).name != name:
        raise MediaError("workflow_file_invalid", "workflow registry contains an unsafe path")
    target = (root / name).resolve(strict=False)
    try:
        target.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise MediaError("workflow_file_invalid", "workflow registry path escapes its root") from exc
    return target


def _string_tuple(raw: Any, field_name: str, *, required: bool = False) -> tuple[str, ...]:
    if raw is None and not required:
        return ()
    if not isinstance(raw, list) or (required and not raw) or len(raw) > 128:
        raise MediaError("workflow_descriptor_invalid", f"{field_name} must be a bounded list")
    if any(not isinstance(item, str) or not item or len(item) > 256 for item in raw):
        raise MediaError("workflow_descriptor_invalid", f"{field_name} contains an invalid value")
    return tuple(raw)


def _descriptor(raw: Mapping[str, Any], graph: Mapping[str, Any]) -> WorkflowDescriptor:
    if raw.get("schema") != WORKFLOW_SCHEMA:
        raise MediaError("workflow_descriptor_invalid", "workflow descriptor schema is invalid")
    parameters_raw = raw.get("parameters")
    if not isinstance(parameters_raw, Mapping):
        raise MediaError("workflow_descriptor_invalid", "workflow parameters are invalid")
    parameters: dict[str, ParameterSpec] = {}
    for name, spec in parameters_raw.items():
        if not isinstance(name, str) or not isinstance(spec, Mapping):
            raise MediaError("workflow_descriptor_invalid", "workflow parameter is invalid")
        allowed = {"kind", "required", "minimum", "maximum", "max_length", "enum"}
        if set(spec) - allowed:
            raise MediaError("workflow_descriptor_invalid", "workflow parameter has unknown fields")
        parameters[name] = ParameterSpec(
            kind=spec.get("kind"),
            required=spec.get("required", True),
            minimum=spec.get("minimum"),
            maximum=spec.get("maximum"),
            max_length=spec.get("max_length"),
            enum=tuple(spec.get("enum", ())),
        )
    bindings_raw = raw.get("bindings")
    if not isinstance(bindings_raw, list) or len(bindings_raw) > 64:
        raise MediaError("workflow_descriptor_invalid", "workflow bindings are invalid")
    bindings: list[ParameterBinding] = []
    for item in bindings_raw:
        if not isinstance(item, Mapping) or set(item) != {"parameter", "node", "input"}:
            raise MediaError("workflow_descriptor_invalid", "workflow binding is invalid")
        bindings.append(ParameterBinding(item["parameter"], item["node"], item["input"]))
    limits = raw.get("limits")
    if not isinstance(limits, Mapping):
        raise MediaError("workflow_descriptor_invalid", "workflow limits are invalid")
    expected_digest = raw.get("graph_digest")
    observed_digest = canonical_digest(graph)
    if expected_digest != observed_digest:
        raise MediaError(
            "workflow_digest_mismatch",
            "workflow graph does not match its immutable descriptor",
            status=409,
            details={"expected": expected_digest, "observed": observed_digest},
        )
    reasons = _string_tuple(raw.get("unavailable_reasons", []), "unavailable reasons")
    available = raw.get("available", False)
    if not isinstance(available, bool) or (available and reasons):
        raise MediaError("workflow_descriptor_invalid", "workflow availability is inconsistent")
    return WorkflowDescriptor(
        id=raw.get("id"),
        version=raw.get("version"),
        kind=raw.get("kind"),
        service_target=raw.get("service_target"),
        graph_digest=expected_digest,
        parameters=parameters,
        bindings=tuple(bindings),
        output_nodes=_string_tuple(raw.get("output_nodes"), "output nodes", required=True),
        output_mime_types=_string_tuple(raw.get("output_mime_types"), "output MIME types", required=True),
        required_features=_string_tuple(raw.get("required_features"), "required features"),
        required_nodes=_string_tuple(raw.get("required_nodes"), "required nodes"),
        required_models=_string_tuple(raw.get("required_models"), "required models"),
        available=available,
        unavailable_reasons=reasons,
        max_request_bytes=limits.get("request_bytes"),
        max_artifact_bytes=limits.get("artifact_bytes"),
        timeout_seconds=limits.get("timeout_seconds"),
        retention_seconds=limits.get("retention_seconds"),
        max_queue_depth=limits.get("queue_depth"),
        max_concurrency=limits.get("concurrency"),
    )


class WorkflowRegistry:
    """Load only explicitly listed descriptors; never scan a directory."""

    def __init__(self, registry_path: str | os.PathLike[str]) -> None:
        path = Path(registry_path).resolve(strict=False)
        root = path.parent
        raw = _read_json(path, max_bytes=MAX_DESCRIPTOR_BYTES)
        if not isinstance(raw, Mapping) or raw.get("schema") != REGISTRY_SCHEMA:
            raise MediaError("workflow_registry_invalid", "workflow registry schema is invalid")
        names = raw.get("workflows")
        if not isinstance(names, list) or not names or len(names) > MAX_WORKFLOWS:
            raise MediaError("workflow_registry_invalid", "workflow registry list is invalid")
        if names != sorted(names) or len(names) != len(set(names)):
            raise MediaError("workflow_registry_invalid", "workflow registry must be sorted and unique")
        descriptors: dict[str, WorkflowDescriptor] = {}
        graphs: dict[str, Mapping[str, Any]] = {}
        for name in names:
            descriptor_path = _safe_child(root, name)
            descriptor_raw = _read_json(descriptor_path, max_bytes=MAX_DESCRIPTOR_BYTES)
            if not isinstance(descriptor_raw, Mapping):
                raise MediaError("workflow_descriptor_invalid", "workflow descriptor must be an object")
            graph_path = _safe_child(root, descriptor_raw.get("graph"))
            graph = _read_json(graph_path, max_bytes=MAX_GRAPH_BYTES)
            if not isinstance(graph, Mapping) or not graph or len(graph) > MAX_GRAPH_NODES:
                raise MediaError("workflow_graph_invalid", "workflow graph is invalid or unbounded")
            if any(not str(node).isdigit() or not isinstance(value, Mapping) for node, value in graph.items()):
                raise MediaError("workflow_graph_invalid", "workflow graph contains an invalid node")
            descriptor = _descriptor(descriptor_raw, graph)
            if descriptor.key in descriptors:
                raise MediaError("workflow_registry_invalid", "workflow registry contains a duplicate identity")
            descriptors[descriptor.key] = descriptor
            graphs[descriptor.key] = copy.deepcopy(graph)
        self._descriptors = descriptors
        self._graphs = graphs

    def list(self) -> list[dict[str, Any]]:
        return [self._descriptors[key].as_public_dict() for key in sorted(self._descriptors)]

    def get(self, workflow_id: str, version: str) -> WorkflowDescriptor:
        descriptor = self._descriptors.get(f"{workflow_id}@{version}")
        if descriptor is None:
            raise MediaError("workflow_not_found", "named workflow version is not configured", status=404)
        return descriptor

    def render(self, workflow_id: str, version: str, values: Mapping[str, Any]) -> RenderedWorkflow:
        descriptor = self.get(workflow_id, version)
        validated = descriptor.validate_parameters(values)
        graph = copy.deepcopy(self._graphs[descriptor.key])
        if canonical_digest(graph) != descriptor.graph_digest:
            raise MediaError("workflow_digest_mismatch", "workflow graph changed after registry load", status=409)
        for binding in descriptor.bindings:
            if binding.parameter not in validated:
                continue
            node = graph.get(binding.node)
            inputs = node.get("inputs") if isinstance(node, Mapping) else None
            if not isinstance(inputs, dict) or binding.input not in inputs:
                raise MediaError("workflow_binding_invalid", "declared workflow binding is absent from the graph")
            inputs[binding.input] = validated[binding.parameter]
        return RenderedWorkflow(
            descriptor=descriptor,
            graph=graph,
            parameters_digest=canonical_digest(validated),
        )


__all__ = ["WorkflowRegistry", "canonical_digest"]
