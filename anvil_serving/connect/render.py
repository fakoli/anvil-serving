"""Deterministic, secret-free render planning for managed Anvil Connect services."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from .config import ManifestError, canonical_manifest, require_isolated, role_identity, role_limits, validate_manifest

_RENDER_SCHEMA = "anvil-connect.render/v1"
_OWNERSHIP = "anvil-connect.ownership/v1"
# Caddy defaults to an eternal HTTP shutdown grace period. Connect carries
# long-lived WebSocket tunnels, so make that drain finite before systemd's
# hard stop deadline while still giving ordinary in-flight requests time to
# complete.
_CADDY_GRACE_PERIOD_SECONDS = 15
_CADDY_GRACE_PERIOD = f"{_CADDY_GRACE_PERIOD_SECONDS}s"
_SERVICE_STOP_TIMEOUT_SECONDS = 20
_DROP_IDENTITY_HEADERS = [
    "Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Real-IP",
    "X-Anvil-Connect-User", "X-Anvil-Connect-Groups", "X-Anvil-Connect-Identity", "X-Auth-Request-User",
    "X-Auth-Request-Email", "X-Authenticated-User", "X-Authenticated-Groups",
    "Remote-User", "Remote-Groups", "Remote-Email", "Remote-Name", "Cf-Access-Jwt-Assertion",
    "X-Goog-Authenticated-User", "X-Goog-Authenticated-User-Email", "X-Amzn-Oidc-Data",
    "X-Amzn-Oidc-Identity", "X-Amzn-Oidc-Accesstoken", "Tailscale-User-Login",
]


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def _unit_argument(value: str) -> str:
    """Conservative systemd argv grammar; shell quoting is not ExecStart quoting."""
    if not value or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./:-" for char in value):
        raise ManifestError("systemd ExecStart argument contains unsupported characters")
    return value


def _unit(description: str, command: list[str], user: str, *, environment_file: str | None = None,
          identity_environment_file: str | None = None, bind_public_tls: bool = False) -> str:
    escaped = " ".join(_unit_argument(arg) for arg in command)
    environment = [] if environment_file is None else ["EnvironmentFile=" + _unit_argument(environment_file)]
    if identity_environment_file is not None:
        environment.append("EnvironmentFile=" + _unit_argument(identity_environment_file))
    return "\n".join((
        "[Unit]", f"Description={description}", "After=network-online.target", "Wants=network-online.target", "",
        "[Service]", "Type=simple", f"User={_unit_argument(user)}", f"Group={_unit_argument(user)}", *environment, f"ExecStart={escaped}",
        *(["AmbientCapabilities=CAP_NET_BIND_SERVICE", "CapabilityBoundingSet=CAP_NET_BIND_SERVICE"] if bind_public_tls else []),
        "Restart=on-failure", "RestartSec=5", f"TimeoutStopSec={_SERVICE_STOP_TIMEOUT_SECONDS}", "KillSignal=SIGTERM", "",
        "[Install]", "WantedBy=multi-user.target", "",
    ))


def _headers() -> dict[str, Any]:
    return {"handler": "headers", "request": {"delete": _DROP_IDENTITY_HEADERS}}


def _proxy(unix_socket: str, versions: list[str]) -> dict[str, Any]:
    return {
        "handler": "reverse_proxy",
        "upstreams": [{"dial": "unix/" + unix_socket}],
        "transport": {"protocol": "http", "versions": versions},
    }


def _route(match: dict[str, Any], proxy_versions: list[str], socket: str) -> dict[str, Any]:
    return {"match": [match], "handle": [_headers(), _proxy(socket, proxy_versions)]}


def _upgrade_match() -> dict[str, Any]:
    # Caddy's plain header values are case-sensitive; HTTP upgrade tokens are
    # not. This only selects HTTP/1 transport. Go still validates the handshake.
    return {"header_regexp": {
        "Connection": {"pattern": r"(?i)(^|,)[\t ]*upgrade[\t ]*(,|$)"},
        "Upgrade": {"pattern": r"(?i)^websocket$"},
    }}


def _caddy(manifest: dict[str, Any]) -> dict[str, Any]:
    gateway = manifest["gateway"]
    authelia = manifest["authelia"]
    ingress = gateway.get("ingress")
    socket = (ingress["directory"] if ingress is not None else gateway["state_directory"]) + "/ingress.sock"
    hosts = [authelia["host"], gateway["control_host"], gateway["tunnel_host"]]
    hosts.extend(resource["rule"]["host"] for resource in gateway["gateway"]["resources"])
    routes: list[dict[str, Any]] = []
    routes.append({
        "match": [{"host": [authelia["host"]]}],
        "handle": [_headers(), {"handler": "reverse_proxy", "upstreams": [{"dial": authelia["listen"]}], "transport": {"protocol": "http", "versions": ["1.1"]}}],
    })
    # The tunnel endpoint is deliberately narrower than the ordinary ingress.
    routes.append(_route({"host": [gateway["tunnel_host"]], "method": ["GET"], "path": ["/acv1/events"], **_upgrade_match()}, ["1.1"], socket))
    for resource in gateway["gateway"]["resources"]:
        rule = resource["rule"]
        host, prefix, methods = rule["host"], rule["path_prefix"], rule["methods"]
        if rule["access"] == "browser":
            # Auth endpoints belong to the browser host even if application policy
            # starts below /; Go owns their session and origin checks.
            routes.append(_route({"host": [host], "path": ["/_anvil-connect/login", "/_anvil-connect/callback", "/_anvil-connect/logout"]}, ["h2c"], socket))
        match_paths = ["/", "/*"] if prefix == "/" else [prefix, prefix + "/*"]
        routes.append(_route({"host": [host], "path": match_paths, **_upgrade_match()}, ["1.1"], socket))
        routes.append(_route({"host": [host], "path": match_paths, "method": methods}, ["h2c"], socket))
    routes.append(_route({"host": [gateway["control_host"]]}, ["h2c"], socket))
    routes.append({"handle": [{"handler": "static_response", "status_code": 404}]})
    tls: dict[str, Any] = {}
    if manifest["caddy"]["tls"]["mode"] == "provided":
        tls["certificates"] = {"load_files": [{"certificate": manifest["caddy"]["tls"]["certificate_file"], "key": manifest["caddy"]["tls"]["key_file"]}]}
    else:
        tls["automation"] = {"policies": [{"subjects": sorted(hosts), "issuers": [{"module": "acme"}]}]}
    edge_listen = manifest["caddy"].get("listen", ":443")
    server: dict[str, Any] = {"listen": [edge_listen], "routes": routes}
    if manifest["caddy"]["tls"]["mode"] == "provided":
        # A supplied certificate need not cover control/tunnel/API hosts. Do
        # not let their route matchers trigger an unrelated ACME transaction.
        server["automatic_https"] = {"disable_certificates": True}
        if edge_listen != ":443":
            # Tunnel origins use only this explicit HTTPS listener: no implicit
            # port-80 redirect listener, and TLS even on a nonstandard port.
            server["automatic_https"]["disable_redirects"] = True
            server["tls_connection_policies"] = [{}]
    return {"admin": {"disabled": True}, "apps": {"http": {"grace_period": _CADDY_GRACE_PERIOD, "servers": {"anvil_connect": server}}, "tls": tls}}


def _quote(value: str) -> str:
    # YAML single-quoted scalars are safe for host names and paths.
    return "'" + value.replace("'", "''") + "'"


def _template_quote(value: str) -> str:
    # Authelia uses Go templates: arguments are Go string literals, not YAML.
    return json.dumps(value, ensure_ascii=True)


def _authelia(manifest: dict[str, Any]) -> str:
    gateway, auth = manifest["gateway"], manifest["authelia"]
    callbacks = sorted({f"https://{item['rule']['host']}/_anvil-connect/callback" for item in gateway["gateway"]["resources"] if item["rule"]["access"] == "browser"})
    if not callbacks:
        raise ManifestError("$.gateway.gateway.resources: at least one browser resource is required for managed OIDC")
    # Current Authelia template filters document fileContent+nindent. Block
    # scalars with |- avoid carrying a final secret-file newline into values.
    lines = [
        "# Generated by Anvil Connect. References point to operator-provisioned files.",
        "server:", f"  address: {_quote('tcp://' + auth['listen'])}",
        "authentication_backend:", "  file:", f"    path: {_quote(auth['users_file'])}",
        "access_control:", "  default_policy: two_factor",
        "identity_validation:", "  reset_password:", "    jwt_secret: |-", f"      {{{{- fileContent {_template_quote(auth['identity_validation_secret_file'])} | nindent 6 }}}}",
        "notifier:", "  filesystem:", f"    filename: {_quote(auth['state_directory'] + '/notifications.txt')}",
        "session:", "  secret: |-", f"    {{{{- fileContent {_template_quote(auth['session_secret_file'])} | nindent 4 }}}}",
        "  cookies:", f"    - domain: {_quote(auth['host'])}", f"      authelia_url: {_quote('https://' + auth['host'])}",
        "storage:", "  encryption_key: |-", f"    {{{{- fileContent {_template_quote(auth['storage_encryption_key_file'])} | nindent 4 }}}}", "  local:", f"    path: {_quote(auth['state_directory'] + '/authelia.sqlite3')}",
        "identity_providers:", "  oidc:", "    hmac_secret: |-", f"      {{{{- fileContent {_template_quote(auth['oidc_hmac_secret_file'])} | nindent 6 }}}}", "    jwks:", "      - key_id: 'anvil-connect-rs256'", "        algorithm: RS256", "        use: sig", "        key: |-",
        f"          {{{{- fileContent {_template_quote(auth['oidc_rsa_private_key_file'])} | nindent 10 }}}}",
        "    clients:", f"      - client_id: {_quote(gateway['oidc']['client_id'])}", "        client_secret: |-", f"          {{{{- fileContent {_template_quote(auth['client_secret_file'])} | nindent 10 }}}}", "        public: false", "        require_pkce: true", "        pkce_challenge_method: S256", "        response_types:", "          - code", "        grant_types:", "          - authorization_code", "        scopes:", "          - openid", "        id_token_signed_response_alg: RS256", "        token_endpoint_auth_method: client_secret_basic", "        redirect_uris:",
    ]
    lines.extend(f"          - {_quote(callback)}" for callback in callbacks)
    if "webauthn" in auth:
        webauthn = auth["webauthn"]
        lines.extend([
            "default_2fa_method: webauthn", "webauthn:", "  disable: false",
            "  display_name: 'Anvil Connect'",
            f"  enable_passkey_login: {str(webauthn['enable_passkey_login']).lower()}",
            "  # Explicit opt-in to the pinned Authelia experimental UV policy.",
            f"  experimental_enable_passkey_uv_two_factors: {str(webauthn['experimental_enable_passkey_uv_two_factors']).lower()}",
            "  selection_criteria:",
            f"    discoverability: {_quote(webauthn['discoverability'])}",
            f"    user_verification: {_quote(webauthn['user_verification'])}",
        ])
    return "\n".join(lines) + "\n"


def _isolated_unit(description: str, command: list[str], identity: tuple[int, int], limits: tuple[int, int], writable_paths: list[str], *,
                   environment_file: str | None = None, identity_environment_file: str | None = None,
                   supplementary_group: int | None = None, bind_public_tls: bool = False,
                   environment: dict[str, str] | None = None) -> str:
    escaped = " ".join(_unit_argument(arg) for arg in command)
    lines = [
        "[Unit]", f"Description={description}", "After=network-online.target", "Wants=network-online.target", "",
        "[Service]", "Type=simple", f"User={identity[0]}", f"Group={identity[1]}", "UMask=0077",
        "NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=true", "PrivateTmp=true",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "MemoryAccounting=true", "TasksAccounting=true",
        f"MemoryMax={limits[0]}", f"TasksMax={limits[1]}",
        "ReadWritePaths=" + " ".join(_unit_argument(path) for path in sorted(set(writable_paths))),
    ]
    if supplementary_group is not None:
        lines.append(f"SupplementaryGroups={supplementary_group}")
    if environment_file is not None:
        lines.append("EnvironmentFile=" + _unit_argument(environment_file))
    if identity_environment_file is not None:
        lines.append("EnvironmentFile=" + _unit_argument(identity_environment_file))
    for name, value in sorted((environment or {}).items()):
        lines.append("Environment=" + _unit_argument(name) + "=" + _unit_argument(value))
    if bind_public_tls:
        lines.extend(["AmbientCapabilities=CAP_NET_BIND_SERVICE", "CapabilityBoundingSet=CAP_NET_BIND_SERVICE"])
    else:
        lines.extend(["AmbientCapabilities=", "CapabilityBoundingSet="])
    lines.extend([f"ExecStart={escaped}", "Restart=on-failure", "RestartSec=5", f"TimeoutStopSec={_SERVICE_STOP_TIMEOUT_SECONDS}", "KillSignal=SIGTERM", "", "[Install]", "WantedBy=multi-user.target", ""])
    return "\n".join(lines)


def _render(data: dict[str, Any]) -> dict[str, Any]:
    generation = hashlib.sha256(canonical_manifest(data)).hexdigest()
    gateway = data["gateway"]
    if "service_identities" not in data:
        files: dict[str, str] = {
            "gateway.json": _json(gateway),
            "caddy.json": _json(_caddy(data)),
            "authelia/configuration.yml": _authelia(data),
            "systemd/anvil-connect-gateway.service": _unit("Anvil Connect gateway", [data["binary"], "gateway", "--config", data["config_root"] + "/gateway.json"], data["service_user"], environment_file=data["environment_files"]["gateway"], identity_environment_file=data["environment_files"].get("gateway_identity")),
            "systemd/anvil-connect-caddy.service": _unit("Anvil Connect Caddy", [data["components"]["caddy"], "run", "--config", data["config_root"] + "/caddy.json"], data["service_user"], bind_public_tls=int(data["caddy"].get("listen", ":443").rsplit(":", 1)[1]) < 1024),
            "systemd/anvil-connect-authelia.service": _unit("Anvil Connect Authelia", [data["components"]["authelia"], "--config", data["config_root"] + "/authelia/configuration.yml", "--config.experimental.filters", "template"], data["service_user"]),
        }
    else:
        ingress = gateway["ingress"]["directory"]
        caddy_state = data["caddy"]["state_directory"]
        files = {
            "gateway.json": _json(gateway),
            "caddy.json": _json(_caddy(data)),
            "authelia/configuration.yml": _authelia(data),
            "systemd/anvil-connect-gateway.service": _isolated_unit("Anvil Connect gateway", [data["binary"], "gateway", "--config", data["config_root"] + "/gateway.json"], role_identity(data, "gateway"), role_limits(data, "gateway"), [gateway["state_directory"], ingress], environment_file=data["environment_files"]["gateway"], identity_environment_file=data["environment_files"].get("gateway_identity")),
            "systemd/anvil-connect-caddy.service": _isolated_unit("Anvil Connect Caddy", [data["components"]["caddy"], "run", "--config", data["config_root"] + "/caddy.json"], role_identity(data, "edge"), role_limits(data, "edge"), [caddy_state], supplementary_group=data["service_identities"]["ingress"]["group_id"], bind_public_tls=int(data["caddy"].get("listen", ":443").rsplit(":", 1)[1]) < 1024, environment={"XDG_CONFIG_HOME": caddy_state + "/config", "XDG_DATA_HOME": caddy_state + "/data"}),
            "systemd/anvil-connect-authelia.service": _isolated_unit("Anvil Connect Authelia", [data["components"]["authelia"], "--config", data["config_root"] + "/authelia/configuration.yml", "--config.experimental.filters", "template"], role_identity(data, "idp"), role_limits(data, "idp"), [data["authelia"]["state_directory"]]),
        }
    for connector in data["connectors"]:
        name = connector["id"]
        files[f"connectors/{name}.json"] = _json(connector)
        if "service_identities" not in data:
            files[f"systemd/anvil-connect-connector-{name}.service"] = _unit("Anvil Connect connector " + name, [data["binary"], "connector", "--config", data["config_root"] + "/connectors/" + name + ".json"], data["service_user"], environment_file=data["environment_files"]["connectors"][name])
        else:
            files[f"systemd/anvil-connect-connector-{name}.service"] = _isolated_unit("Anvil Connect connector " + name, [data["binary"], "connector", "--config", data["config_root"] + "/connectors/" + name + ".json"], role_identity(data, "connector", name), role_limits(data, "connector", name), [connector["state_directory"]], environment_file=data["environment_files"]["connectors"][name])
            if "local_tunnel" in connector:
                # Order the declared colocated gateway without propagating its
                # stop/restart to connectors using a healthy sibling entry.
                unit = f"systemd/anvil-connect-connector-{name}.service"
                preflight = [data["binary"], "preflight", "--mode", "connector", "--config",
                             data["config_root"] + "/connectors/" + name + ".json", "--input",
                             data["config_root"] + "/gateway.json", "--socket", connector["local_tunnel"]["address"]]
                files[unit] = files[unit].replace("ExecStart=", "ExecStartPre=" + " ".join(_unit_argument(arg) for arg in preflight) + "\nExecStart=")
                files[unit] = files[unit].replace(
                    "After=network-online.target\nWants=network-online.target",
                    "After=network-online.target anvil-connect-gateway.service\n"
                    "Wants=network-online.target anvil-connect-gateway.service",
                )
    for client in data["clients"]:
        name = client["rule"]["id"]
        files[f"clients/{name}.json"] = _json(client)
        if "service_identities" not in data:
            files[f"systemd/anvil-connect-client-{name}.service"] = _unit("Anvil Connect client " + name, [data["binary"], "client", "--config", data["config_root"] + "/clients/" + name + ".json"], data["service_user"], environment_file=data["environment_files"]["clients"][name])
        else:
            files[f"systemd/anvil-connect-client-{name}.service"] = _isolated_unit("Anvil Connect client " + name, [data["binary"], "client", "--config", data["config_root"] + "/clients/" + name + ".json"], role_identity(data, "client", name), role_limits(data, "client", name), [], environment_file=data["environment_files"]["clients"][name])
    files = dict(sorted(files.items()))
    ownership = {"schema": _OWNERSHIP, "generation": generation, "files": {name: hashlib.sha256(content.encode("utf-8")).hexdigest() for name, content in files.items()}}
    files["managed.json"] = _json(ownership)
    return {"schema": _RENDER_SCHEMA, "generation": generation, "files": files, "ownership": ownership}


def render_for_inspection(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic read-only plan, including a legacy generation."""
    return _render(validate_manifest(manifest))


