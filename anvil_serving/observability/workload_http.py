"""Scoped, read-only workload HTTP boundary independent of telemetry auth."""

from __future__ import annotations

import re
import threading
import time
import urllib.parse
from collections.abc import Callable
from datetime import datetime, timezone

from ..control_plane.authorization import (
    MAX_CREDENTIAL_BYTES,
    WORKLOADS_READ,
    AuthorizationPolicy,
    _normalize_credential,
    check_scope,
)
from ..transports import _controller_endpoint, _validate_controller_endpoint_host
from .probes.controller_workloads import _endpoint, read_controller_fleet_workloads
from .workloads import (
    MAX_JSON_BYTES,
    FleetResult,
    WorkloadQuery,
    fleet_result_from_json,
    fleet_result_to_json,
    normalize_workload_timestamp,
    parse_workload_query,
)


_FORWARDED_CREDENTIAL = "WORKLOAD_REQUEST_CREDENTIAL"
_MAX_QUERY_BYTES = 8192
_PERCENT_ESCAPE = re.compile(r"%[0-9a-fA-F]{2}")
_ERRORS = {
    "authentication_error": (401, "workload authentication required"),
    "authorization_scope_denied": (403, "authorization scope denied"),
    "invalid_workload_query": (400, "invalid workload query"),
    "invalid_workload_request": (400, "invalid workload request"),
    "read_only_workload_api": (405, "workload API is read only"),
    "workload_source_unavailable": (503, "workload source unavailable"),
}


def workload_http_error(code: str) -> tuple[int, bytes]:
    """Return only a fixed allowlisted error; no caller text is serialized."""
    if type(code) is not str or code not in _ERRORS:
        code = "workload_source_unavailable"
    status, message = _ERRORS[code]
    return status, (
        '{"ok":false,"error":{"code":"' + code + '","message":"' + message + '"}}'
    ).encode("ascii")


def _presented_credential(headers: object) -> str:
    authorization = headers.get_all("Authorization")
    alternate = headers.get_all("X-Api-Key")
    if alternate is not None or type(authorization) is not list or len(authorization) != 1:
        raise ValueError
    header = authorization[0]
    if type(header) is not str or len(header) > len("Bearer ") + MAX_CREDENTIAL_BYTES:
        raise ValueError
    if not header.startswith("Bearer "):
        raise ValueError
    presented = header[len("Bearer "):]
    if any(character.isspace() for character in presented):
        raise ValueError
    if _normalize_credential(presented).decode("ascii") != presented:
        raise ValueError
    return presented


def _workload_query(raw_query: object) -> WorkloadQuery:
    if type(raw_query) is not str or len(raw_query) > _MAX_QUERY_BYTES:
        raise ValueError
    raw_query.encode("ascii", "strict")
    for index, character in enumerate(raw_query):
        if character == "%" and _PERCENT_ESCAPE.match(raw_query, index) is None:
            raise ValueError
    pairs = urllib.parse.parse_qsl(
        raw_query, keep_blank_values=True, strict_parsing=False,
        encoding="utf-8", errors="strict", max_num_fields=7,
    )
    normalized: list[tuple[str, object]] = []
    for key, value in pairs:
        if key == "active_only":
            if value not in {"true", "false"}:
                raise ValueError
            parsed: object = value == "true"
        elif key in {"limit", "recent_seconds"}:
            if re.fullmatch(r"[0-9]{1,5}", value) is None:
                raise ValueError
            parsed = int(value)
        else:
            parsed = value
        normalized.append((key, parsed))
    return parse_workload_query(normalized)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkloadHTTPService:
    """One explicit fleet controller, scoped caller credentials and four slots.

    Calls never queue, discover configuration or lend a service credential.
    The fleet reader owns its health/query/read/cleanup deadline. This service
    starts no workers and always releases capacity after the reader returns.
    """

    def __init__(
        self,
        endpoint: str,
        expected_node: str,
        policy: AuthorizationPolicy | None,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], object] = time.monotonic,
        reader: Callable[..., FleetResult] = read_controller_fleet_workloads,
    ) -> None:
        try:
            if _endpoint(endpoint) is None or type(expected_node) is not str:
                raise ValueError
            _controller_endpoint(endpoint)
            _validate_controller_endpoint_host(endpoint)
            parse_workload_query({"host": expected_node})
        except Exception:
            raise ValueError("invalid workload controller binding") from None
        self._endpoint = endpoint
        self._expected_node = expected_node
        self._policy = policy
        self._clock = _utc_now if clock is None else clock
        self._monotonic = monotonic
        self._reader = reader
        self._slots = threading.BoundedSemaphore(4)

    def read(self, raw_query: str, headers: object) -> tuple[int, bytes]:
        """Authorize, validate and return canonical bytes, or one fixed error."""
        try:
            presented = _presented_credential(headers)
        except Exception:
            return workload_http_error("authentication_error")
        try:
            allowed = check_scope(self._policy, presented, WORKLOADS_READ).allowed
        except Exception:
            allowed = False
        if not allowed:
            return workload_http_error("authorization_scope_denied")
        try:
            query = _workload_query(raw_query)
        except Exception:
            return workload_http_error("invalid_workload_query")
        if not self._slots.acquire(blocking=False):
            return workload_http_error("workload_source_unavailable")
        try:
            now = normalize_workload_timestamp(self._clock())
            result = self._reader(
                self._endpoint, _FORWARDED_CREDENTIAL, self._expected_node, query, now,
                environment={_FORWARDED_CREDENTIAL: presented}, monotonic=self._monotonic,
            )
            if type(result) is not FleetResult:
                raise ValueError
            serialized = fleet_result_to_json(result)
            if fleet_result_from_json(serialized) != result:
                raise ValueError
            payload = b'{"ok":true,"data":' + serialized.encode("utf-8") + b'}'
            if len(payload) > MAX_JSON_BYTES:
                raise ValueError
            return 200, payload
        except Exception:
            return workload_http_error("workload_source_unavailable")
        finally:
            self._slots.release()
