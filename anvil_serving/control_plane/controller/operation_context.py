"""Request-local identity for confirmed controller operation chaining."""

from __future__ import annotations

import contextvars
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class ControllerOperationContext:
    idempotency_key: str
    execution: Mapping[str, str]


_OPERATION_CONTEXT: contextvars.ContextVar[ControllerOperationContext | None] = (
    contextvars.ContextVar("anvil_controller_operation_context", default=None)
)


@contextmanager
def controller_operation_context(
    idempotency_key: str,
    execution: Mapping[str, str],
) -> Iterator[ControllerOperationContext]:
    """Bind one already-validated operation identity to its dispatch thread."""

    context = ControllerOperationContext(idempotency_key, dict(execution))
    token = _OPERATION_CONTEXT.set(context)
    try:
        yield context
    finally:
        _OPERATION_CONTEXT.reset(token)


def current_controller_operation_context() -> ControllerOperationContext | None:
    return _OPERATION_CONTEXT.get()


__all__ = [
    "ControllerOperationContext",
    "controller_operation_context",
    "current_controller_operation_context",
]
