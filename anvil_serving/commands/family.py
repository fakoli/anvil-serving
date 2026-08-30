"""Decorator and type for one independently testable command family."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .spec import CommandNode


CommandFactory = Callable[[], CommandNode | tuple[CommandNode, ...]]


@dataclass(frozen=True)
class CommandFamily:
    """A lazily built implementation family and fallback help category."""

    category: str
    factory: CommandFactory

    def build(self) -> tuple[CommandNode, ...]:
        result = self.factory()
        return result if isinstance(result, tuple) else (result,)


def command_family(*, category: str) -> Callable[[CommandFactory], CommandFamily]:
    """Decorate one module-local factory as an explicit command family."""

    if not category.strip():
        raise ValueError("command family category must not be empty")

    def decorate(factory: CommandFactory) -> CommandFamily:
        return CommandFamily(category=category, factory=factory)

    return decorate
