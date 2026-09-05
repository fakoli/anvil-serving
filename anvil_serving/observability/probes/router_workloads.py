"""Bounded authenticated reader for one declared local router workload source."""

from __future__ import annotations

import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Callable

from ...control_plane.authorization import AuthorizationError, _normalize_credential
from ...paths import LOOPBACK_ALIAS_ENV, runtime_url
from ...transports import _urlopen_no_proxy_no_redirect
from ..workload_collection import build_node_workloads
from ..workloads import (
    MAX_FUTURE_SECONDS,
    MAX_JSON_BYTES,
    ResultStatus,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadOwner,
    WorkloadQuery,
    node_result_from_json,
)


_ENDPOINT_RE = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})/v1/?\Z", re.ASCII)
_ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,255}\Z", re.ASCII)
_SOCKET_TIMEOUT = 1.0


def _single_router_source(node) -> SourceResult:
    if len(node.sources) == 1 and node.sources[0].owner is WorkloadOwner.ROUTER:
        return node.sources[0]
    raise WorkloadError(WorkloadErrorCode.INVALID, "invalid router workload source")


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


def _unavailable(fallback) -> SourceResult:
    return next(source for source in fallback.sources if source.owner is WorkloadOwner.ROUTER)


def _router_source(node) -> SourceResult:
    return next(source for source in node.sources if source.owner is WorkloadOwner.ROUTER)


def _failed(fallback, code: WorkloadErrorCode) -> SourceResult:
    return SourceResult(
        owner=WorkloadOwner.ROUTER,
        status=ResultStatus.UNAVAILABLE,
        collection_timestamp=fallback.collection_timestamp,
        records=(),
        truncation=Truncation(0, None),
        error=code,
    )


def _close_http_error(error: urllib.error.HTTPError) -> None:
    try:
        error.close()
    except Exception:
        pass
    try:
        if error.fp is not None:
            error.fp.close()
    except Exception:
        pass


def _close_response(response: object) -> bool:
    try:
        response.close()
    except Exception:
        return False
    return True


def _declared_endpoint(value: object) -> str | None:
    if type(value) is not str or not value or len(value) > 2048:
        return None
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return None
    matched = _ENDPOINT_RE.fullmatch(value)
    if matched is None or int(matched.group(1)) > 65535:
        return None
    return value


def _environment_value(
    environment: Mapping[str, str] | None, reference: str
) -> tuple[object, object] | None:
    if environment is None:
        source = os.environ
    elif isinstance(environment, Mapping):
        source = environment
    else:
        return None
    try:
        return source.get(reference), source.get(LOOPBACK_ALIAS_ENV)
    except Exception:
        return None


def _query_string(query: WorkloadQuery) -> str:
    values: list[tuple[str, str]] = []
    for name, value in (("owner", query.owner), ("kind", query.kind), ("state", query.state)):
        if value is not None:
            values.append((name, value.value))
    if query.host is not None:
        values.append(("host", query.host))
    values.extend(
        (
            ("active_only", "true" if query.active_only else "false"),
            ("recent_seconds", str(query.recent_seconds)),
            ("limit", str(query.limit)),
        )
    )
    return urllib.parse.urlencode(values)


def read_router_workloads(
    endpoint: str,
    auth_env: str,
    host: str,
    query: WorkloadQuery,
    now: datetime,
    *,
    environment: Mapping[str, str] | None = None,
    _open: Callable[..., object] | None = None,
) -> SourceResult:
    """Read one declared, authenticated router workload source without retries."""
    fallback = build_node_workloads(host, query, now, {})
    checked_query = _canonical_query(query)
    checked_now = fallback.collection_timestamp
    declared = _declared_endpoint(endpoint)
    if declared is None or type(auth_env) is not str or _ENV_RE.fullmatch(auth_env) is None:
        return _unavailable(fallback)
    values = _environment_value(environment, auth_env)
    if values is None:
        return _unavailable(fallback)
    credential_value, loopback_alias = values
    try:
        credential = _normalize_credential(credential_value)
        token = credential.decode("ascii")
    except (AuthorizationError, UnicodeError):
        return _unavailable(fallback)
    try:
        base = runtime_url(declared, environ={LOOPBACK_ALIAS_ENV: loopback_alias})
    except Exception:
        return _unavailable(fallback)
    url = base.rstrip("/") + "/workloads?" + _query_string(checked_query)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "Authorization": "Bearer " + token},
        method="GET",
    )
    opener = _urlopen_no_proxy_no_redirect if _open is None else _open
    response = None
    transport_error: WorkloadErrorCode | None = None
    payload: bytes | None = None
    try:
        response = opener(request, timeout=_SOCKET_TIMEOUT)
        if response.getcode() != 200:
            transport_error = WorkloadErrorCode.UNAVAILABLE
        else:
            candidate = response.read(MAX_JSON_BYTES + 1)
            if type(candidate) is not bytes or len(candidate) > MAX_JSON_BYTES:
                transport_error = WorkloadErrorCode.INVALID
            else:
                payload = candidate
    except urllib.error.HTTPError as exc:
        _close_http_error(exc)
        transport_error = WorkloadErrorCode.UNAVAILABLE
    except Exception:
        transport_error = WorkloadErrorCode.UNAVAILABLE
    finally:
        if response is not None and not _close_response(response):
            transport_error = WorkloadErrorCode.UNAVAILABLE
    if transport_error is not None:
        return _failed(fallback, transport_error)
    if payload is None:
        return _failed(fallback, WorkloadErrorCode.UNAVAILABLE)
    try:
        node = node_result_from_json(payload)
        if node.host != host:
            raise WorkloadError(WorkloadErrorCode.INVALID, "invalid router workload source")
        if node.collection_timestamp > checked_now + timedelta(seconds=MAX_FUTURE_SECONDS):
            raise WorkloadError(WorkloadErrorCode.FUTURE, "future router workload source")
        source = _single_router_source(node)
        if source.collection_timestamp > checked_now + timedelta(seconds=MAX_FUTURE_SECONDS):
            raise WorkloadError(WorkloadErrorCode.FUTURE, "future router workload source")
        result = build_node_workloads(host, checked_query, checked_now, {WorkloadOwner.ROUTER: source})
        return _router_source(result)
    except WorkloadError as exc:
        code = WorkloadErrorCode.FUTURE if exc.code is WorkloadErrorCode.FUTURE else WorkloadErrorCode.INVALID
        return _failed(fallback, code)
    except Exception:
        return _failed(fallback, WorkloadErrorCode.INVALID)


__all__ = ["read_router_workloads"]
