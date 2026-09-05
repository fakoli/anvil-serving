"""Bounded expected-node controller reader for canonical workload observations."""

from __future__ import annotations

import math
import os
import re
import time
import urllib.error
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from ...control_plane.authorization import _normalize_credential
from ...transports import (
    MAX_RESPONSE_BYTES,
    ControllerTransport,
    Operation,
    TransportResult,
    _urlopen_no_proxy_no_redirect,
    _validate_controller_endpoint_host,
)
from ..fleet_workload_collection import build_fleet_workloads, normalize_node_workloads
from ..workload_collection import build_node_workloads
from ..workloads import (
    MAX_FUTURE_SECONDS,
    MAX_JSON_BYTES,
    FleetResult,
    NodeResult,
    ResultStatus,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadOwner,
    WorkloadQuery,
    fleet_result_from_dict,
    node_result_from_dict,
)


_AUTH_ENV_RE = re.compile(r"[A-Z][A-Z0-9_]{0,255}\Z", re.ASCII)
_MAX_ENDPOINT_LENGTH = 2048
_NODE_TIMEOUT_SECONDS = 2.0
_FLEET_TIMEOUT_SECONDS = 7.0


class _BudgetFailure(Exception):
    """Private sentinel: the public boundary projects it to UNAVAILABLE."""


class _Budget:
    def __init__(self, monotonic: Callable[[], object], duration: float) -> None:
        if type(duration) is not float or not math.isfinite(duration) or duration <= 0:
            raise _BudgetFailure
        self._monotonic = monotonic
        self._duration = duration
        self._last = self._read_clock()
        self._deadline = self._last + duration
        self.cleanup_failed = False

    def _read_clock(self) -> float:
        try:
            value = self._monotonic()
        except Exception:
            raise _BudgetFailure from None
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise _BudgetFailure
        return float(value)

    def remaining(self, requested: object) -> float:
        if self.cleanup_failed:
            raise _BudgetFailure
        if type(requested) not in (int, float) or not math.isfinite(requested) or requested <= 0:
            raise _BudgetFailure
        current = self._read_clock()
        if current < self._last or current >= self._deadline:
            raise _BudgetFailure
        self._last = current
        return min(float(requested), self._deadline - current)

    def completed(self) -> None:
        current = self._read_clock()
        if current < self._last or current >= self._deadline:
            raise _BudgetFailure
        self._last = current

    def remaining_total(self) -> float:
        return self.remaining(self._duration)


def _safe_close(value: object) -> bool:
    try:
        value.close()
    except Exception:
        return False
    return True


class _BudgetedResponse:
    def __init__(self, raw: object, budget: _Budget) -> None:
        self._raw = raw
        self._budget = budget

    def __enter__(self) -> "_BudgetedResponse":
        return self

    def __exit__(self, *_unused: object) -> bool:
        if not _safe_close(self._raw):
            self._budget.cleanup_failed = True
        else:
            try:
                self._budget.completed()
            except _BudgetFailure:
                self._budget.cleanup_failed = True
        return False

    def read(self, amount: int = -1) -> bytes:
        self._budget.remaining_total()
        try:
            result = self._raw.read(amount)
        except Exception:
            raise _BudgetFailure from None
        self._budget.completed()
        return result


def _budgeted_opener(
    opener: Callable[..., object], budget: _Budget
) -> Callable[..., _BudgetedResponse]:
    def open_request(request: object, timeout: object) -> _BudgetedResponse:
        remaining = budget.remaining(timeout)
        try:
            response = opener(request, timeout=remaining)
        except urllib.error.HTTPError as error:
            _safe_close(error)
            if error.fp is not None:
                _safe_close(error.fp)
            raise _BudgetFailure from None
        except Exception:
            raise _BudgetFailure from None
        try:
            budget.completed()
        except _BudgetFailure:
            _safe_close(response)
            raise
        return _BudgetedResponse(response, budget)

    return open_request


def _canonical_query(query: WorkloadQuery) -> WorkloadQuery:
    return WorkloadQuery(
        owner=query.owner,
        kind=query.kind,
        state=query.state,
        host=query.host,
        active_only=query.active_only,
        recent_seconds=query.recent_seconds,
        limit=query.limit,
    )


def _failed(
    host: str, query: WorkloadQuery, collected: datetime, code: WorkloadErrorCode
) -> NodeResult:
    sources = {
        owner: SourceResult(
            owner=owner,
            status=ResultStatus.UNAVAILABLE,
            collection_timestamp=collected,
            records=(),
            truncation=Truncation(0, None),
            error=code,
        )
        for owner in WorkloadOwner
    }
    return build_node_workloads(host, query, collected, sources)