def render(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return an isolated generation suitable for managed publication only."""
    data = validate_manifest(manifest)
    require_isolated(data)
    return _render(data)


_MAX_MARKER_BYTES = 64 * 1024
_MAX_RENDERED_FILE_BYTES = 2 * 1024 * 1024
_MAX_DIRECTORY_ENTRIES = 256
_MAX_OWNED_FILES = 128
_SHA256 = re.compile(r"[0-9a-f]{64}$")
_SAFE_RENDERED_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*$")


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _regular(path: Path, *, maximum: int) -> bytes:
    """Read a bounded regular file through a no-follow descriptor."""
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ManifestError(f"{path}: expected a regular non-symlink file") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ManifestError(f"{path}: exceeds bounded renderer read")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > maximum:
            raise ManifestError(f"{path}: exceeds bounded renderer read")
        return data
    finally:
        os.close(descriptor)


def _safe_root(root: Path) -> None:
    status = _lstat(root)
    if status is not None and (root.is_symlink() or not root.is_dir()):
        raise ManifestError(f"{root}: destination must be a non-symlink directory")


def _safe_rendered_name(name: Any) -> bool:
    return isinstance(name, str) and bool(_SAFE_RENDERED_NAME.fullmatch(name)) and not name.startswith(".") and "/." not in name and "//" not in name


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate marker key")
        result[key] = value
    return result


def _owned_actual(root: Path, owned: set[str]) -> tuple[dict[str, str], list[str]]:
    """Read declared files and shallow owned directories; never recurse foreign trees."""
    actual: dict[str, str] = {}
    expected_by_parent: dict[str, set[str]] = {"": {"managed.json"}}
    for name in owned:
        parent, _, leaf = name.rpartition("/")
        expected_by_parent.setdefault(parent, set()).add(leaf)
        while parent:
            grandparent, _, child = parent.rpartition("/")
            expected_by_parent.setdefault(grandparent, set()).add(child)
            parent = grandparent
    unmanaged: list[str] = []
    for parent, expected in expected_by_parent.items():
        directory = root / parent
        status = _lstat(directory)
        if status is None:
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise ManifestError(f"{directory}: owned directory is not a non-symlink directory")
        count = 0
        for entry in directory.iterdir():
            count += 1
            if count > _MAX_DIRECTORY_ENTRIES:
                raise ManifestError(f"{directory}: exceeds bounded renderer directory entries")
            relative = str(entry.relative_to(root))
            if entry.name not in expected:
                unmanaged.append(relative)
                continue
            if entry.is_symlink():
                raise ManifestError(f"{entry}: symlinks are not accepted in managed output")
    for name in owned:
        target = root / name
        if _lstat(target) is not None:
            actual[name] = hashlib.sha256(_regular(target, maximum=_MAX_RENDERED_FILE_BYTES)).hexdigest()
    return actual, sorted(unmanaged)


def _read_marker(marker: Path) -> dict[str, Any] | None:
    if _lstat(marker) is None:
        return None
    try:
        value = json.loads(_regular(marker, maximum=_MAX_MARKER_BYTES).decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ManifestError(f"{marker}: invalid ownership marker") from exc
    if not isinstance(value, dict) or set(value) != {"schema", "generation", "files"} or value["schema"] != _OWNERSHIP:
        return None
    generation, files = value["generation"], value["files"]
    if not isinstance(generation, str) or not _SHA256.fullmatch(generation) or not isinstance(files, dict) or not 0 < len(files) <= _MAX_OWNED_FILES:
        return None
    if any(name == "managed.json" or not _safe_rendered_name(name) or not isinstance(digest, str) or not _SHA256.fullmatch(digest) for name, digest in files.items()):
        return None
    return value


def _plan(manifest: dict[str, Any], destination: str | Path, renderer: Any) -> dict[str, Any]:
    """Read-only drift report; it never adopts or overwrites existing files."""
    result = renderer(manifest)
    root = Path(destination)
    _safe_root(root)
    if _lstat(root) is None:
        return {"schema": "anvil-connect.render-plan/v1", "generation": result["generation"], "state": "absent", "destination": str(root), "changes": sorted(result["files"])}
    marker = _read_marker(root / "managed.json")
    if marker is None:
        return {"schema": "anvil-connect.render-plan/v1", "generation": result["generation"], "state": "unmanaged", "destination": str(root), "changes": []}
    desired = {name: hashlib.sha256(content.encode("utf-8")).hexdigest() for name, content in result["files"].items() if name != "managed.json"}
    actual, unmanaged = _owned_actual(root, set(marker["files"]))
    corrupt = sorted(name for name, digest in marker["files"].items() if actual.get(name) != digest)
    marker_digest = hashlib.sha256(result["files"]["managed.json"].encode("utf-8")).hexdigest()
    current_marker_digest = hashlib.sha256(_regular(root / "managed.json", maximum=_MAX_MARKER_BYTES)).hexdigest()
    changes = sorted(name for name in set(desired) | set(actual) if desired.get(name) != actual.get(name))
    if marker_digest != current_marker_digest:
        changes.append("managed.json")
    changes = sorted(set(changes))
    state = "drift" if unmanaged or corrupt else ("current" if not changes else "update")
    return {"schema": "anvil-connect.render-plan/v1", "generation": result["generation"], "state": state, "destination": str(root), "changes": changes, "unmanaged": unmanaged, "corrupt": corrupt}


def plan_for_inspection(manifest: dict[str, Any], destination: str | Path) -> dict[str, Any]:
    """Read a legacy or isolated generation for permitted status/down inspection."""
    return _plan(manifest, destination, render_for_inspection)


def plan(manifest: dict[str, Any], destination: str | Path) -> dict[str, Any]:
    """Read an isolated generation plan suitable for managed publication."""
    return _plan(manifest, destination, render)


def _stage_root(root: Path, generation: str) -> Path:
    parent = root.parent
    _safe_root(parent)
    if _lstat(parent) is None:
        raise ManifestError(f"{parent}: staging parent does not exist")
    base = parent / ("." + root.name + ".anvil-connect-staging")
    status = _lstat(base)
    if status is not None and (base.is_symlink() or not base.is_dir()):
        raise ManifestError(f"{base}: staging root must be a non-symlink directory")
    return base / generation


def stage(manifest: dict[str, Any], destination: str | Path) -> dict[str, Any]:
    """Write a sibling staging generation; the deployment root remains untouched."""
    result = render(manifest)
    root = Path(destination)
    check = plan(manifest, root)
    if check["state"] in {"unmanaged", "drift"}:
        raise ManifestError(f"{root}: refusing to stage over {check['state']} output")
    stage_root = _stage_root(root, result["generation"])
    if _lstat(stage_root) is not None:
        if stage_root.is_symlink() or not stage_root.is_dir():
            raise ManifestError(f"{stage_root}: staging generation is not a non-symlink directory")
        # Idempotently reuse only a complete owned staging generation.
        marker = _read_marker(stage_root / "managed.json")
        if marker == result["ownership"]:
            actual, unmanaged = _owned_actual(stage_root, set(marker["files"]))
            if not unmanaged and all(actual.get(name) == digest for name, digest in marker["files"].items()):
                return {"schema": "anvil-connect.render-stage/v1", "generation": result["generation"], "state": "staged", "path": str(stage_root), "files": sorted(result["files"])}
        raise ManifestError(f"{stage_root}: existing staging generation is not owned and complete")
    stage_root.mkdir(parents=True, exist_ok=False)
    for name, content in result["files"].items():
        target = stage_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        os.chmod(target, 0o640)
    return {"schema": "anvil-connect.render-stage/v1", "generation": result["generation"], "state": "staged", "path": str(stage_root), "files": sorted(result["files"])}
