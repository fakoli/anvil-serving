#!/usr/bin/env python
"""Public compatibility facade for benchmark workflows."""

import os
import sys

if __package__ in (None, ""):  # direct ``python anvil_serving/benchmark.py``
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "anvil_serving"

from .benchmarking import cli as _cli
from .benchmarking.requests import (
    detect_max_model_len,
    post_chat,
    stream_chat,
)

__all__ = ["detect_max_model_len", "main", "post_chat", "stream_chat"]


def main(argv=None, *, prog=None):
    """Delegate CLI coordination while preserving facade monkeypatch seams."""
    return _cli.main(
        argv,
        prog=prog,
        post_request=post_chat,
        stream_request=stream_chat,
        detect_context_limit=detect_max_model_len,
    )


if __name__ == "__main__":
    raise SystemExit(main())