def _endpoint(value: object) -> str | None:
    if type(value) is not str or not value or len(value) > _MAX_ENDPOINT_LENGTH:
        return None
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return None
    if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


def _credential(
    auth_env: object, environment: Mapping[str, str] | None
) -> tuple[str, dict[str, str]] | None:
    if type(auth_env) is not str or _AUTH_ENV_RE.fullmatch(auth_env) is None:
        return None
    source: Mapping[str, str]
    if environment is None:
        source = os.environ
    elif isinstance(environment, Mapping):
        source = environment
    else:
        return None
    try:
        value = source.get(auth_env)
        normalized = _normalize_credential(value)
        token = normalized.decode("ascii")
    except Exception:
        return None
    return auth_env, {auth_env: token}


def _arguments(query: WorkloadQuery) -> dict[str, object]:
    return {
        "owner": None if query.owner is None else query.owner.value,
        "kind": None if query.kind is None else query.kind.value,
        "state": None if query.state is None else query.state.value,
        "host": query.host,
        "active_only": query.active_only,
        "recent_seconds": query.recent_seconds,
        "limit": query.limit,
    }


def _node_from_transport(result: object) -> NodeResult:
    if (
        type(result) is not TransportResult
        or getattr(result, "operation", None) != "node-workloads"
        or getattr(result, "transport", None) != "controller"
    ):
        raise WorkloadError(WorkloadErrorCode.INVALID, "invalid controller workload result")
    envelope = getattr(result, "data", None)
    if not isinstance(envelope, Mapping) or set(envelope) != {"ok", "data"}:
        raise WorkloadError(WorkloadErrorCode.INVALID, "invalid controller workload result")
    if type(envelope["ok"]) is not bool or envelope["ok"] is not True:
        raise WorkloadError(WorkloadErrorCode.INVALID, "invalid controller workload result")
    if type(envelope["data"]) is not dict:
        raise WorkloadError(WorkloadErrorCode.INVALID, "invalid controller workload result")
    return node_result_from_dict(envelope["data"])


def _fleet_failure(code: WorkloadErrorCode) -> WorkloadError:
    messages = {
        WorkloadErrorCode.INVALID: "invalid controller fleet workload result",
        WorkloadErrorCode.UNSUPPORTED: "controller fleet workload schema is unsupported",
        WorkloadErrorCode.FUTURE: "controller fleet workload timestamp is in the future",
        WorkloadErrorCode.UNAVAILABLE: "controller fleet workload source is unavailable",
    }
    return WorkloadError(code, messages[code])


def _fleet_from_transport(result: object) -> FleetResult:
    if (
        type(result) is not TransportResult
        or getattr(result, "operation", None) != "fleet-workloads"
        or getattr(result, "transport", None) != "controller"
    ):
        raise _fleet_failure(WorkloadErrorCode.INVALID)
    envelope = getattr(result, "data", None)
    if not isinstance(envelope, Mapping) or set(envelope) != {"ok", "data"}:
        raise _fleet_failure(WorkloadErrorCode.INVALID)
    if type(envelope["ok"]) is not bool or envelope["ok"] is not True:
        raise _fleet_failure(WorkloadErrorCode.UNAVAILABLE)
    if type(envelope["data"]) is not dict:
        raise _fleet_failure(WorkloadErrorCode.INVALID)
    return fleet_result_from_dict(envelope["data"])


def _reject_future_fleet(result: FleetResult, receipt: datetime) -> None:
    latest = receipt + timedelta(seconds=MAX_FUTURE_SECONDS)
    timestamps = [result.collection_timestamp]
    for node in result.nodes:
        timestamps.append(node.collection_timestamp)
        for source in node.sources:
            timestamps.append(source.collection_timestamp)
            for record in source.records:
                timestamps.extend((record.created_at, record.updated_at, record.source_timestamp))
    if any(value > latest for value in timestamps):
        raise _fleet_failure(WorkloadErrorCode.FUTURE)


def _validated_fleet(result: FleetResult, query: WorkloadQuery, receipt: datetime) -> FleetResult:
    _reject_future_fleet(result, receipt)
    hosts = tuple(node.host for node in result.nodes)
    normalized = build_fleet_workloads(
        hosts,
        query,
        receipt,
        {node.host: node for node in result.nodes},
    )
    comparison = FleetResult(
        normalized.status,
        result.collection_timestamp,
        normalized.nodes,
        normalized.truncation,
    )
    if comparison != result:
        raise _fleet_failure(WorkloadErrorCode.INVALID)
    return result


