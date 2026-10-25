"""Publish a non-secret local recipe identity for an unprivileged observer.

Opt in with recipe-monitoring.json in the operator home. The lifecycle writer
owns monitoring/active-recipe.json; consumers mount that directory read-only.
This registration is not a lease or proof that a container is still running.
Consumers must independently verify health and model identity on every poll.
"""
import argparse
import copy
import http.client
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import tempfile
import time

from .paths import config_path

SCHEMA = "recipe-monitoring/v1"
MAX_BYTES = 65536


def read_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("monitoring input must be a regular file")
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("monitoring input exceeds byte bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("monitoring input must be an object")
    return value


def configuration():
    try:
        value = read_json(config_path("recipe-monitoring.json"))
    except FileNotFoundError:
        return None
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or not {"schema", "ports", "engine_contracts"} <= set(value)
            or set(value) - {"schema", "ports", "engine_contracts", "model_api_key_files"}
            or not isinstance(value["ports"], list) or not 1 <= len(value["ports"]) <= 16
            or any(type(p) is not int or not 1 <= p <= 65535 for p in value["ports"])
            or len(set(value["ports"])) != len(value["ports"])
            or not isinstance(value["engine_contracts"], dict)
            or not 1 <= len(value["engine_contracts"]) <= 16
            or any(not isinstance(k, str) or not 1 <= len(k) <= 128
                   or not isinstance(v, str) or v not in {"vllm", "sglang", "flare", "ignis"}
                   for k, v in value["engine_contracts"].items())):
        raise ValueError("invalid recipe-monitoring.json contract")
    keys = value.get("model_api_key_files", {})
    from .recipe_secrets import safe_path
    if not isinstance(keys, dict) or any(p not in {str(v) for v in value['ports']} for p in keys):
        raise ValueError("model_api_key_files must map declared ports to protected files")
    for path in keys.values():
        safe_path(path)
    return value


def receipt_path():
    return Path(config_path("monitoring", "active-recipe.json"))


