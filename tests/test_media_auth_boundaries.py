"""Authentication isolation for the two managed-media controller edges."""

from __future__ import annotations

import contextlib
import http.client
import os
import tempfile
import threading

from anvil_serving import controller


LIFECYCLE_TOKEN = "lifecycle-controller-test-secret"
RESOURCE_TOKEN = "resource-controller-test-secret"


@contextlib.contextmanager
def _running_controller(auth_env: str, token: str, operation: str):
    with tempfile.TemporaryDirectory(prefix="anvil-media-auth-test-") as temp_dir:
        server = controller.make_server(
            "127.0.0.1",
            0,
            auth_token_env=auth_env,
            env={auth_env: token},
            allow_unauthenticated_loopback=False,
            allowed_operations=(operation,),
            idempotency_db_path=os.path.join(temp_dir, "operations.sqlite3"),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[:2]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def _health(host: str, port: int, token: str) -> int:
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request(
            "GET",
            "/health",
            headers={"Authorization": "Bearer " + token},
        )
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def test_media_controller_tokens_are_not_cross_usable():
    with _running_controller(
        "ANVIL_MEDIA_CONTROLLER_TOKEN",
        LIFECYCLE_TOKEN,
        "media-worker-status",
    ) as (host, port):
        assert _health(host, port, LIFECYCLE_TOKEN) == 200
        assert _health(host, port, RESOURCE_TOKEN) == 401

    with _running_controller(
        "ANVIL_MEDIA_RESOURCE_CONTROLLER_TOKEN",
        RESOURCE_TOKEN,
        "serves-status",
    ) as (host, port):
        assert _health(host, port, RESOURCE_TOKEN) == 200
        assert _health(host, port, LIFECYCLE_TOKEN) == 401
