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
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_OPENCLAW_CONFIG = "~/.openclaw/openclaw.json"
DEFAULT_HERMES_CONFIG = "~/.hermes/config.yaml"
DEFAULT_HERMES_BIN = "~/.local/bin/hermes"
DEFAULT_HERMES_HOME = "~/.hermes"
DEFAULT_HERMES_MEDIA_SKILL = "~/.hermes/skills/anvil-media/SKILL.md"
DEFAULT_HERMES_MEDIA_BACKUP_ROOT = "~/.anvil-serving/backups/hermes-media"
DEFAULT_PI_MODELS = "~/.pi/agent/models.json"
DEFAULT_PI_SETTINGS = "~/.pi/agent/settings.json"
DEFAULT_STATE = "~/.anvil-serving/state/client-catalog.json"
DEFAULT_BACKUP_ROOT = "~/.anvil-serving/backups/client-catalog"
DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
PI_ALIASES = ("llm.primary", "llm.secondary", "vision.general", "vision.ocr")
OPENCLAW_EXCLUDED_ALIASES = frozenset({"llm.auxiliary"})
COMPACTION_EXCLUDED_ALIASES = frozenset({"llm.voice"})
CLIENT_TARGETS = ("openclaw", "hermes", "pi")
HERMES_PROFILE_KEYS = (
    "model",
    "compression",
    "auxiliary.vision",
    "auxiliary.compression",
    "providers",
    "custom_providers",
)
HERMES_LEGACY_TEXT_ALIASES = ("llm.primary", "llm.secondary")
HERMES_MEDIA_TOOLS = (
    "media_capabilities",
    "media_workflow_list",
    "media_workflow_show",
    "media_workflow_validate",
    "media_workflow_run",
    "media_job_status",
    "media_job_cancel",
    "media_artifact_inspect",
)
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


def _normalize_hermes_profiles(value: str) -> tuple[str, ...] | None:
    if not isinstance(value, str):
        raise ClientCatalogError("hermes_profiles must be 'all' or comma-separated names")
    requested = [item.strip() for item in value.split(",") if item.strip()]
    if not requested:
        raise ClientCatalogError("hermes_profiles must not be empty")
    if len(requested) == 1 and requested[0] == "all":
        return None
    for profile in requested:
        if (
            profile in {".", ".."}
            or "/" in profile
            or "\\" in profile
            or profile.startswith("-")
        ):
            raise ClientCatalogError("invalid Hermes profile name: %s" % profile)
    if len(set(requested)) != len(requested):
        raise ClientCatalogError("Hermes profile selection contains duplicates")
    return tuple(requested)


def _discover_hermes_profile_configs(
    hermes_home: str,
    hermes_profiles: str,
) -> dict[str, Path]:
    home = Path(os.path.expanduser(hermes_home))
    if home.is_symlink() or not home.is_dir():
        raise ClientCatalogError("Hermes home is not a regular directory")
    discovered: dict[str, Path] = {}
    default = home / "config.yaml"
    if default.exists():
        if default.is_symlink() or not default.is_file():
            raise ClientCatalogError("Hermes default config is not a regular file")
        discovered["default"] = default
    profiles = home / "profiles"
    if profiles.exists():
        if profiles.is_symlink() or not profiles.is_dir():
            raise ClientCatalogError("Hermes profiles path is not a regular directory")
        for directory in sorted(profiles.iterdir(), key=lambda item: item.name):
            if directory.is_symlink() or not directory.is_dir():
                continue
            config = directory / "config.yaml"
            if not config.exists():
                continue
            if config.is_symlink() or not config.is_file():
                raise ClientCatalogError(
                    "Hermes profile config is not a regular file: %s"
                    % directory.name
                )
            discovered[directory.name] = config
    selected = _normalize_hermes_profiles(hermes_profiles)
    if selected is None:
        if not discovered:
            raise ClientCatalogError("Hermes has no discoverable profile configs")
        return discovered
    missing = [profile for profile in selected if profile not in discovered]
    if missing:
        raise ClientCatalogError(
            "Hermes profile config is missing: %s" % ", ".join(missing)
        )
    return {profile: discovered[profile] for profile in selected}


