"""Fail-closed activation binding for frozen serving-campaign arms."""
from __future__ import annotations

import ipaddress
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import time
import tomllib
from urllib.parse import urlencode, urlsplit
import urllib.error
import urllib.request

from .jobs import BenchmarkJobError

_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _error(message):
    raise BenchmarkJobError("campaign_activation_mismatch", message)


def _url(value, *, loopback=False, suffix=None):
    if not isinstance(value, str) or any(ord(char) < 33 or ord(char) == 127 for char in value):
        _error("campaign activation URL is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        _error("campaign activation URL is invalid")
    if (parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password
            or not port or parsed.query or parsed.fragment or (suffix is None and parsed.path not in {"", "/"})
            or (suffix is not None and parsed.path != suffix)):
        _error("campaign activation URL is invalid")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        _error("campaign activation URL must use a literal IP address")
    if loopback and not address.is_loopback:
        _error("campaign observer must use literal loopback")
    return value.rstrip("/")


def validate_activation(arm):
    """Validate frozen private activation coordinates without probing them."""
    activation = arm.get("activation") if isinstance(arm, dict) else None
    expected = {"registry_path", "monitoring_receipt_path", "observer_base_url", "upstream_base_url", "container"}
    if not isinstance(activation, dict) or set(activation) != expected:
        _error("campaign arm requires an exact activation contract")
    for key in ("registry_path", "monitoring_receipt_path"):
        if not isinstance(activation[key], str) or not os.path.isabs(activation[key]):
            _error("campaign activation paths must be absolute")
    _url(activation["observer_base_url"], loopback=True)
    _url(activation["upstream_base_url"], suffix="/v1")
    if not isinstance(activation["container"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", activation["container"]):
        _error("campaign activation container is invalid")
    return activation


def _read_bytes(path, limit):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError()
            raw = stream.read(limit + 1)
    except (OSError, ValueError) as exc:
        raise BenchmarkJobError("campaign_activation_unavailable", "activation receipt is unavailable") from exc
    if len(raw) > limit:
        _error("activation receipt exceeds byte limit")
    return raw


def _read_json(path):
    try:
        return json.loads(_read_bytes(path, 65536))
    except (TypeError, ValueError) as exc:
        raise BenchmarkJobError("campaign_activation_unavailable", "activation receipt is malformed") from exc


def _load_registry(path):
    try:
        raw = _read_bytes(path, 4 * 1024 * 1024)
        registry = tomllib.loads(raw.decode("utf-8"))
        from .. import serve_recipes
        serve_recipes.validate_registry(registry)
        return registry, hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise BenchmarkJobError("campaign_activation_unavailable", "managed recipe registry is unavailable") from exc


def _local_address(url):
    parsed = urlsplit(url)
    address = ipaddress.ip_address(parsed.hostname or "")
    if address.is_global or address.is_link_local or address.is_multicast or address.is_unspecified:
        _error("activation upstream address is not local to this worker")
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    try:
        probe = socket.socket(family, socket.SOCK_DGRAM)
        with probe:
            probe.bind((str(address), 0))
    except OSError as exc:
        raise BenchmarkJobError("campaign_activation_unavailable", "activation upstream address is not owned by this worker") from exc


def _fetch_json(url, *, headers, fetch=None, timeout=3):
    try:
        if fetch is not None:
            status, raw = fetch(url, headers)
        else:
            request = urllib.request.Request(url, headers=headers, method="GET")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
            with opener.open(request, timeout=timeout) as response:
                status, raw = response.status, response.read(65537)
        if status != 200 or len(raw) > 65536:
            raise ValueError()
        return json.loads(raw)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, urllib.error.HTTPError) as exc:
        raise BenchmarkJobError("campaign_activation_unavailable", "activation metadata is unavailable") from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_activation(arm, *, runner=None, fetch=None):
    """Return the current generation only when frozen and live owners agree."""
    activation = validate_activation(arm)
    identity, endpoint = arm["identity"], arm["endpoint"]
    from .. import serve_recipes
    from ..transports import _run_bounded_process
    deadline = time.monotonic() + 8
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            _error("activation verification deadline expired")
        return value
    def bounded_runner(argv, **kwargs):
        if runner is not None:
            result = runner(argv, **{**kwargs, "timeout": remaining()})
        else:
            result = _run_bounded_process(argv, timeout=remaining(), max_output_bytes=4 * 1024 * 1024,
                                          env={"PATH": os.defpath})
            result = subprocess.CompletedProcess(argv, result.returncode,
                result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace"))
            if kwargs.get("check"):
                result.check_returncode()
        return result
    def discover():
        inventory = serve_recipes.discover_recipe_containers(_run=bounded_runner)
        return serve_recipes.select_recipe_container(inventory, container=activation["container"])
    try:
        registry, registry_hash = _load_registry(activation["registry_path"])
        recipe = serve_recipes.find_recipe(registry, identity["recipe_selector"])
        container = discover()
    except (OSError, ValueError, subprocess.SubprocessError, serve_recipes.RecipeError) as exc:
        raise BenchmarkJobError("campaign_activation_unavailable", "managed recipe identity is unavailable") from exc
    expected = {
        "model": identity["model"], "revision": identity["revision"], "image_digest": identity["image"],
        "recipe_digest": identity["recipe_sha256"], "registry_digest": registry_hash,
        "served_identity": identity["served_model"], "container": activation["container"],
    }
    if (recipe is None or recipe.get("model") != identity["model"]
            or (recipe.get("download") or {}).get("repo") != identity["model"]
            or (recipe.get("download") or {}).get("revision") != identity["revision"]
            or (recipe.get("serve") or {}).get("engine") != identity["runtime"]
            or serve_recipes.recipe_digest(recipe) != identity["recipe_sha256"]
            or not container.get("running") or any(container.get(key) != value for key, value in expected.items())
            or serve_recipes._recipe_time(container.get("started_at")) is None
            or container.get("bound_port") != (recipe.get("serve") or {}).get("port")
            or not container.get("bound_port") or sorted(container.get("gpu_selection") or []) != sorted(identity["hardware"])):
        _error("managed recipe does not match the frozen arm")
    upstream = _url(activation["upstream_base_url"], suffix="/v1")
    if urlsplit(upstream).port != container["bound_port"]:
        _error("activation upstream port differs from the managed container")
    _local_address(upstream)
    if upstream != endpoint["base_url"]:
        # Routed endpoints bind through the router fingerprint below.
        routed = True
    else:
        routed = False
        if endpoint["model"] != identity["served_model"]:
            _error("direct endpoint model differs from served model")
    receipt = _read_json(activation["monitoring_receipt_path"])
    fields = {"schema": "recipe-monitoring/v1", "state": "active", "container": container["container"],
              "container_id": container["container_id"], "model": identity["served_model"],
              "engine": identity["runtime"], "image_digest": identity["image"],
              "recipe_digest": identity["recipe_sha256"], "registry_digest": expected["registry_digest"],
              "bound_port": container["bound_port"], "gpu_selection": container["gpu_selection"],
              "started_at": container.get("started_at")}
    generation = receipt.get("generation") if isinstance(receipt, dict) else None
    if (not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in fields.items())
            or not isinstance(generation, str) or _HEX.fullmatch(generation) is None):
        _error("monitoring receipt does not bind the active recipe")
    from ..router.model_metadata import endpoint_identity_sha256
    from .requests import resolve_api_key
    try:
        key = resolve_api_key(endpoint.get("auth_env"))
    except ValueError as exc:
        raise BenchmarkJobError("missing_credential", "endpoint metadata credential is unavailable") from exc
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    path = "/models/fingerprints?" + urlencode({"model": endpoint["model"]}) if routed else "/models"
    payload = _fetch_json(endpoint["base_url"].rstrip("/") + path, headers=headers, fetch=fetch, timeout=min(3, remaining()))
    if routed:
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) != 1:
            _error("router fingerprint is ambiguous")
        row = rows[0]
        if (not isinstance(row, dict) or row.get("model") != identity["served_model"]
                or not isinstance(row.get("aliases"), list) or endpoint["model"] not in row["aliases"]
                or row.get("endpoint_sha256") != endpoint_identity_sha256(upstream, identity["served_model"])
                or not isinstance(row.get("readiness"), dict) or row["readiness"].get("loaded") is not True
                or not isinstance(row.get("served_identity"), dict)
                or row["served_identity"].get("observed") != identity["served_model"]):
            _error("router fingerprint does not bind the active endpoint")
    else:
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("id") != identity["served_model"]:
            _error("direct endpoint does not serve the frozen model")
    try:
        after = discover()
        stable = (*expected, "container_id", "started_at", "bound_port", "gpu_selection", "running")
        if (any(after.get(k) != container.get(k) for k in stable)
                or _load_registry(activation["registry_path"])[1] != registry_hash
                or _read_json(activation["monitoring_receipt_path"]) != receipt):
            _error("activation changed during verification")
        remaining()
    except (OSError, ValueError, subprocess.SubprocessError, serve_recipes.RecipeError) as exc:
        raise BenchmarkJobError("campaign_activation_unavailable", "activation recheck failed") from exc
    result = {"generation": generation, "metrics_url": activation["observer_base_url"] + "/engine/" + generation + "/metrics",
              "container_id": container["container_id"], "started_at": container.get("started_at"),
              "served_model": identity["served_model"]}
    result["binding_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result
