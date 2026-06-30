"""Run the front door as a module: ``python -m anvil_serving.router``.

Starts on 127.0.0.1:8000 with the default echo backend (so the verification
curl works out of the box). This is the warning-free launch entry point;
``python -m anvil_serving.router.front_door`` also works but emits a benign
runpy RuntimeWarning because the package ``__init__`` imports ``front_door``.

Pass ``--host`` / ``--port`` to override the defaults, e.g.::

    python -m anvil_serving.router --host 0.0.0.0 --port 9000
"""

import argparse
from typing import Optional, Sequence

from .backends import EchoBackend
from .front_door import serve


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments for the module entry point.

    Factored out so it can be unit-tested without binding a real socket.
    """
    p = argparse.ArgumentParser(
        prog="python -m anvil_serving.router",
        description="Start the router front door with the echo backend.",
    )
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address (default: 127.0.0.1)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=8000,
        help="bind port (default: 8000)",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    serve(args.host, args.port, EchoBackend())