def _run_hermes(
    hermes_bin: str,
    profile: str,
    arguments: list[str],
    *,
    timeout_seconds: int,
    run,
):
    try:
        return run(
            [os.path.expanduser(hermes_bin), "-p", profile, *arguments],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ClientCatalogError(
            "Hermes profile command failed for %s" % profile
        ) from exc


def _read_hermes_profile_key(
    hermes_bin: str,
    profile: str,
    key: str,
    *,
    timeout_seconds: int,
    run,
    required: bool,
):
    completed = _run_hermes(
        hermes_bin,
        profile,
        ["config", "get", key, "--json"],
        timeout_seconds=timeout_seconds,
        run=run,
    )
    if completed.returncode:
        if required:
            raise ClientCatalogError(
                "Hermes profile %s has no readable %s configuration"
                % (profile, key)
            )
        return None
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ClientCatalogError(
            "Hermes profile %s returned invalid JSON for %s" % (profile, key)
        ) from exc


def _read_hermes_profile(
    hermes_bin: str,
    profile: str,
    *,
    timeout_seconds: int,
    run,
) -> dict:
    result = {}
    for key in HERMES_PROFILE_KEYS:
        result[key] = _read_hermes_profile_key(
            hermes_bin,
            profile,
            key,
            timeout_seconds=timeout_seconds,
            run=run,
            required=key in {"model", "compression", "providers"},
        )
    return result


def _hermes_ratio(value, *, label: str) -> float:  # noqa: ANN001
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ClientCatalogError("%s must be a numeric ratio" % label)
    parsed = float(value)
    if not 0 < parsed < 1:
        raise ClientCatalogError(
            "%s must be greater than zero and less than one" % label
        )
    return parsed


def render_hermes_profile_plan(
    catalog: Mapping,
    current: Mapping,
    *,
    profile: str,
) -> dict:
    """Return a secret-free plan for one isolated Hermes profile."""
    models = catalog.get("models")
    if not isinstance(models, Mapping):
        raise ClientCatalogError("catalog models are invalid")
    model = current.get("model")
    if not isinstance(model, Mapping):
        raise ClientCatalogError(
            "Hermes profile %s has no model configuration" % profile
        )
    provider = model.get("provider")
    alias = model.get("default")
    if provider != "anvil":
        return {
            "profile": profile,
            "managed": False,
            "provider": provider,
            "model": alias,
            "changed_keys": [],
            "updates": {},
        }
    selected = models.get(alias)
    if not isinstance(alias, str) or not isinstance(selected, Mapping):
        raise ClientCatalogError(
            "Hermes profile %s selects an Anvil alias absent from the router"
            % profile
        )

    compression = current.get("compression")
    if not isinstance(compression, Mapping) or compression.get("enabled") is not True:
        raise ClientCatalogError(
            "Hermes profile %s compression must remain enabled" % profile
        )
    threshold = _hermes_ratio(
        compression.get("threshold"), label="Hermes compression.threshold"
    )
    target_ratio = _hermes_ratio(
        compression.get("target_ratio"),
        label="Hermes compression.target_ratio",
    )
    if target_ratio >= threshold:
        raise ClientCatalogError(
            "Hermes compression target_ratio must remain below threshold"
        )
    if int(selected["context_window"] * (1 - threshold)) < selected["max_output_tokens"]:
        raise ClientCatalogError(
            "Hermes profile %s compression leaves less headroom than max output"
            % profile
        )

    vision = models.get("vision.general")
    if not isinstance(vision, Mapping) or "image" not in vision.get("input", []):
        raise ClientCatalogError("router has no image-capable vision.general alias")
    vision_current = current.get("auxiliary.vision")
    vision_current = vision_current if isinstance(vision_current, Mapping) else {}
    compression_current = current.get("auxiliary.compression")
    compression_current = (
        compression_current if isinstance(compression_current, Mapping) else {}
    )
    updates = {
        "model.context_length": selected["context_window"],
        "model.max_tokens": selected["max_output_tokens"],
        "auxiliary.vision.provider": "anvil",
        "auxiliary.vision.model": "vision.general",
        "auxiliary.compression.context_length": selected["context_window"],
    }
    current_values = {
        "model.context_length": model.get("context_length"),
        "model.max_tokens": model.get("max_tokens"),
        "auxiliary.vision.provider": vision_current.get("provider"),
        "auxiliary.vision.model": vision_current.get("model"),
        "auxiliary.compression.context_length": compression_current.get(
            "context_length"
        ),
    }

    providers = current.get("providers")
    provider_config = (
        providers.get(provider) if isinstance(providers, Mapping) else None
    )
    if not isinstance(provider_config, Mapping):
        raise ClientCatalogError(
            "Hermes profile %s has no Anvil provider configuration" % profile
        )
    old_provider_models = provider_config.get("models")
    old_provider_models = (
        old_provider_models if isinstance(old_provider_models, Mapping) else {}
    )
    rendered_provider_models = {}
    for model_id in HERMES_LEGACY_TEXT_ALIASES:
        metadata = models.get(model_id)
        if not isinstance(metadata, Mapping):
            continue
        existing = old_provider_models.get(model_id)
        row = dict(existing) if isinstance(existing, Mapping) else {}
        row["context_length"] = metadata["context_window"]
        rendered_provider_models[model_id] = row
    updates.update(
        {
            "providers.%s.default_model" % provider: alias,
            "providers.%s.context_length" % provider: selected["context_window"],
            "providers.%s.models" % provider: rendered_provider_models,
        }
    )
    current_values.update(
        {
            "providers.%s.default_model" % provider: provider_config.get(
                "default_model"
            ),
            "providers.%s.context_length" % provider: provider_config.get(
                "context_length"
            ),
            "providers.%s.models" % provider: old_provider_models,
        }
    )
    unsets = []
    extra_body = provider_config.get("extra_body")
    if isinstance(extra_body, Mapping) and "chat_template_kwargs" in extra_body:
        remaining_extra_body = dict(extra_body)
        remaining_extra_body.pop("chat_template_kwargs")
        extra_body_key = "providers.%s.extra_body" % provider
        if remaining_extra_body:
            updates[extra_body_key] = remaining_extra_body
            current_values[extra_body_key] = extra_body
        else:
            unsets.append(extra_body_key)

    custom_providers = current.get("custom_providers")
    rendered_custom = (
        json.loads(json.dumps(custom_providers))
        if isinstance(custom_providers, list)
        else []
    )
    custom_changed = False
    anvil_custom_found = False
    for custom in rendered_custom:
        if not isinstance(custom, dict):
            continue
        name = custom.get("name")
        if not isinstance(name, str) or not name.casefold().startswith("anvil"):
            continue
        anvil_custom_found = True
        if custom.get("model") != alias:
            custom["model"] = alias
            custom_changed = True
        old_models = custom.get("models")
        rendered_models = dict(old_models) if isinstance(old_models, Mapping) else {}
        for model_id, metadata in models.items():
            existing = rendered_models.get(model_id)
            row = dict(existing) if isinstance(existing, Mapping) else {}
            if row.get("context_length") != metadata["context_window"]:
                row["context_length"] = metadata["context_window"]
                custom_changed = True
            rendered_models[model_id] = row
        for model_id in tuple(rendered_models):
            if model_id not in models:
                rendered_models.pop(model_id)
                custom_changed = True
        custom["models"] = rendered_models
    if not anvil_custom_found:
        raise ClientCatalogError(
            "Hermes profile %s has no Anvil custom provider catalog" % profile
        )
    if custom_changed:
        updates["custom_providers"] = rendered_custom
        current_values["custom_providers"] = custom_providers

    changed_update_keys = [
        key for key, value in updates.items() if current_values.get(key) != value
    ]
    changed_keys = [*changed_update_keys, *unsets]
    return {
        "profile": profile,
        "managed": True,
        "provider": provider,
        "model": alias,
        "context_window": selected["context_window"],
        "max_output_tokens": selected["max_output_tokens"],
        "compression": {
            "enabled": True,
            "threshold": threshold,
            "target_ratio": target_ratio,
        },
        "vision_model": "vision.general",
        "vision_context_window": vision["context_window"],
        "changed_keys": changed_keys,
        "updates": {key: updates[key] for key in changed_update_keys},
        "unsets": unsets,
    }


def plan_hermes_profiles(
    catalog: Mapping,
    *,
    hermes_bin: str,
    hermes_home: str,
    hermes_profiles: str,
    timeout_seconds: int,
    run=subprocess.run,
) -> tuple[list[dict], dict[str, Path]]:
    configs = _discover_hermes_profile_configs(hermes_home, hermes_profiles)
    rows = []
    for profile in configs:
        current = _read_hermes_profile(
            hermes_bin,
            profile,
            timeout_seconds=timeout_seconds,
            run=run,
        )
        rows.append(render_hermes_profile_plan(catalog, current, profile=profile))
    return rows, configs


def _hermes_config_value(value) -> str:  # noqa: ANN001
    if isinstance(value, (dict, list, bool, int, float)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _apply_hermes_profile_plans(
    rows: list[Mapping],
    *,
    hermes_bin: str,
    timeout_seconds: int,
    run=subprocess.run,
) -> None:
    for row in rows:
        profile = row["profile"]
        for key in row.get("unsets", []):
            completed = _run_hermes(
                hermes_bin,
                profile,
                ["config", "unset", key],
                timeout_seconds=timeout_seconds,
                run=run,
            )
            if completed.returncode:
                raise ClientCatalogError(
                    "Hermes profile %s removal failed for %s" % (profile, key)
                )
        for key, value in row.get("updates", {}).items():
            completed = _run_hermes(
                hermes_bin,
                profile,
                ["config", "set", key, _hermes_config_value(value)],
                timeout_seconds=timeout_seconds,
                run=run,
            )
            if completed.returncode:
                raise ClientCatalogError(
                    "Hermes profile %s update failed for %s" % (profile, key)
                )
        if row.get("updates") or row.get("unsets"):
            checked = _run_hermes(
                hermes_bin,
                profile,
                ["config", "check"],
                timeout_seconds=timeout_seconds,
                run=run,
            )
            if checked.returncode:
                raise ClientCatalogError(
                    "Hermes profile %s failed config validation" % profile
                )


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
    hermes_rows: list[Mapping] | None,
    hermes_restarted: bool,
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
        "hermes_profiles": [
            {
                key: row[key]
                for key in (
                    "profile",
                    "managed",
                    "provider",
                    "model",
                    "context_window",
                    "max_output_tokens",
                    "compression",
                    "vision_model",
                    "vision_context_window",
                    "changed_keys",
                )
                if key in row
            }
            for row in (hermes_rows or [])
        ],
        "hermes_restarted": hermes_restarted,
        "dry_run": dry_run,
    }


def _environment_reference(name: str) -> str:
    if (
        not isinstance(name, str)
        or not name
        or name[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_"
        or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in name)
    ):
        raise ClientCatalogError("Hermes media environment reference is invalid")
    return name


def _hermes_media_skill_bytes() -> bytes:
    path = Path(__file__).resolve().parent / "_hermes_skills" / "anvil-media" / "SKILL.md"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ClientCatalogError("packaged Hermes media skill is unavailable")
    payload = path.read_bytes()
    if not payload.startswith(b"---\nname: anvil-media\n"):
        raise ClientCatalogError("packaged Hermes media skill is invalid")
    return payload


def _hermes_media_skill_path(hermes_home: str, skill_path: str) -> Path:
    home = Path(os.path.expanduser(hermes_home)).resolve(strict=False)
    root = (home / "skills").resolve(strict=False)
    target = Path(os.path.expanduser(skill_path)).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ClientCatalogError(
            "Hermes media skill path must remain under the Hermes skills directory"
        ) from exc
    if target.exists() and (target.is_symlink() or not target.is_file()):
        raise ClientCatalogError("Hermes media skill target is not a regular file")
    return target


def _hermes_media_server(
    *,
    anvil_command: str,
    mcp_url_env: str,
    token_env: str,
) -> dict:
    if (
        not isinstance(anvil_command, str)
        or not anvil_command
        or len(anvil_command) > 512
        or "\x00" in anvil_command
    ):
        raise ClientCatalogError("Anvil command for Hermes media is invalid")
    mcp_url_env = _environment_reference(mcp_url_env)
    token_env = _environment_reference(token_env)
    return {
        "command": anvil_command,
        "args": [
            "mcp",
            "serve",
            "--controller-url",
            "${%s}" % mcp_url_env,
            "--auth-env",
            token_env,
        ],
        "env": {token_env: "${%s}" % token_env},
        "tools": {
            "include": list(HERMES_MEDIA_TOOLS),
            "resources": False,
            "prompts": False,
        },
    }


def _hermes_media_raw_block(config: Path) -> str | None:
    """Return Hermes' raw ``mcp_servers.anvil-media`` YAML block.

    ``hermes config get`` intentionally returns a resolved value, including
    values loaded from the profile's ``.env`` file.  The reconciler must verify
    that the file still contains environment references without retaining or
    comparing the resolved credential.  Hermes itself owns this compact YAML
    shape; this scanner only locates the one mapping written by ``config set``
    and fails closed for duplicate or unexpected structure.
    """

    try:
        if config.stat().st_size > DEFAULT_MAX_RESPONSE_BYTES:
            return None
        lines = config.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    parents = [index for index, line in enumerate(lines) if line == "mcp_servers:"]
    if len(parents) != 1:
        return None
    parent = parents[0]
    parent_end = next(
        (
            index
            for index in range(parent + 1, len(lines))
            if lines[index] and not lines[index][0].isspace()
        ),
        len(lines),
    )
    children = [
        index
        for index in range(parent + 1, parent_end)
        if lines[index] == "  anvil-media:"
    ]
    if len(children) != 1:
        return None
    child = children[0]
    child_end = next(
        (
            index
            for index in range(child + 1, parent_end)
            if lines[index]
            and len(lines[index]) - len(lines[index].lstrip()) <= 2
        ),
        parent_end,
    )
    return "\n".join(lines[child:child_end])


def _hermes_media_server_matches(
    observed: object,
    expected: Mapping,
    *,
    config: Path,
    mcp_url_env: str,
    token_env: str,
) -> bool:
    """Compare a resolved Hermes value while proving raw env references."""

    if observed == expected:
        return True
    if not isinstance(observed, Mapping):
        return False
    block = _hermes_media_raw_block(config)
    if block is None:
        return False
    url_reference = "${%s}" % mcp_url_env
    token_reference = "${%s}" % token_env
    stripped = [line.strip() for line in block.splitlines()]
    if stripped.count("- " + url_reference) != 1:
        return False
    if stripped.count("%s: %s" % (token_env, token_reference)) != 1:
        return False
    try:
        normalized = json.loads(json.dumps(observed))
        expected_args = expected["args"]
        observed_args = normalized["args"]
        url_index = expected_args.index(url_reference)
        if not isinstance(observed_args[url_index], str) or not observed_args[url_index]:
            return False
        observed_args[url_index] = url_reference
        observed_token = normalized["env"][token_env]
        if not isinstance(observed_token, str) or not observed_token:
            return False
        normalized["env"][token_env] = token_reference
    except (KeyError, TypeError, ValueError, IndexError):
        return False
    return normalized == expected


def sync_hermes_media(
    *,
    hermes_bin: str = DEFAULT_HERMES_BIN,
    hermes_home: str = DEFAULT_HERMES_HOME,
    hermes_profiles: str = "default",
    skill_path: str = DEFAULT_HERMES_MEDIA_SKILL,
    backup_root: str = DEFAULT_HERMES_MEDIA_BACKUP_ROOT,
    anvil_command: str = "anvil-serving",
    mcp_url_env: str = "ANVIL_MEDIA_MCP_URL",
    token_env: str = "ANVIL_ROUTER_TOKEN",
    restart_hermes_on_change: bool = False,
    dry_run: bool = True,
    confirm: bool = False,
    timeout_seconds: int = 15,
    run=subprocess.run,
    restart_hermes: Callable[[], int] | None = None,
) -> dict:
    """Reconcile the narrow Anvil media MCP server and packaged Hermes skill."""

    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds < 1
        or timeout_seconds > 120
    ):
        raise ClientCatalogError("timeout_seconds must be between 1 and 120")
    configs = _discover_hermes_profile_configs(hermes_home, hermes_profiles)
    target = _hermes_media_skill_path(hermes_home, skill_path)
    skill = _hermes_media_skill_bytes()
    server = _hermes_media_server(
        anvil_command=anvil_command,
        mcp_url_env=mcp_url_env,
        token_env=token_env,
    )
    rows = []
    for profile in configs:
        current = _read_hermes_profile_key(
            hermes_bin,
            profile,
            "mcp_servers.anvil-media",
            timeout_seconds=timeout_seconds,
            run=run,
            required=False,
        )
        rows.append(
            {
                "profile": profile,
                "changed": not _hermes_media_server_matches(
                    current,
                    server,
                    config=configs[profile],
                    mcp_url_env=mcp_url_env,
                    token_env=token_env,
                ),
                "config": configs[profile],
            }
        )
    skill_sha256 = _sha256_bytes(skill)
    changed = []
    if _file_sha256(target) != skill_sha256:
        changed.append("skill")
    changed.extend(
        "hermes:" + row["profile"] for row in rows if row["changed"]
    )
    summary = {
        "schema": "anvil-serving.hermes-media-sync/v1",
        "profiles": [
            {"profile": row["profile"], "changed": row["changed"]}
            for row in rows
        ],
        "changed": changed,
        "skillSha256": skill_sha256,
        "tools": list(HERMES_MEDIA_TOOLS),
        "mcpUrlEnv": mcp_url_env,
        "tokenEnv": token_env,
        "backupCreated": False,
        "hermesRestarted": False,
        "dryRun": True,
    }
    if dry_run or not confirm:
        return summary
    desired_sha256 = _sha256_bytes(
        skill + _json_bytes({"mcp_server": server})
    )
    backup = None
    if changed:
        backup_paths = []
        if "skill" in changed:
            backup_paths.append(target)
        backup_paths.extend(row["config"] for row in rows if row["changed"])
        backup = _backup(
            backup_paths,
            Path(os.path.expanduser(backup_root)),
            desired_sha256,
        )
        try:
            if "skill" in changed:
                _atomic_write(target, skill, mode=0o644)
            for row in rows:
                if not row["changed"]:
                    continue
                completed = _run_hermes(
                    hermes_bin,
                    row["profile"],
                    [
                        "config",
                        "set",
                        "mcp_servers.anvil-media",
                        _hermes_config_value(server),
                    ],
                    timeout_seconds=timeout_seconds,
                    run=run,
                )
                if completed.returncode:
                    raise ClientCatalogError(
                        "Hermes media MCP update failed for %s" % row["profile"]
                    )
                checked = _run_hermes(
                    hermes_bin,
                    row["profile"],
                    ["config", "check"],
                    timeout_seconds=timeout_seconds,
                    run=run,
                )
                if checked.returncode:
                    raise ClientCatalogError(
                        "Hermes profile %s failed config validation" % row["profile"]
                    )
            for row in rows:
                observed = _read_hermes_profile_key(
                    hermes_bin,
                    row["profile"],
                    "mcp_servers.anvil-media",
                    timeout_seconds=timeout_seconds,
                    run=run,
                    required=True,
                )
                if not _hermes_media_server_matches(
                    observed,
                    server,
                    config=row["config"],
                    mcp_url_env=mcp_url_env,
                    token_env=token_env,
                ):
                    raise ClientCatalogError(
                        "Hermes media MCP verification still reports configuration drift"
                    )
            if _file_sha256(target) != skill_sha256:
                raise ClientCatalogError(
                    "Hermes media skill verification still reports file drift"
                )
        except Exception:
            _restore_backup(backup)
            raise
    restarted = False
    if changed and restart_hermes_on_change:
        restart_hermes = restart_hermes or (lambda: 1)
        if restart_hermes() != 0:
            if backup is not None:
                _restore_backup(backup)
                if restart_hermes() != 0:
                    raise ClientCatalogError(
                        "Hermes media rollback was restored on disk but its restart failed"
                    )
            raise ClientCatalogError(
                "Hermes media configuration was restored after gateway restart failed"
            )
        restarted = True
    return {
        **summary,
        "backupCreated": backup is not None,
        "hermesRestarted": restarted,
        "dryRun": False,
    }