def _write(value):
    path = receipt_path()
    path.parent.mkdir(mode=0o755, exist_ok=True)
    if path.parent.is_symlink():
        raise ValueError("monitoring directory must not be a symlink")
    fd, temporary = tempfile.mkstemp(prefix=".active-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def withdraw(*, port=None, container=None, pending=False):
    """Invalidate old scrape paths before a selected lifecycle mutation."""
    config = configuration()
    if config is None and not os.path.lexists(receipt_path()):
        return None
    from .serve_recipes import registry_lock
    with registry_lock(receipt_path()):
        if config is None or port not in config["ports"]:
            try:
                previous = read_json(receipt_path())
            except FileNotFoundError:
                return None
            if previous.get("schema") != SCHEMA:
                raise ValueError("invalid monitoring receipt schema")
            same_container = bool(container) and previous.get("container") == container
            same_port = (type(port) is int and type(previous.get("bound_port")) is int
                         and previous["bound_port"] == port)
            if not same_container and not same_port:
                return None
        generation = secrets.token_hex(32)
        _write({"schema": SCHEMA, "state": "pending" if pending and config is not None else "withdrawn",
                "generation": generation, "container": container, "updated": time.time()})
        return generation


def publish(recipe, container, *, registry_digest=None, _run=subprocess.run, _fetch=None):
    """Bind one exact running container and verified model to a new generation."""
    config = configuration()
    port = recipe["serve"].get("port")
    if config is None or port not in config["ports"]:
        return None
    from . import serve_recipes
    engine = recipe["serve"]["engine"]
    contract = config["engine_contracts"].get(engine)
    if contract is None:
        raise ValueError("recipe engine has no declared monitoring contract")
    before = read_json(receipt_path())
    if before.get("state") not in {"pending", "active"} or before.get("container") != container:
        raise ValueError("monitoring registration was withdrawn or superseded")

    from .transports import _run_bounded_process
    deadline = time.monotonic() + 12

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise ValueError("monitoring verification deadline expired")
        return value

    def bounded_run(argv, **kwargs):
        if _run is not subprocess.run:
            return _run(argv, **{**kwargs, "timeout": remaining()})
        result = _run_bounded_process(argv, timeout=remaining(), max_output_bytes=4 * 1024 * 1024)
        completed = subprocess.CompletedProcess(argv, result.returncode,
            result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace"))
        if kwargs.get("check"):
            completed.check_returncode()
        return completed

    def discover():
        inventory = serve_recipes.discover_recipe_containers(_run=bounded_run, strict=True)
        active = [r for r in inventory["containers"] if r["running"] and
                  any(p in config["ports"] for p in r["bound_ports"])]
        if len(active) != 1:
            raise ValueError("monitoring requires exactly one running recipe in its configured slot")
        row = active[0]
        if (row["container"] != container or row.get("model") != recipe["model"]
                or row.get("bound_endpoints") != [
                    {"host_ip": "127.0.0.1", "host_port": port, "container_port": port}]
                or row["bound_ports"] != [port]
                or row["recipe_digest"] != serve_recipes.recipe_digest(recipe)
                or not registry_digest or row["registry_digest"] != registry_digest
                or not row["image_digest"] or serve_recipes._recipe_time(row.get("started_at")) is None
                or row.get("started_at") == serve_recipes._RECIPE_ZERO_TIME
                or not row.get("gpu_selection") or "all" in row["gpu_selection"]):
            raise ValueError("monitoring recipe identity, digest, port or incarnation mismatch")
        return copy.deepcopy({key: row[key] for key in ("container", "container_id", "image_digest",
                "recipe_digest", "registry_digest", "served_identity", "bound_port",
                "gpu_selection", "started_at", "bound_endpoints")})

    endpoint = serve_recipes.recipe_memory._docker_endpoint(bounded_run)
    if not isinstance(endpoint, str) or not endpoint.startswith("unix://"):
        raise ValueError("monitoring requires a local Linux Docker endpoint")
    identity = discover()
    from .recipe_secrets import read_key
    key_file = config.get('model_api_key_files', {}).get(str(port))
    key, key_identity = read_key(key_file) if key_file else (None, None)
    def fetch(path):
        from .router.model_capacity import _default_metrics_read
        headers = {"Authorization": "Bearer " + key} if key and path == "/v1/models" else {}
        try:
            status, raw = _default_metrics_read("http://127.0.0.1:%s%s" % (port, path),
                headers, timeout=min(3, remaining()), max_bytes=MAX_BYTES)
        except (OSError, http.client.HTTPException) as exc:
            raise ValueError("monitoring probe failed") from exc
        if status != 200 or len(raw) > MAX_BYTES:
            raise ValueError("monitoring probe failed or exceeds byte bound")
        return raw
    fetcher = _fetch or fetch
    fetcher("/ready" if contract == "ignis" else "/health")
    models = json.loads(fetcher("/v1/models"))
    if (not isinstance(models, dict) or not isinstance(models.get("data"), list)
            or len(models["data"]) != 1 or not isinstance(models["data"][0], dict)
            or models["data"][0].get("id") != identity["served_identity"]):
        raise ValueError("monitoring live model identity differs from recipe container")
    if discover() != identity:
        raise ValueError("monitoring container changed during verification")
    with serve_recipes.registry_lock(receipt_path()):
        remaining()
        if read_json(receipt_path()) != before or configuration() != config:
            raise ValueError("monitoring registration changed during verification")
        if key_file and read_key(key_file)[1] != key_identity:
            raise ValueError('monitoring credential changed during verification')
        value = {"schema": SCHEMA, "state": "active", "generation": secrets.token_hex(32),
                 "updated": time.time(), "engine": engine, "metrics_contract": contract,
                 "model": identity.pop("served_identity"), **identity}
        _write(value)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model")
    parser.add_argument("--container", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    from . import serve_recipes
    try:
        config = configuration()
        recipe = serve_recipes.find_recipe(serve_recipes.load_registry(args.registry), args.model)
        if config is None or recipe is None or recipe["serve"].get("port") not in config["ports"]:
            raise ValueError("configured monitoring slot and matching recipe required")
        if args.dry_run or not args.confirm:
            print(json.dumps({"mode": "preview", "container": args.container,
                              "receipt": str(receipt_path()), "changes_serving": False}))
            return 0
        withdraw(port=recipe["serve"]["port"], container=args.container, pending=True)
        print(json.dumps(publish(recipe, args.container,
                                registry_digest=serve_recipes.registry_digest(args.registry)), sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        print(json.dumps({"ok": False, "error": "monitoring registration failed; inspect configuration and managed identity"}))
        return 1
