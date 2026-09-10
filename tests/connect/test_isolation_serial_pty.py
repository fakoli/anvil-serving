"""Exercise the fixture's serial writer on an isolated pseudo-terminal."""
import importlib.util
import json
import os
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "posix", reason="PTY qualification requires POSIX")
def test_guest_result_crosses_real_tty_as_one_canonical_frame(monkeypatch, capsys):
    import pty
    import selectors
    import tty

    source = Path(__file__).resolve().parents[2] / "connect/test/vm/guest.py"
    spec = importlib.util.spec_from_file_location("guest_serial_test", source)
    guest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guest)
    result = {"schema": guest.RESULT_SCHEMA, "ok": True,
              "cases": [{"name": name, "status": "passed"} for name in guest.CASES], "service_samples": {}}
    expected = ("\n" + guest._result_line(result) + "\n").encode("ascii")
    master, slave = pty.openpty()
    try:
        tty.setraw(slave)
        monkeypatch.setattr(guest, "SERIAL_DEVICE", os.ttyname(slave))
        guest._write_serial_result(result)
        os.set_blocking(master, False)
        received = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(master, selectors.EVENT_READ)
            while len(received) < len(expected):
                assert selector.select(1), "serial frame was not delivered"
                received.extend(os.read(master, 4096))
        assert bytes(received) == expected
        assert received.count(guest.MARKER.encode("ascii")) == 1
        assert json.loads(received.split(b" ", 1)[1]) == result
        assert capsys.readouterr().out == ""
    finally:
        os.close(master)
        os.close(slave)