def sync_clients(
    *,
    base_url: str,
    api_key_env: str = "ANVIL_ROUTER_TOKEN",
    clients: str = "openclaw,pi",
    openclaw_config: str = DEFAULT_OPENCLAW_CONFIG,
    hermes_config: str = DEFAULT_HERMES_CONFIG,
    hermes_bin: str = DEFAULT_HERMES_BIN,
    hermes_home: str = DEFAULT_HERMES_HOME,
    hermes_profiles: str | None = None,
    pi_models: str = DEFAULT_PI_MODELS,
    pi_settings: str = DEFAULT_PI_SETTINGS,
    state_path: str = DEFAULT_STATE,
    backup_root: str = DEFAULT_BACKUP_ROOT,
    restart_openclaw_on_change: bool = False,
    restart_hermes_on_change: bool = False,
    dry_run: bool = True,
    confirm: bool = False,
    timeout_seconds: int = 15,
    environ: Mapping[str, str] | None = None,
    opener=None,
    restart: Callable[[], int] | None = None,
    restart_hermes: Callable[[], int] | None = None,
    hermes_run=subprocess.run,
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
    hermes_rows: list[dict] = []
    hermes_configs: dict[str, Path] = {}
    if "openclaw" in selected_clients:
        desired["openclaw"] = _json_bytes(
            _render_openclaw_document(catalog, _read_json_file(paths["openclaw"]))
        )
    if "hermes" in selected_clients:
        if hermes_profiles:
            hermes_rows, hermes_configs = plan_hermes_profiles(
                catalog,
                hermes_bin=hermes_bin,
                hermes_home=hermes_home,
                hermes_profiles=hermes_profiles,
                timeout_seconds=timeout_seconds,
                run=hermes_run,
            )
        else:
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
    changed = [
        name
        for name in desired
        if _file_sha256(paths[name]) != _sha256_bytes(desired[name])
    ]
    changed.extend(
        "hermes:" + row["profile"]
        for row in hermes_rows
        if row.get("changed_keys")
    )
    prior_state = _read_json_file(paths["state"], required=False)
    openclaw_restart_pending = (
        "openclaw" in selected_clients
        and restart_openclaw_on_change
        and prior_state.get("openclaw_restarted_sha256") != catalog["config_sha256"]
    )
    hermes_restart_pending = (
        restart_hermes_on_change
        and any(
            row.get("profile") == "default" and row.get("changed_keys")
            for row in hermes_rows
        )
    )
    if dry_run or not confirm:
        return _summary(
            catalog,
            clients=selected_clients,
            changed=changed,
            backup=None,
            restarted=False,
            hermes_rows=hermes_rows,
            hermes_restarted=False,
            dry_run=True,
        )

    backup = None
    if changed:
        backup_paths = [
            (
                hermes_configs[name.split(":", 1)[1]]
                if name.startswith("hermes:")
                else paths[name]
            )
            for name in changed
        ]
        backup = _backup(
            backup_paths,
            Path(os.path.expanduser(backup_root)),
            catalog["config_sha256"],
        )
        try:
            for name in desired:
                if name not in changed:
                    continue
                mode = stat.S_IMODE(paths[name].stat().st_mode) if paths[name].exists() else 0o600
                _atomic_write(paths[name], desired[name], mode=mode)
            if hermes_rows:
                _apply_hermes_profile_plans(
                    hermes_rows,
                    hermes_bin=hermes_bin,
                    timeout_seconds=timeout_seconds,
                    run=hermes_run,
                )
                verified_rows, _ = plan_hermes_profiles(
                    catalog,
                    hermes_bin=hermes_bin,
                    hermes_home=hermes_home,
                    hermes_profiles=hermes_profiles or "default",
                    timeout_seconds=timeout_seconds,
                    run=hermes_run,
                )
                if any(row.get("changed_keys") for row in verified_rows):
                    raise ClientCatalogError(
                        "Hermes profile verification still reports configuration drift"
                    )
        except Exception:
            _restore_backup(backup)
            raise

    hermes_restarted = False
    if hermes_restart_pending:
        restart_hermes = restart_hermes or (lambda: 1)
        if restart_hermes() != 0:
            if backup is not None:
                _restore_backup(backup)
                restart_hermes()
            raise ClientCatalogError(
                "Hermes profile reconciliation was restored after gateway restart failed"
            )
        hermes_restarted = True

    prior_hashes = prior_state.get("file_sha256")
    file_hashes = dict(prior_hashes) if isinstance(prior_hashes, Mapping) else {}
    file_hashes.update({name: _file_sha256(paths[name]) for name in desired})
    file_hashes.update(
        {
            "hermes:" + profile: _file_sha256(path)
            for profile, path in hermes_configs.items()
        }
    )
    state = {
        "config_sha256": catalog["config_sha256"],
        "file_sha256": file_hashes,
        "openclaw_restarted_sha256": prior_state.get("openclaw_restarted_sha256"),
    }
    _atomic_write(paths["state"], _json_bytes(state), mode=0o600)

    restarted = False
    if openclaw_restart_pending:
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
        hermes_rows=hermes_rows,
        hermes_restarted=hermes_restarted,
        dry_run=False,
    )


__all__ = [
    "ClientCatalogError",
    "fetch_client_catalog",
    "render_client_documents",
    "render_hermes_profile_plan",
    "plan_hermes_profiles",
    "sync_hermes_media",
    "sync_clients",
]