def read_controller_workloads(
    endpoint: str,
    auth_env: str,
    host: str,
    query: WorkloadQuery,
    now: datetime,
    *,
    environment: Mapping[str, str] | None = None,
    monotonic: Callable[[], object] = time.monotonic,
    _open: Callable[..., object] | None = None,
) -> NodeResult:
    """Read one expected-node controller workload snapshot with one absolute budget."""
    baseline = build_node_workloads(host, query, now, {})
    checked_query = _canonical_query(query)
    collected = baseline.collection_timestamp
    if checked_query.host is not None and checked_query.host != host:
        return normalize_node_workloads(host, checked_query, collected, None)

    declared = _endpoint(endpoint)
    credential = _credential(auth_env, environment)
    if declared is None or credential is None:
        return _failed(host, checked_query, collected, WorkloadErrorCode.UNAVAILABLE)
    try:
        _validate_controller_endpoint_host(declared)
        budget = _Budget(monotonic, _NODE_TIMEOUT_SECONDS)
        reference, values = credential
        transport = ControllerTransport(
            declared,
            auth_env=reference,
            allowed_operations=("node-workloads",),
            environment=values,
            timeout_seconds=_NODE_TIMEOUT_SECONDS,
            max_response_bytes=MAX_RESPONSE_BYTES,
            opener=_budgeted_opener(
                _urlopen_no_proxy_no_redirect if _open is None else _open, budget
            ),
            expected_node=host,
        )
        result = transport.execute(
            Operation("node-workloads", _arguments(checked_query), tool_name="node_workloads")
        )
        if budget.cleanup_failed:
            raise _BudgetFailure
        node = _node_from_transport(result)
        return normalize_node_workloads(host, checked_query, collected, node)
    except WorkloadError as error:
        return _failed(host, checked_query, collected, error.code)
    except Exception:
        return _failed(host, checked_query, collected, WorkloadErrorCode.UNAVAILABLE)


def read_controller_fleet_workloads(
    endpoint: str,
    auth_env: str,
    expected_node: str,
    query: WorkloadQuery,
    now: datetime,
    *,
    environment: Mapping[str, str] | None = None,
    monotonic: Callable[[], object] = time.monotonic,
    _open: Callable[..., object] | None = None,
) -> FleetResult:
    """Read one scoped canonical fleet snapshot with one absolute budget."""
    try:
        if (
            type(expected_node) is not str
            or type(query) is not WorkloadQuery
            or type(now) is not datetime
        ):
            raise _fleet_failure(WorkloadErrorCode.INVALID)
        checked_query = _canonical_query(query)
        receipt = build_node_workloads(expected_node, checked_query, now, {}).collection_timestamp
    except WorkloadError as error:
        code = error.code if type(error.code) is WorkloadErrorCode else WorkloadErrorCode.INVALID
        raise _fleet_failure(code) from None
    except Exception:
        raise _fleet_failure(WorkloadErrorCode.INVALID) from None

    declared = _endpoint(endpoint)
    credential = _credential(auth_env, environment)
    if declared is None or credential is None:
        raise _fleet_failure(WorkloadErrorCode.UNAVAILABLE) from None
    try:
        _validate_controller_endpoint_host(declared)
        budget = _Budget(monotonic, _FLEET_TIMEOUT_SECONDS)
        reference, values = credential
        transport = ControllerTransport(
            declared,
            auth_env=reference,
            allowed_operations=("fleet-workloads",),
            environment=values,
            timeout_seconds=_FLEET_TIMEOUT_SECONDS,
            max_response_bytes=MAX_JSON_BYTES,
            opener=_budgeted_opener(
                _urlopen_no_proxy_no_redirect if _open is None else _open,
                budget,
            ),
            expected_node=expected_node,
        )
        result = transport.execute(
            Operation(
                "fleet-workloads",
                _arguments(checked_query),
                tool_name="fleet_workloads",
            )
        )
        if budget.cleanup_failed:
            raise _BudgetFailure
        return _validated_fleet(_fleet_from_transport(result), checked_query, receipt)
    except WorkloadError as error:
        code = error.code if type(error.code) is WorkloadErrorCode else WorkloadErrorCode.INVALID
        raise _fleet_failure(code) from None
    except Exception:
        raise _fleet_failure(WorkloadErrorCode.UNAVAILABLE) from None


__all__ = ["read_controller_fleet_workloads", "read_controller_workloads"]
