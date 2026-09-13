"""Entry point for the independent, stdlib-only Connect manager zipapp."""
from __future__ import annotations

import json
import sys

from .cli import dispatch


def main() -> None:
    result = dispatch(sys.argv[1:], prog="anvil-connect-ctl")
    if result.error:
        print(json.dumps({"ok": False, "code": result.error.code, "error": str(result.error),
                          **({"data": result.data} if result.data is not None else {})}))
    else:
        print(json.dumps({"ok": True, "data": result.data}, sort_keys=True))
    raise SystemExit(result.exit_code)
