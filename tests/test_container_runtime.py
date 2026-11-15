"""Container-controller host boundary tests."""

from __future__ import annotations


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False
