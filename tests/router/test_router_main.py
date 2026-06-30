"""Tests for ``python -m anvil_serving.router`` arg parsing (Bug fix: --host/--port were ignored).

The ``_parse_args`` helper is factored out of ``__main__`` so it can be
unit-tested without binding a real socket.
"""
from __future__ import annotations

import pytest

from anvil_serving.router.__main__ import _parse_args


def test_default_host_and_port():
    """No flags -> defaults 127.0.0.1:8000."""
    args = _parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_custom_host_and_port():
    """Explicit flags are returned and forwarded to serve()."""
    args = _parse_args(["--host", "127.0.0.1", "--port", "9001"])
    assert args.host == "127.0.0.1"
    assert args.port == 9001


def test_host_zero_zero_zero_zero():
    """0.0.0.0 is a valid bind address for listening on all interfaces."""
    args = _parse_args(["--host", "0.0.0.0", "--port", "9000"])
    assert args.host == "0.0.0.0"
    assert args.port == 9000


def test_port_is_parsed_as_int():
    """Port must be an integer, not a string."""
    args = _parse_args(["--port", "8080"])
    assert isinstance(args.port, int)
    assert args.port == 8080


def test_non_integer_port_is_rejected():
    """A non-integer port value must be rejected by argparse."""
    with pytest.raises(SystemExit) as exc:
        _parse_args(["--port", "not-a-number"])
    assert exc.value.code != 0
