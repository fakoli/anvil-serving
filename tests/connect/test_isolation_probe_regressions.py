"""No-VM regressions for rejection outcomes and activation failure injection."""
import ast
import importlib.util
from pathlib import Path
import socket
import subprocess
import sys
import threading

import pytest


pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='requires Linux Unix-socket guest probes')

_PATH = Path(__file__).parents[2] / 'connect/test/vm/guest.py'
_SPEC = importlib.util.spec_from_file_location('guest_probe_regression', _PATH)
assert _SPEC and _SPEC.loader
guest = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(guest)


@pytest.mark.parametrize('mode,denied,expected', [
    ('reset', True, 0), ('redirect', True, 1),
    ('redirect', False, 0), ('forbidden', False, 1), ('reset', False, 1),
])
def test_probe_against_real_unix_socket(tmp_path, mode, denied, expected):
    path = tmp_path / 'ingress.sock'
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    server.listen(1)
    server.settimeout(5)
    observed = []

    def serve():
        try:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(5)
                # Peek leaves the request unread. Closing then produces the
                # reset rejected-peer clients observe on Linux.
                request = connection.recv(4096, socket.MSG_PEEK)
                observed.append(request)
                if mode != 'reset':
                    connection.recv(4096)
                    status = b'302 Found' if mode == 'redirect' else b'403 Forbidden'
                    connection.sendall(b'HTTP/1.1 ' + status + b'\r\nContent-Length: 0\r\n\r\n')
        finally:
            server.close()

    worker = threading.Thread(target=serve)
    worker.start()
    try:
        script = guest._ingress_probe_script(denied=denied).replace("'/run/anvil-test/ingress/ingress.sock'", repr(str(path)))
        result = subprocess.run([sys.executable, '-I', '-c', script], timeout=6,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert result.returncode == expected
    finally:
        worker.join(6)
        assert not worker.is_alive()
    assert observed and observed[0].endswith(b'\r\n\r\n')
    assert b'\\r' not in observed[0]


def test_missing_ingress_socket_is_not_a_passing_rejection(tmp_path):
    script = guest._ingress_probe_script(denied=True).replace("'/run/anvil-test/ingress/ingress.sock'", repr(str(tmp_path / 'absent.sock')))
    result = subprocess.run([sys.executable, '-I', '-c', script], timeout=4,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert result.returncode != 0


def test_failure_injection_targets_the_declared_tunnel_listener():
    tree = ast.parse(_PATH.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_restart_and_rollback')
    assignments = [n for n in function.body if isinstance(n, ast.Assign)]
    selected = next(n for n in assignments if isinstance(n.value, ast.Constant) and n.value.value == '127.0.0.1:27179')
    declaration = guest.build_manifest()
    original = declaration['gateway']['gateway']['listen']
    assignment = ast.Module(body=[selected], type_ignores=[])
    exec(compile(assignment, '<closed fixture assignment>', 'exec'), {'rejected': declaration})
    assert declaration['gateway']['tunnel_listen'] == '127.0.0.1:27179'
    assert declaration['gateway']['gateway']['listen'] == original
