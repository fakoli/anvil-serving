"""Explicit, principal-bound supplemental-memory routing.

The router owns alias-to-bank binding.  Callers can request only retain,
recall, and reflect through a small request vocabulary; bank administration and
caller-selected upstream settings never cross this boundary.
"""

from __future__ import annotations

from .admission import owned_dispatch

from contextlib import contextmanager
import json
import os
import re
import threading
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import quote

from .backends.relay import (
    RelayTimeoutError, Transport, _close_controlled_response, _controlled_stream_open,
    _interrupt_response,
)
from .config import MemoryRoute, normalize_model_alias
from .internal import BackendClientError
from .request_control import RequestControl, RequestControlError


_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_RECALL_BYTES = 32 * 1024
_MAX_CONCURRENCY = 4
_TAG_RE = re.compile(r"^[A-Za-z0-9._:/=-]+$")
_PATHS = {
    "retain": "/memories",
    "recall": "/memories/recall",
    "reflect": "/reflect",
}


def _memory_transport(url, *, data, headers, timeout, max_bytes):
    """Reuse relay's cancellable connection and bound the entire body read."""
    control = RequestControl(total_timeout_s=timeout, startup_timeout_s=timeout)
    control.start_upstream()
    response = _controlled_stream_open(url, data=data, headers=headers, timeout=timeout, control=control)
    def close():
        _interrupt_response(response)
    control.set_upstream_close(close)
    timer = threading.Timer(control.remaining_seconds(), control.cancel)
    timer.daemon = True
    timer.start()
    try:
        control.check()
        raw = response.read(max_bytes + 1)
        control.check()
        if len(raw) > max_bytes:
            raise ValueError("memory response exceeds limit")
        return raw
    except Exception:
        control.check()
        raise
    finally:
        timer.cancel()
        control.clear_upstream_close(close)
        _close_controlled_response(response)


