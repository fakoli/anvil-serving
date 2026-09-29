"""Fresh, bounded model/runtime identity from an approved activation receipt."""

from __future__ import annotations

import hashlib
import json
import re
from time import monotonic
import urllib.request
from datetime import datetime, timedelta, timezone

from ...controller_diagnostics import _capture_fixed_child, local_docker_prefix
from ... import serve_recipes
from ...client_catalog_sync import fetch_client_catalog
from ..propagation import ActiveIdentity, _digest, _id
from .propagation_job_store import PropagationJobError


_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_OWNER_FIELDS = frozenset({"container_id", "runtime_digest", "served_identity", "bound_port"})
_OBSERVATION_DEADLINE_SECONDS = 20


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _runtime_digest(row: dict) -> str:
    """Fingerprint immutable Docker start inputs; omit environment and its secrets."""
    config, host = row["Config"], row["HostConfig"]
    projection = {
        "schema": "anvil-propagation-runtime/v1",
        "image": row["Image"], "args": row["Args"],
        "entrypoint": config["Entrypoint"], "command": config["Cmd"],
        "image_ref": config["Image"],
        "recipe_labels": {key: value for key, value in config["Labels"].items()
                          if key.startswith("io.anvil-serving.recipe.")
                          or key == serve_recipes.RECIPE_MANAGED_LABEL},
        "mounts": sorted(({"source": mount["Source"], "destination": mount["Destination"],
                           "type": mount["Type"], "rw": mount["RW"]}
                          for mount in row["Mounts"]), key=lambda item: item["destination"]),
        "port_bindings": host["PortBindings"], "device_requests": host["DeviceRequests"],
    }
    return hashlib.sha256(json.dumps(projection, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode("ascii")).hexdigest()


def _bound_port(row: dict, expected: int) -> None:
    """Both Docker's requested and observed binding must own exact loopback."""
    def matches(bindings):
        found = []
        for port, rows in bindings.items():
            for item in rows or []:
                if item.get("HostPort") == str(expected):
                    found.append((port, item.get("HostIp"), item.get("HostPort")))
        return found
    requested = matches(row["HostConfig"]["PortBindings"])
    observed = matches(row["NetworkSettings"]["Ports"])
    if len(requested) != 1 or requested != observed or requested[0][1] != "127.0.0.1":
        raise ValueError()


class ObservedActiveIdentity:
    """Accept only the exact previously approved container and fresh endpoints."""

    def __init__(self, activation_ref: str, catalog_sha256: str, owners: list[dict],
                 router_base_url: str, router_token_env: str, observer: dict | None = None):
        try:
            self.ref = _id(activation_ref)
            self.catalog_sha256 = _digest(catalog_sha256)
            if (type(router_base_url) is not str or not router_base_url
                    or type(router_token_env) is not str or not router_token_env
                    or type(owners) is not list or not 1 <= len(owners) <= 2):
                raise ValueError()
            frozen = []
            for owner in owners:
                if (type(owner) is not dict or set(owner) != _OWNER_FIELDS
                        or type(owner["container_id"]) is not str
                        or _CONTAINER_ID.fullmatch(owner["container_id"]) is None
                        or _digest(owner["runtime_digest"]) != owner["runtime_digest"]
                        or type(owner["served_identity"]) is not str
                        or not 1 <= len(owner["served_identity"]) <= 512
                        or type(owner["bound_port"]) is not int
                        or not 1 <= owner["bound_port"] <= 65535):
                    raise ValueError()
                frozen.append(dict(owner))
            if len({owner["container_id"] for owner in frozen}) != len(frozen):
                raise ValueError()
            self.owners = tuple(sorted(frozen, key=lambda owner: owner["container_id"]))
            self.router_base_url = router_base_url
            self.router_token_env = router_token_env
            if observer is not None and (type(observer) is not dict or set(observer) != {
                    "controller_url", "token_file", "profile_sha256"}):
                raise ValueError()
            self.observer = observer
            fingerprint = {"schema": "anvil-propagation-activation/v1",
                           "catalog_sha256": self.catalog_sha256, "owners": self.owners}
            self.digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, separators=(",", ":"),
                                                   ensure_ascii=True).encode("ascii")).hexdigest()
        except (TypeError, ValueError):
            raise PropagationJobError("active_identity_unavailable") from None

    def __call__(self) -> ActiveIdentity:
        if self.observer is not None:
            return self._remote()
        try:
            deadline = monotonic() + _OBSERVATION_DEADLINE_SECONDS
            def remaining() -> float:
                value = deadline - monotonic()
                if value <= 0:
                    raise ValueError()
                return value

            catalog = fetch_client_catalog(base_url=self.router_base_url,
                                           api_key_env=self.router_token_env, timeout_seconds=5)
            remaining()
            if catalog["config_sha256"] != self.catalog_sha256:
                raise ValueError()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
            for owner in self.owners:
                remaining()
                capture = _capture_fixed_child(local_docker_prefix() + ("inspect", owner["container_id"]),
                                               merged=False)
                remaining()
                if capture.state != "ok" or capture.truncated:
                    raise ValueError()
                rows = json.loads(capture.stdout)
                if type(rows) is not list or len(rows) != 1:
                    raise ValueError()
                row = rows[0]
                if (row["Id"] != owner["container_id"] or row["State"]["Running"] is not True
                        or row["State"]["Status"] != "running"
                        or _runtime_digest(row) != owner["runtime_digest"]):
                    raise ValueError()
                _bound_port(row, owner["bound_port"])
                request = urllib.request.Request(f"http://127.0.0.1:{owner['bound_port']}/v1/models")
                with opener.open(request, timeout=min(5, remaining())) as response:
                    if response.status != 200:
                        raise ValueError()
                    raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError()
                remaining()
                payload = json.loads(raw)
                if (type(payload) is not dict or type(payload.get("data")) is not list
                        or len(payload["data"]) != 1 or type(payload["data"][0]) is not dict
                        or payload["data"][0].get("id") != owner["served_identity"]):
                    raise ValueError()
            remaining()
            return ActiveIdentity(self.ref, self.digest)
        except Exception:
            raise PropagationJobError("active_identity_unavailable") from None

    def _remote(self) -> ActiveIdentity:
        """Use the scoped resource controller, never a Docker socket in the worker owner."""
        try:
            from ... import mcp
            from ..mcp.controller_client import remote_controller_request, resolve_controller_token_file
            observer = self.observer
            assert observer is not None
            expected_profile = _digest(observer["profile_sha256"])
            token = resolve_controller_token_file(observer["token_file"])
            response = remote_controller_request(observer["controller_url"], {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "propagation.activation.observe.v1", "arguments": {},
                           "_meta": {"io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
                                     "io.modelcontextprotocol/clientCapabilities": {},
                                     "io.modelcontextprotocol/clientInfo": {
                                         "name": "anvil-propagation-owner", "version": mcp.SERVER_INFO["version"]}}},
            }, token, timeout=_OBSERVATION_DEADLINE_SECONDS, max_response_bytes=8192)
            result = response["result"]
            content = result["structuredContent"]
            data = content["data"]
            observed = datetime.fromisoformat(data["observed_at"].replace("Z", "+00:00"))
            if (result.get("isError") is not False or content.get("ok") is not True
                    or data["observer_profile_sha256"] != expected_profile
                    or data["activation_ref"] != self.ref
                    or data["activation_digest"] != self.digest
                    or observed.tzinfo != timezone.utc
                    or not timedelta(0) <= datetime.now(timezone.utc) - observed <= timedelta(seconds=30)):
                raise ValueError()
            return ActiveIdentity(self.ref, self.digest)
        except Exception:
            raise PropagationJobError("active_identity_unavailable") from None
