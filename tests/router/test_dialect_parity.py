"""Dialect parity: both shipped wire dialects implement the same router surface (#46).

The front door (``front_door.py``) calls a FIXED surface on whichever
:class:`~anvil_serving.router.dialects.Dialect` a route resolves to::

    dialect.name
    dialect.parse_request(body)
    dialect.stream(request, deltas, get_structured=...)
    dialect.render(request, text, structured=...)
    dialect.render_error(status, etype, message)

The ``Dialect`` Protocol is ``runtime_checkable`` but ``isinstance`` only checks
attribute NAMES — and the Protocol does NOT even declare the keyword-only
``get_structured`` / ``structured`` parameters the router actually passes. So a
new dialect could satisfy the Protocol yet silently miss a method the router
relies on, or a keyword it depends on. This parity test pins the REAL required
surface across BOTH dialects so a future third dialect can't regress it.

Focused parity assertions — not a framework. Hermetic, stdlib + pytest only.
"""

from __future__ import annotations

import inspect

import pytest

from anvil_serving.router.dialects import Dialect
from anvil_serving.router.dialects.anthropic import AnthropicDialect
from anvil_serving.router.dialects.openai import OpenAIDialect

# The two shipped dialects, instantiated.  A new dialect added to the codebase
# should be added here so it is held to the same parity contract.
DIALECT_CLASSES = (AnthropicDialect, OpenAIDialect)

# The surface the router (front_door.py) relies on for EVERY dialect.
REQUIRED_METHODS = ("parse_request", "stream", "render", "render_error")

# Keyword params the router passes that the Protocol does NOT declare — pinned
# here so a dialect that drops them is caught (the text path silently changes).
REQUIRED_KWARGS = {"stream": "get_structured", "render": "structured"}


def _dialects():
    return [cls() for cls in DIALECT_CLASSES]


@pytest.mark.parametrize("dialect", _dialects(), ids=lambda d: d.name)
def test_dialect_satisfies_runtime_protocol(dialect):
    """Every dialect ``isinstance``-satisfies the runtime_checkable Protocol."""
    assert isinstance(dialect, Dialect)


@pytest.mark.parametrize("dialect", _dialects(), ids=lambda d: d.name)
def test_dialect_has_nonempty_string_name(dialect):
    assert isinstance(dialect.name, str) and dialect.name


@pytest.mark.parametrize("dialect", _dialects(), ids=lambda d: d.name)
def test_dialect_exposes_all_required_methods(dialect):
    for method in REQUIRED_METHODS:
        assert callable(getattr(dialect, method, None)), (
            f"{dialect.name!r} dialect is missing required method {method!r}"
        )


def test_dialects_expose_identical_public_surface():
    """Both dialects must present the SAME public attribute/method set.

    Compared as sets so a method present on one but not the other fails here,
    by construction — the parity guarantee a new dialect must uphold.
    """
    surfaces = {
        cls.__name__: frozenset(n for n in dir(cls()) if not n.startswith("_"))
        for cls in DIALECT_CLASSES
    }
    distinct = set(surfaces.values())
    assert len(distinct) == 1, (
        f"dialects diverge in public surface: {surfaces}"
    )
    # And that shared surface must cover everything the router relies on.
    shared = next(iter(distinct))
    assert {"name", *REQUIRED_METHODS} <= shared


@pytest.mark.parametrize("dialect", _dialects(), ids=lambda d: d.name)
@pytest.mark.parametrize("method,kwarg", sorted(REQUIRED_KWARGS.items()))
def test_dialect_methods_accept_router_passed_kwargs(dialect, method, kwarg):
    """``stream``/``render`` must accept the structured-fields kwarg the router passes.

    The Protocol omits these keyword-only params, so this is the only guard that a
    new dialect keeps them (front_door.py calls ``stream(..., get_structured=...)``
    and ``render(..., structured=...)`` unconditionally).
    """
    sig = inspect.signature(getattr(dialect, method))
    assert kwarg in sig.parameters, (
        f"{dialect.name!r} dialect's {method}() must accept a {kwarg!r} keyword"
    )


def test_shipped_dialect_names_are_distinct_and_expected():
    names = [cls().name for cls in DIALECT_CLASSES]
    assert names == ["anthropic", "openai"]
    assert len(set(names)) == len(names)  # no two dialects share a name