class MemoryError(Exception):
    """A safe, caller-facing memory capability error."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class MemoryRouter:
    """Dispatch bounded memory operations to their configured bank.

    Tokens are resolved once at construction and are only used in the outbound
    Authorization header.  There is deliberately no fallback between routes or
    memory backends.
    """

    def __init__(
        self,
        routes: Sequence[MemoryRoute],
        *,
        env: Optional[Mapping[str, str]] = None,
        transport: Optional[Transport] = None,
    ) -> None:
        environ: Mapping[str, str] = os.environ if env is None else env
        self._transport: Transport = transport or _memory_transport
        self._routes: dict[tuple[str, str], tuple[MemoryRoute, Optional[str]]] = {}
        self._aliases: set[str] = set()
        for route in routes:
            alias = normalize_model_alias(route.alias)
            self._aliases.add(alias)
            token = environ.get(route.auth_env)
            if not isinstance(token, str) or not 1 <= len(token) <= 8192 or any(ord(c) < 33 or ord(c) > 126 for c in token):
                token = None
            self._routes[(alias, route.principal)] = (route, token)
        self._limit = threading.BoundedSemaphore(_MAX_CONCURRENCY)
        self._tracking = threading.local()

    @contextmanager
    def track(self, invocation):
        """Bind one trusted handler invocation across REST/MCP dispatch only."""
        from .internal import UsageInvocation
        if type(invocation) is not UsageInvocation:
            raise ValueError("invalid accounting invocation")
        prior = getattr(self._tracking, "invocation", None)
        self._tracking.invocation = invocation
        try:
            yield
        finally:
            self._tracking.invocation = prior

    @owned_dispatch("memory")
    def dispatch(self, body: Mapping[str, Any], *, principal: str) -> dict:
        """Validate and dispatch one memory request for ``principal``."""
        if not isinstance(principal, str) or not principal.strip():
            raise MemoryError(401, "authentication_required", "memory authentication is required")
        if not isinstance(body, Mapping) or set(body) != {"alias", "operation", "arguments"}:
            raise MemoryError(422, "invalid_request", "memory request must contain alias, operation, and arguments")
        alias_value = body["alias"]
        if not isinstance(alias_value, str) or not alias_value.strip():
            raise MemoryError(422, "invalid_request", "memory alias must be a non-empty string")
        alias = normalize_model_alias(alias_value)
        route_binding = self._routes.get((alias, principal))
        if route_binding is None:
            if alias in self._aliases:
                raise MemoryError(403, "memory_forbidden", "memory alias is not available to this principal")
            raise MemoryError(404, "memory_not_found", "unknown memory alias")
        route, token = route_binding
        operation = body["operation"]
        if not isinstance(operation, str) or operation not in _PATHS:
            raise MemoryError(422, "invalid_request", "unsupported memory operation")
        arguments = body["arguments"]
        payload = _payload(operation, arguments)
        if route.backend == "hermes":
            raise MemoryError(501, "unsupported_backend", "selected memory backend is not available")
        if route.backend != "hindsight" or token is None:
            raise MemoryError(503, "memory_unavailable", "selected memory route is unavailable")
        if not self._limit.acquire(blocking=False):
            raise MemoryError(503, "memory_busy", "selected memory route is busy")
        try:
            invocation = getattr(self._tracking, "invocation", None)
            if invocation is not None:
                from .usage_store import RouteAssociation
                invocation.route = RouteAssociation(route_id=route.alias)
                invocation.dispatch()
            raw = self._transport(
                route.base_url.rstrip("/") + "/v1/default/banks/" + quote(route.bank, safe="") + _PATHS[operation],
                data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                timeout=route.timeout,
                max_bytes=_MAX_RESPONSE_BYTES,
            )
            result = json.loads(raw.decode("utf-8"))
            if not isinstance(result, dict):
                raise ValueError("response is not an object")
            if operation == "recall":
                result = _recall_result(result)
        except (RequestControlError, RelayTimeoutError, TimeoutError):
            raise MemoryError(504, "memory_timeout", "selected memory route timed out") from None
        except BackendClientError as exc:
            if exc.status in {400, 413, 422}:
                raise MemoryError(exc.status, "memory_rejected", "selected memory route rejected the request") from None
            raise MemoryError(503, "memory_unavailable", "selected memory route is unavailable") from None
        except Exception:
            raise MemoryError(503, "memory_unavailable", "selected memory route is unavailable") from None
        finally:
            self._limit.release()
        return {"alias": route.alias, "backend": "hindsight", "operation": operation, "result": result}

    def aliases(self, principal: str) -> tuple[str, ...]:
        """Return the configured aliases available to one authenticated principal."""
        if not isinstance(principal, str) or not principal.strip():
            return ()
        return tuple(sorted(alias for alias, route_principal in self._routes if route_principal == principal))


def tool_schemas(aliases: Sequence[str]) -> tuple[dict, ...]:
    """Return compact function schemas for the three caller-safe operations."""
    alias = {"type": "string", "enum": list(aliases)}
    tags = {
        "type": "array", "maxItems": 16,
        "items": {"type": "string", "minLength": 1, "maxLength": 128},
    }
    common = {
        "type": "object", "additionalProperties": False,
        "properties": {"alias": alias, "tags": tags},
    }
    retain = dict(common, required=["alias", "content"], properties={
        **common["properties"],
        "content": {"type": "string", "minLength": 1, "maxLength": 32768},
        "context": {"type": "string", "minLength": 1, "maxLength": 1024},
    })
    def query_schema(max_tokens: int, default_budget: str, default_tokens: int) -> dict:
        return dict(common, required=["alias", "query"], properties={
            **common["properties"],
            "query": {"type": "string", "minLength": 1, "maxLength": 4096},
            "budget": {"type": "string", "enum": ["low", "mid", "high"], "default": default_budget},
            "max_tokens": {"type": "integer", "minimum": 1, "maximum": max_tokens, "default": default_tokens},
        })
    return (
        {"type": "function", "function": {"name": "memory_retain", "parameters": retain}},
        {"type": "function", "function": {"name": "memory_recall", "parameters": query_schema(8192, "mid", 1024)}},
        {"type": "function", "function": {"name": "memory_reflect", "parameters": query_schema(4096, "low", 4096)}},
    )


def _payload(operation: str, arguments: Any) -> dict:
    if not isinstance(arguments, Mapping):
        raise MemoryError(422, "invalid_request", "memory arguments must be an object")
    if operation == "retain":
        allowed = {"content", "context", "tags"}
        _exact_keys(arguments, allowed)
        content = _string(arguments.get("content"), "content", 32768)
        item: dict[str, Any] = {"content": content}
        if "context" in arguments:
            item["context"] = _string(arguments["context"], "context", 1024)
        if "tags" in arguments:
            item["tags"] = _tags(arguments["tags"])
        return {"items": [item], "async": False}
    allowed = {"query", "budget", "max_tokens", "tags"}
    _exact_keys(arguments, allowed)
    query = _string(arguments.get("query"), "query", 4096)
    default_budget = "mid" if operation == "recall" else "low"
    budget = arguments.get("budget", default_budget)
    if not isinstance(budget, str) or budget not in {"low", "mid", "high"}:
        raise MemoryError(422, "invalid_request", "memory budget must be low, mid, or high")
    max_limit = 8192 if operation == "recall" else 4096
    max_tokens = arguments.get("max_tokens", 1024 if operation == "recall" else 4096)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= max_limit:
        raise MemoryError(422, "invalid_request", "memory max_tokens is out of range")
    result = {"query": query, "budget": budget, "max_tokens": max_tokens}
    if operation == "recall":
        result["include"] = {"entities": None, "chunks": None, "source_facts": None}
    if "tags" in arguments:
        result["tags"] = _tags(arguments["tags"])
    return result


def _recall_result(result: dict) -> dict:
    """Keep a ranked prefix of complete facts, including their source references."""
    facts = result.get("results")
    if not isinstance(facts, list) or any(
        not isinstance(fact, dict) or not isinstance(fact.get("id"), str)
        or not isinstance(fact.get("text"), str) for fact in facts
    ):
        raise ValueError("invalid recall results")
    bounded: dict[str, Any] = {"results": [], "truncated": False, "omitted_results": len(facts)}
    size = len(json.dumps(bounded).encode("utf-8"))
    for fact in facts:
        fact_size = len(json.dumps(fact).encode("utf-8")) + (2 if bounded["results"] else 0)
        if size + fact_size > _MAX_RECALL_BYTES:
            break
        bounded["results"].append(fact)
        size += fact_size
    bounded["omitted_results"] = len(facts) - len(bounded["results"])
    bounded["truncated"] = bounded["omitted_results"] > 0
    return bounded


def _exact_keys(arguments: Mapping[str, Any], allowed: set[str]) -> None:
    if set(arguments) - allowed:
        raise MemoryError(422, "invalid_request", "memory arguments contain unsupported fields")


def _string(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise MemoryError(422, "invalid_request", f"memory {name} is invalid")
    return value


def _tags(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 16:
        raise MemoryError(422, "invalid_request", "memory tags are invalid")
    if any(not isinstance(tag, str) or not tag or len(tag) > 128 or not _TAG_RE.fullmatch(tag) for tag in value):
        raise MemoryError(422, "invalid_request", "memory tags are invalid")
    return list(value)
