"""Run the front door as a module: ``python -m anvil_serving.router``.

Starts on 127.0.0.1:8000 with the default echo backend (so the verification
curl works out of the box). This is the warning-free launch entry point;
``python -m anvil_serving.router.front_door`` also works but emits a benign
runpy RuntimeWarning because the package ``__init__`` imports ``front_door``.
"""

from .backends import EchoBackend
from .front_door import serve

if __name__ == "__main__":
    serve("127.0.0.1", 8000, EchoBackend())
