"""Fail-closed router capability reconciliation for Mini-side LLM clients."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_OPENCLAW_CONFIG = "~/.openclaw/openclaw.json"
DEFAULT_HERMES_CONFIG = "~/.hermes/config.yaml"
DEFAULT_PI_MODELS = "~/.pi/agent/models.json"
DEFAULT_PI_SETTINGS = "~/.pi/agent/settings.json"
DEFAULT_STATE = "~/.anvil-serving/state/client-catalog.json"
DEFAULT_BACKUP_ROOT = "~/.anvil-serving/backups/client-catalog"
DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
PI_ALIASES = ("llm.primary", "llm.secondary", "vision.general", "vision.ocr")
OPENCLAW_EXCLUDED_ALIASES = frozenset({"llm.auxiliary"})
COMPACTION_EXCLUDED_ALIASES = frozenset({"llm.voice"})
CLIENT_TARGETS = ("openclaw", "hermes", "pi")
PI_ANVIL_COMPAT = {
    "maxTokensField": "max_tokens",
    "supportsDeveloperRole": False,
    "supportsReasoningEffort": True,
    "supportsStore": False,
    "supportsUsageInStreaming": True,
    "thinkingFormat": "openai",
}


class ClientCatalogError(ValueError):
    """A remote catalog or local client invariant failed before mutation."""


def _normalize_clients(value: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ClientCatalogError("clients must be a comma-separated string")
    requested = [item.strip() for item in value.split(",") if item.strip()]
    invalid = sorted(set(requested) - set(CLIENT_TARGETS))
    if not requested or invalid:
        raise ClientCatalogError(
            "clients must select one or more of: openclaw, hermes, pi"
        )
    return tuple(target for target in CLIENT_TARGETS if target in requested)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _safe_base_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ClientCatalogError("base_url must be a non-empty http(s) URL ending in /v1")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ClientCatalogError("base_url must be a non-empty http(s) URL ending in /v1")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ClientCatalogError("base_url must not contain credentials, a query, or a fragment")
    if parsed.path.rstrip("/") != "/v1":
        raise ClientCatalogError("base_url must end in exactly /v1")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "localhost":
        raise ClientCatalogError("base_url must use 127.0.0.1 instead of localhost")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Credential-bearing discovery permits only a TLS-authenticated
        # tailnet name when the address class cannot be proven from the URL.
        # This prevents a typo or hostile public hostname from receiving the
        # router bearer token.
        if parsed.scheme != "https" or not host.endswith(".ts.net"):
            raise ClientCatalogError(
                "hostname base_url must be an HTTPS tailnet .ts.net name"
            ) from None
    else:
        tailnet = address.version == 4 and address in ipaddress.ip_network("100.64.0.0/10")
        if (
            not (address.is_loopback or address.is_private or tailnet)
            or address.is_unspecified
            or address.is_multicast
        ):
            raise ClientCatalogError(
                "base_url host must be loopback, private, or tailnet-safe"
            )
    return value.rstrip("/")


def _bounded_json_response(response, *, max_bytes: int) -> dict:  # noqa: ANN001
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise ClientCatalogError("router metadata response exceeds the size limit")
        except ValueError:
            pass
    raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ClientCatalogError("router metadata response exceeds the size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientCatalogError("router metadata response is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ClientCatalogError("router metadata response must be a JSON object")
    return payload


def _fetch_json(
    base_url: str,
    endpoint: str,
    *,
    token: str,
    timeout_seconds: int,
    max_bytes: int,
    opener=None,
) -> dict:
    opener = opener or urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect()
    )
    request = urllib.request.Request(
        base_url + endpoint,
        headers={"Accept": "application/json", "Authorization": "Bearer " + token},
        method="GET",
    )
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            return _bounded_json_response(response, max_bytes=max_bytes)
    except urllib.error.HTTPError as exc:
        raise ClientCatalogError(
            "router metadata request failed with HTTP %s" % exc.code
        ) from exc
    except urllib.error.URLError as exc:
        raise ClientCatalogError("router metadata request failed: %s" % exc.reason) from exc


def fetch_client_catalog(
    *,
    base_url: str,
    api_key_env: str = "ANVIL_ROUTER_TOKEN",
    timeout_seconds: int = 15,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    environ: Mapping[str, str] | None = None,
    opener=None,
) -> dict:
    """Fetch and cross-check the authenticated router status/capability contract."""
    from .harness import _validate_env

    base_url = _safe_base_url(base_url)
    _validate_env(api_key_env, "api_key_env")
    environ = os.environ if environ is None else environ
    token = environ.get(api_key_env, "")
    if not token:
        raise ClientCatalogError("required router credential environment variable is unset")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds < 1:
        raise ClientCatalogError("timeout_seconds must be a positive integer")
    status = _fetch_json(
        base_url,
        "/router/status",
        token=token,
        timeout_seconds=timeout_seconds,
        max_bytes=max_response_bytes,
        opener=opener,
    )
    capabilities = _fetch_json(
        base_url,
        "/models/capabilities",
        token=token,
        timeout_seconds=timeout_seconds,
        max_bytes=max_response_bytes,
        opener=opener,
    )
    config_sha256 = status.get("config_sha256")
    aliases = status.get("model_aliases")
    if (
        not isinstance(config_sha256, str)
        or len(config_sha256) != 64
        or any(character not in "0123456789abcdef" for character in config_sha256)
    ):
        raise ClientCatalogError("router status has no valid config_sha256")
    if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
        raise ClientCatalogError("router status has no valid model_aliases list")
    if len(set(aliases)) != len(aliases):
        raise ClientCatalogError("router status contains duplicate model aliases")
    rows = capabilities.get("data")
    if capabilities.get("object") != "list" or not isinstance(rows, list):
        raise ClientCatalogError("router capability response has an invalid list shape")

    models: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("aliases"), list):
            raise ClientCatalogError("router capability row has an invalid shape")
        context = row.get("context_limit_tokens")
        limits = row.get("limits")
        output = limits.get("max_output_tokens") if isinstance(limits, dict) else None
        if (
            not isinstance(context, int)
            or isinstance(context, bool)
            or context <= 0
            or not isinstance(output, int)
            or isinstance(output, bool)
            or output <= 0
            or output > context
        ):
            raise ClientCatalogError(
                "every routed tier must declare valid context_limit_tokens and max_output_tokens"
            )
        modalities = row.get("modalities")
        if not isinstance(modalities, list) or any(not isinstance(item, str) for item in modalities):
            raise ClientCatalogError("router capability row has invalid modalities")
        inputs = ["text"]
        if "image" in modalities or "video" in modalities:
            inputs.append("image")
        thinking = row.get("thinking")
        reasoning = bool(isinstance(thinking, dict) and thinking.get("supported") is True)
        compat = row.get("compat") if isinstance(row.get("compat"), dict) else {}
        for alias in row["aliases"]:
            if not isinstance(alias, str) or not alias:
                raise ClientCatalogError("router capability row contains an invalid alias")
            if alias in models:
                raise ClientCatalogError("router capability rows contain a duplicate alias")
            models[alias] = {
                "id": alias,
                "context_window": context,
                "max_output_tokens": output,
                "input": inputs,
                "reasoning": reasoning,
                "compat": compat,
            }
    if set(models) != set(aliases):
        raise ClientCatalogError(
            "router status and capability alias sets do not match exactly"
        )
    return {
        "config_sha256": config_sha256,
        "package_version": status.get("package_version"),
        "models": {alias: models[alias] for alias in sorted(models)},
    }


def _managed_model(existing: Mapping | None, model: Mapping, *, name_prefix: str) -> dict:
    result = dict(existing) if isinstance(existing, Mapping) else {}
    result.update({
        "id": model["id"],
        "name": result.get("name") or "%s %s" % (name_prefix, model["id"]),
        "reasoning": model["reasoning"],
        "input": list(model["input"]),
        "contextWindow": model["context_window"],
        "maxTokens": model["max_output_tokens"],
    })
    return result


def _models_by_id(value) -> dict[str, Mapping]:  # noqa: ANN001
    if not isinstance(value, list):
        return {}
    return {
        item["id"]: item
        for item in value
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }


def _validate_compaction(
    *,
    label: str,
    compaction: Mapping,
    models: list[Mapping],
    reserve_key: str,
    recent_key: str,
) -> None:
    if label == "OpenClaw" and compaction.get("mode") != "safeguard":
        raise ClientCatalogError("OpenClaw compaction mode must remain safeguard")
    if label == "Pi" and compaction.get("enabled") is not True:
        raise ClientCatalogError("Pi compaction must remain enabled")
    reserve_declared = reserve_key in compaction
    reserve = compaction.get(reserve_key)
    recent = compaction.get(recent_key)
    thresholds = (reserve, recent) if reserve_declared else (recent,)
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in thresholds
    ):
        raise ClientCatalogError("%s compaction token thresholds must be non-negative integers" % label)
    if label == "Pi" and not reserve_declared:
        raise ClientCatalogError("Pi compaction reserveTokens must remain configured")
    if label == "OpenClaw":
        floor = compaction.get("reserveTokensFloor")
        if floor is not None and (
            not isinstance(floor, int)
            or isinstance(floor, bool)
            or floor < 0
            or (reserve_declared and floor < reserve)
        ):
            raise ClientCatalogError(
                "OpenClaw reserveTokensFloor must be a non-negative integer no lower than reserveTokens"
            )
    max_output = max(model["max_output_tokens"] for model in models)
    min_context = min(model["context_window"] for model in models)
    if reserve_declared and reserve < max_output:
        raise ClientCatalogError(
            "%s compaction reserve must be at least the largest selected max output" % label
        )
    effective_reserve = reserve if reserve_declared else max_output
    if effective_reserve + recent >= min_context:
        raise ClientCatalogError(
            "%s compaction reserve plus recent tokens must fit the smallest selected context" % label
        )


def _render_openclaw_document(catalog: Mapping, openclaw: Mapping) -> dict:
    models = catalog.get("models")
    if not isinstance(models, Mapping):
        raise ClientCatalogError("catalog models are invalid")
    openclaw_aliases = [
        alias for alias in models if alias not in OPENCLAW_EXCLUDED_ALIASES
    ]
    rendered_openclaw = json.loads(json.dumps(openclaw))
    provider = (
        rendered_openclaw.setdefault("models", {})
        .setdefault("providers", {})
        .get("anvil")
    )
    if not isinstance(provider, dict):
        raise ClientCatalogError("OpenClaw Anvil provider is missing")
    old_models = _models_by_id(provider.get("models"))
    provider["models"] = [
        _managed_model(old_models.get(alias), models[alias], name_prefix="Anvil")
        for alias in openclaw_aliases
    ]
    defaults = rendered_openclaw.setdefault("agents", {}).setdefault("defaults", {})
    enabled = defaults.get("models")
    if not isinstance(enabled, dict):
        raise ClientCatalogError("OpenClaw agents.defaults.models must be an object")
    for key in tuple(enabled):
        if key.startswith("anvil/"):
            enabled.pop(key)
    enabled.update({"anvil/" + alias: {} for alias in openclaw_aliases})
    if "vision.general" in openclaw_aliases:
        image_model = defaults.get("imageModel")
        if isinstance(image_model, dict):
            if not isinstance(image_model.get("primary"), str) or image_model["primary"].startswith("anvil/"):
                image_model["primary"] = "anvil/vision.general"
        elif not isinstance(image_model, str) or image_model.startswith("anvil/"):
            defaults["imageModel"] = {"primary": "anvil/vision.general"}
    compaction_models = [
        models[alias]
        for alias in openclaw_aliases
        if alias not in COMPACTION_EXCLUDED_ALIASES
    ]
    compaction = defaults.get("compaction")
    if not isinstance(compaction, Mapping):
        raise ClientCatalogError("OpenClaw compaction policy is missing")
    _validate_compaction(
        label="OpenClaw",
        compaction=compaction,
        models=compaction_models,
        reserve_key="reserveTokens",
        recent_key="keepRecentTokens",
    )
    return rendered_openclaw


def _render_pi_documents(
    catalog: Mapping,
    pi_models: Mapping,
    pi_settings: Mapping,
    *,
    base_url: str,
    api_key_env: str,
) -> tuple[dict, dict]:
    models = catalog.get("models")
    if not isinstance(models, Mapping):
        raise ClientCatalogError("catalog models are invalid")
    pi_aliases = [alias for alias in PI_ALIASES if alias in models]
    if "llm.primary" not in pi_aliases or "llm.secondary" not in pi_aliases:
        raise ClientCatalogError("Pi requires llm.primary and llm.secondary in the router catalog")

    rendered_pi_models = json.loads(json.dumps(pi_models))
    providers = rendered_pi_models.setdefault("providers", {})
    pi_provider = providers.get("anvil")
    if pi_provider is None:
        pi_provider = {
            "api": "openai-completions",
            "apiKey": "$" + api_key_env,
            "authHeader": True,
            "baseUrl": _safe_base_url(base_url),
            "compat": dict(PI_ANVIL_COMPAT),
            "models": [],
        }
        providers["anvil"] = pi_provider
    elif not isinstance(pi_provider, dict):
        raise ClientCatalogError("Pi Anvil provider must be an object")
    if pi_provider.get("apiKey") == api_key_env:
        pi_provider["apiKey"] = "$" + api_key_env
    old_pi_models = _models_by_id(pi_provider.get("models"))
    rendered_rows = []
    for alias in pi_aliases:
        row = _managed_model(old_pi_models.get(alias), models[alias], name_prefix="Anvil")
        row.setdefault("api", "openai-completions")
        row.setdefault("cost", {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0})
        rendered_rows.append(row)
    pi_provider["models"] = rendered_rows

    rendered_pi_settings = json.loads(json.dumps(pi_settings))
    enabled_models = rendered_pi_settings.get("enabledModels")
    if not isinstance(enabled_models, list):
        raise ClientCatalogError("Pi enabledModels must be a list")
    retained = [
        value
        for value in enabled_models
        if isinstance(value, str) and not value.startswith("anvil/")
    ]
    rendered_pi_settings["enabledModels"] = retained + ["anvil/" + alias for alias in pi_aliases]
    if rendered_pi_settings.get("defaultProvider") == "anvil":
        default_model = rendered_pi_settings.get("defaultModel")
        if default_model not in pi_aliases:
            raise ClientCatalogError("Pi default Anvil model is not present in the router catalog")
    pi_compaction = rendered_pi_settings.get("compaction")
    if not isinstance(pi_compaction, Mapping):
        raise ClientCatalogError("Pi compaction policy is missing")
    _validate_compaction(
        label="Pi",
        compaction=pi_compaction,
        models=[models[alias] for alias in pi_aliases],
        reserve_key="reserveTokens",
        recent_key="keepRecentTokens",
    )
    return rendered_pi_models, rendered_pi_settings


def render_client_documents(
    catalog: Mapping,
    *,
    openclaw: Mapping,
    pi_models: Mapping,
    pi_settings: Mapping,
    base_url: str = "http://127.0.0.1:8000/v1",
    api_key_env: str = "ANVIL_ROUTER_TOKEN",
) -> tuple[dict, dict, dict]:
    """Render client documents while preserving credentials and compaction policy."""
    rendered_openclaw = _render_openclaw_document(catalog, openclaw)
    rendered_pi_models, rendered_pi_settings = _render_pi_documents(
        catalog,
        pi_models,
        pi_settings,
        base_url=base_url,
        api_key_env=api_key_env,
    )
    return rendered_openclaw, rendered_pi_models, rendered_pi_settings


def _read_json_file(path: Path, *, required: bool = True) -> dict:
    if path.is_symlink():
        raise ClientCatalogError("refusing symbolic-link client config: %s" % path)
    if not path.exists():
        if required:
            raise ClientCatalogError("required client config does not exist: %s" % path)
        return {}
    if not path.is_file():
        raise ClientCatalogError("client config is not a regular file: %s" % path)
    if path.stat().st_size > DEFAULT_MAX_RESPONSE_BYTES:
        raise ClientCatalogError("client config exceeds the size limit: %s" % path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientCatalogError("client config is not valid UTF-8 JSON: %s" % path) from exc
    if not isinstance(payload, dict):
        raise ClientCatalogError("client config must contain a JSON object: %s" % path)
    return payload


def _read_text_file(path: Path) -> str:
    if path.is_symlink():
        raise ClientCatalogError("refusing symbolic-link client config: %s" % path)
    if not path.exists() or not path.is_file():
        raise ClientCatalogError("required client config does not exist: %s" % path)
    if path.stat().st_size > DEFAULT_MAX_RESPONSE_BYTES:
        raise ClientCatalogError("client config exceeds the size limit: %s" % path)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ClientCatalogError("client config is not valid UTF-8 text: %s" % path) from exc


def _yaml_block(
    lines: list[str],
    *,
    key: str,
    indent: int,
    start: int = 0,
    end: int | None = None,
) -> tuple[int, int]:
    """Locate one ordinary block-mapping key without interpreting YAML values."""
    end = len(lines) if end is None else end
    prefix = " " * indent + key + ":"
    matches = []
    for index in range(start, end):
        line = lines[index]
        if line.startswith(prefix):
            suffix = line[len(prefix):].strip()
            if not suffix or suffix.startswith("#"):
                matches.append(index)
    if len(matches) != 1:
        raise ClientCatalogError(
            "Hermes config must contain exactly one %s mapping" % key
        )
    block_start = matches[0]
    block_end = end
    for index in range(block_start + 1, end):
        stripped = lines[index].lstrip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        current_indent = len(lines[index]) - len(stripped)
        if current_indent <= indent:
            block_end = index
            break
    return block_start + 1, block_end


def _yaml_scalar(
    lines: list[str],
    *,
    key: str,
    indent: int,
    start: int,
    end: int,
) -> tuple[int, str, str]:
    prefix = " " * indent + key + ":"
    matches = []
    for index in range(start, end):
        line = lines[index]
        if line.startswith(prefix):
            matches.append((index, line[len(prefix):]))
    if len(matches) != 1:
        raise ClientCatalogError(
            "Hermes config must contain exactly one %s scalar in the selected block" % key
        )
    index, suffix = matches[0]
    value, marker, comment = suffix.partition("#")
    return index, value.strip(), ((" #" + comment) if marker else "")


def _replace_yaml_integer(
    lines: list[str],
    *,
    key: str,
    indent: int,
    start: int,
    end: int,
    value: int,
) -> None:
    index, current, comment = _yaml_scalar(
        lines,
        key=key,
        indent=indent,
        start=start,
        end=end,
    )
    try:
        parsed = int(current)
    except ValueError as exc:
        raise ClientCatalogError("Hermes %s must be an integer scalar" % key) from exc
    if isinstance(parsed, bool) or parsed <= 0:
        raise ClientCatalogError("Hermes %s must be a positive integer" % key)
    lines[index] = " " * indent + key + ": " + str(value) + comment


def _render_hermes_document(catalog: Mapping, source: str) -> bytes:
    """Patch the selected Hermes Anvil model limits without parsing credentials."""
    if "\t" in source:
        raise ClientCatalogError("Hermes config must use spaces for indentation")
    newline = "\r\n" if "\r\n" in source else "\n"
    trailing_newline = source.endswith(("\n", "\r"))
    lines = source.splitlines()

    models = catalog.get("models")
    if not isinstance(models, Mapping):
        raise ClientCatalogError("catalog models are invalid")

    model_start, model_end = _yaml_block(lines, key="model", indent=0)
    _, provider, _ = _yaml_scalar(
        lines, key="provider", indent=2, start=model_start, end=model_end
    )
    _, default_model, _ = _yaml_scalar(
        lines, key="default", indent=2, start=model_start, end=model_end
    )
    if provider != "anvil":
        raise ClientCatalogError("Hermes selected provider must be anvil")
    selected = models.get(default_model)
    if not isinstance(selected, Mapping):
        raise ClientCatalogError("Hermes selected model is absent from the router catalog")
    _replace_yaml_integer(
        lines,
        key="max_tokens",
        indent=2,
        start=model_start,
        end=model_end,
        value=selected["max_output_tokens"],
    )

    providers_start, providers_end = _yaml_block(lines, key="providers", indent=0)
    anvil_start, anvil_end = _yaml_block(
        lines,
        key="anvil",
        indent=2,
        start=providers_start,
        end=providers_end,
    )
    _replace_yaml_integer(
        lines,
        key="context_length",
        indent=4,
        start=anvil_start,
        end=anvil_end,
        value=selected["context_window"],
    )
    provider_models_start, provider_models_end = _yaml_block(
        lines,
        key="models",
        indent=4,
        start=anvil_start,
        end=anvil_end,
    )
    selected_start, selected_end = _yaml_block(
        lines,
        key=default_model,
        indent=6,
        start=provider_models_start,
        end=provider_models_end,
    )
    _replace_yaml_integer(
        lines,
        key="context_length",
        indent=8,
        start=selected_start,
        end=selected_end,
        value=selected["context_window"],
    )
    rendered = newline.join(lines)
    if trailing_newline:
        rendered += newline
    return rendered.encode("utf-8")


def _json_bytes(payload: Mapping) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write(path: Path, value: bytes, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode if mode is not None else 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _backup(paths: list[Path], root: Path, config_sha256: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = root / (stamp + "-" + config_sha256[:12])
    suffix = 1
    while candidate.exists():
        candidate = root / (stamp + "-" + config_sha256[:12] + "-%d" % suffix)
        suffix += 1
    candidate.mkdir(mode=0o700)
    entries = []
    for index, source in enumerate(paths):
        if not source.exists():
            entries.append({"source": str(source), "backup": None, "existed": False})
            continue
        target = candidate / ("%02d-%s" % (index, source.name))
        shutil.copy2(source, target)
        mode = stat.S_IMODE(source.stat().st_mode)
        entries.append({
            "source": str(source),
            "backup": target.name,
            "existed": True,
            "sha256": _file_sha256(source),
            "mode": mode,
        })
    manifest = {"config_sha256": config_sha256, "files": entries}
    _atomic_write(candidate / "manifest.json", _json_bytes(manifest), mode=0o600)
    return candidate


def _restore_backup(bundle: Path) -> None:
    manifest = _read_json_file(bundle / "manifest.json")
    for entry in manifest.get("files", []):
        source = Path(entry["source"])
        if entry.get("existed"):
            backup = bundle / entry["backup"]
            _atomic_write(source, backup.read_bytes(), mode=entry.get("mode", 0o600))
        elif source.exists():
            source.unlink()


def _summary(
    catalog: Mapping,
    *,
    clients: tuple[str, ...],
    changed: list[str],
    backup: Path | None,
    restarted: bool,
    dry_run: bool,
) -> dict:
    models = catalog["models"]
    return {
        "config_sha256": catalog["config_sha256"],
        "package_version": catalog.get("package_version"),
        "clients": list(clients),
        "models": [
            {
                "id": alias,
                "context_window": models[alias]["context_window"],
                "max_output_tokens": models[alias]["max_output_tokens"],
            }
            for alias in models
        ],
        "changed": changed,
        "backup_created": backup is not None,
        "openclaw_restarted": restarted,
        "dry_run": dry_run,
    }


def sync_clients(
    *,
    base_url: str,
    api_key_env: str = "ANVIL_ROUTER_TOKEN",
    clients: str = "openclaw,pi",
    openclaw_config: str = DEFAULT_OPENCLAW_CONFIG,
    hermes_config: str = DEFAULT_HERMES_CONFIG,
    pi_models: str = DEFAULT_PI_MODELS,
    pi_settings: str = DEFAULT_PI_SETTINGS,
    state_path: str = DEFAULT_STATE,
    backup_root: str = DEFAULT_BACKUP_ROOT,
    restart_openclaw_on_change: bool = False,
    dry_run: bool = True,
    confirm: bool = False,
    timeout_seconds: int = 15,
    environ: Mapping[str, str] | None = None,
    opener=None,
    restart: Callable[[], int] | None = None,
) -> dict:
    """Reconcile selected Mini clients from one authenticated router snapshot."""
    selected_clients = _normalize_clients(clients)
    catalog = fetch_client_catalog(
        base_url=base_url,
        api_key_env=api_key_env,
        timeout_seconds=timeout_seconds,
        environ=environ,
        opener=opener,
    )
    paths = {
        "openclaw": Path(os.path.expanduser(openclaw_config)),
        "hermes": Path(os.path.expanduser(hermes_config)),
        "pi_models": Path(os.path.expanduser(pi_models)),
        "pi_settings": Path(os.path.expanduser(pi_settings)),
        "state": Path(os.path.expanduser(state_path)),
    }
    desired = {}
    if "openclaw" in selected_clients:
        desired["openclaw"] = _json_bytes(
            _render_openclaw_document(catalog, _read_json_file(paths["openclaw"]))
        )
    if "hermes" in selected_clients:
        desired["hermes"] = _render_hermes_document(
            catalog, _read_text_file(paths["hermes"])
        )
    if "pi" in selected_clients:
        rendered_pi = _render_pi_documents(
            catalog,
            _read_json_file(paths["pi_models"]),
            _read_json_file(paths["pi_settings"]),
            base_url=base_url,
            api_key_env=api_key_env,
        )
        desired["pi_models"] = _json_bytes(rendered_pi[0])
        desired["pi_settings"] = _json_bytes(rendered_pi[1])
    changed = [name for name in desired if _file_sha256(paths[name]) != _sha256_bytes(desired[name])]
    prior_state = _read_json_file(paths["state"], required=False)
    restart_pending = (
        "openclaw" in selected_clients
        and restart_openclaw_on_change
        and prior_state.get("openclaw_restarted_sha256") != catalog["config_sha256"]
    )
    if dry_run or not confirm:
        return _summary(
            catalog,
            clients=selected_clients,
            changed=changed,
            backup=None,
            restarted=False,
            dry_run=True,
        )

    backup = None
    if changed:
        backup = _backup(
            [paths[name] for name in changed],
            Path(os.path.expanduser(backup_root)),
            catalog["config_sha256"],
        )
        try:
            for name in changed:
                mode = stat.S_IMODE(paths[name].stat().st_mode) if paths[name].exists() else 0o600
                _atomic_write(paths[name], desired[name], mode=mode)
        except Exception:
            _restore_backup(backup)
            raise

    prior_hashes = prior_state.get("file_sha256")
    file_hashes = dict(prior_hashes) if isinstance(prior_hashes, Mapping) else {}
    file_hashes.update({name: _file_sha256(paths[name]) for name in desired})
    state = {
        "config_sha256": catalog["config_sha256"],
        "file_sha256": file_hashes,
        "openclaw_restarted_sha256": prior_state.get("openclaw_restarted_sha256"),
    }
    _atomic_write(paths["state"], _json_bytes(state), mode=0o600)

    restarted = False
    if restart_pending:
        restart = restart or (lambda: 1)
        if restart() != 0:
            raise ClientCatalogError(
                "OpenClaw config was reconciled but gateway restart failed; the next run will retry"
            )
        restarted = True
        state["openclaw_restarted_sha256"] = catalog["config_sha256"]
        _atomic_write(paths["state"], _json_bytes(state), mode=0o600)
    return _summary(
        catalog,
        clients=selected_clients,
        changed=changed,
        backup=backup,
        restarted=restarted,
        dry_run=False,
    )


__all__ = [
    "ClientCatalogError",
    "fetch_client_catalog",
    "render_client_documents",
    "sync_clients",
]
