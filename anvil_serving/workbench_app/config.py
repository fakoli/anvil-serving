"""Explicit private Workbench connections; browser input never selects a URL/path."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from ..observability.dashboard.contracts import fields, identifier


def absolute_path(value):
    if type(value) is not str or not Path(value).is_absolute():
        raise ValueError("Workbench paths must be absolute private configuration")
    return value


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Workbench bound is out of range")
    return value


def validate_config(value):
    fields(value, required=("state_path",), optional=("connectors", "presets", "projects", "pi", "pi_storage", "retention_days"))
    absolute_path(value["state_path"])
    integer(value.get("retention_days", 30), 1, 365)
    for group in ("connectors", "presets", "projects"):
        rows = value.get(group, [])
        if type(rows) is not list or len(rows) > 64:
            raise ValueError("Workbench catalogs must be bounded lists")
        seen = set()
        for row in rows:
            key = identifier(row.get("id"))
            if key in seen:
                raise ValueError("Duplicate Workbench catalog identity")
            seen.add(key)
            if group == "connectors":
                fields(row, required=("id", "label", "resource_id", "base_url", "models"),
                       optional=("token_env", "token_ref", "host_id", "max_output_tokens", "timeout_seconds"))
                identifier(row["resource_id"])
                url = urlsplit(row["base_url"])
                if (url.scheme != "https" and not (url.scheme == "http" and url.hostname == "127.0.0.1")) or not url.hostname or url.username or url.password or url.query or url.fragment or url.hostname == "localhost":
                    raise ValueError("Invalid declared model connection")
                if "token_env" in row and (type(row["token_env"]) is not str or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", row["token_env"])):
                    raise ValueError("Use a protected environment secret reference")
                if "token_ref" in row:
                    reference = row["token_ref"]
                    if "token_env" in row or type(reference) is not str or not (re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", reference) or (reference.startswith("file:/") and Path(reference[5:]).is_absolute())):
                        raise ValueError("Use one protected credential reference")
                if type(row["models"]) is not list or not 1 <= len(row["models"]) <= 64:
                    raise ValueError("Declare the exact allowed model identities")
                if any(type(model) is not str or not 1 <= len(model) <= 192 or any(ord(c) < 32 for c in model) for model in row["models"]):
                    raise ValueError("Invalid declared model identity")
                integer(row.get("max_output_tokens", 4096), 1, 32768)
                integer(row.get("timeout_seconds", 120), 1, 300)
            elif group == "presets":
                fields(row, required=("id", "label", "temperature", "max_tokens"), optional=("system",))
                if type(row["temperature"]) not in (int, float) or not 0 <= row["temperature"] <= 2:
                    raise ValueError("Invalid preset temperature")
                integer(row["max_tokens"], 1, 32768)
                if type(row.get("system", "")) is not str or len(row.get("system", "")) > 16384:
                    raise ValueError("System instructions exceed their bound")
            else:
                fields(row, required=("id", "label", "resource_id", "checkout", "anvil_binary"), optional=("runner_root",))
                identifier(row["resource_id"])
                absolute_path(row["checkout"])
                absolute_path(row["anvil_binary"])
                if "runner_root" in row:
                    absolute_path(row["runner_root"])
    if value.get("pi"):
        pi = value["pi"]
        fields(pi, required=("id", "state_root", "engine_binary", "image", "uid", "gid", "models", "thinking_levels"),
               optional=("cpus", "memory_bytes", "pids", "network", "proxy_url", "provider_secret_refs", "max_sessions", "max_state_bytes", "max_active", "max_wall_seconds", "max_per_principal", "max_per_task", "provider_egress", "provider_endpoints", "runner_storage_root"))
        identifier(pi["id"])
        absolute_path(pi["state_root"])
        absolute_path(pi["runner_storage_root"])
        absolute_path(pi["engine_binary"])
        if type(pi["image"]) is not str or not re.fullmatch(r"(?:[A-Za-z0-9_./:-]+@)?sha256:[a-f0-9]{64}", pi["image"]):
            raise ValueError("Pin the isolated Pi image by immutable digest")
        integer(pi["uid"], 1, 2**31 - 1)
        integer(pi["gid"], 1, 2**31 - 1)
        if type(pi.get("cpus", 2)) not in (int, float) or not 0.1 <= pi.get("cpus", 2) <= 4:
            raise ValueError("Pi runner CPU bound must be between 0.1 and 4")
        integer(pi.get("memory_bytes", 2 * 1024**3), 64 * 1024**2, 4 * 1024**3)
        integer(pi.get("pids", 256), 16, 512)
        network = pi.get("network", "none")
        if network == "none":
            if "proxy_url" in pi:
                raise ValueError("Pi proxy requires an approved isolated network")
        elif not isinstance(network, str) or not re.fullmatch(r"isolated-[A-Za-z0-9_.-]{1,96}", network) or not isinstance(pi.get("proxy_url"), str):
            raise ValueError("Pi cloud access requires an approved isolated network and proxy")
        elif not (url := urlsplit(pi["proxy_url"])).scheme == "http" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Invalid isolated Pi proxy URL")
        if type(pi["models"]) is not dict or not 1 <= len(pi["models"]) <= 16:
            raise ValueError("Declare the Pi provider/model policy")
        for provider, models in pi["models"].items():
            identifier(provider)
            if type(models) is not list or not 1 <= len(models) <= 64 or any(type(m) is not str or not 1 <= len(m) <= 192 or any(ord(c) < 32 for c in m) for m in models):
                raise ValueError("Declare exact Pi model identities")
        if type(pi["thinking_levels"]) is not list or not pi["thinking_levels"] or any(t not in {"off", "minimal", "low", "medium", "high", "xhigh"} for t in pi["thinking_levels"]):
            raise ValueError("Invalid Pi thinking policy")
        refs = pi.get("provider_secret_refs", {})
        if type(refs) is not dict or set(refs) != set(pi["models"]) or any(type(provider) is not str or type(values) is not dict or not values or any(not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", target) or type(reference) is not str or not (re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", reference) or (reference.startswith("file:/") and Path(reference[5:]).is_absolute())) for target, reference in values.items()) for provider, values in refs.items()):
            raise ValueError("Use explicit Pi provider secret references")
        egress = pi.get("provider_egress", {})
        if type(egress) is not dict or not set(egress).issubset(pi["models"]):
            raise ValueError("Pi egress must select declared providers")
        for origins in egress.values():
            if type(origins) is not list or not 1 <= len(origins) <= 8 or any(type(origin) is not str for origin in origins):
                raise ValueError("Declare bounded exact Pi provider origins")
        endpoints = pi.get("provider_endpoints", {})
        if type(endpoints) is not dict or set(endpoints) != set(pi["models"]):
            raise ValueError("Pi custom endpoints must select declared providers")
        for provider, endpoint in endpoints.items():
            fields(endpoint, required=("base_url", "api", "credential_env"), optional=("context_window", "max_tokens", "reasoning"))
            url = urlsplit(endpoint["base_url"])
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("Invalid explicit Pi provider endpoint")
            if endpoint["api"] not in {"openai-completions", "openai-responses", "anthropic-messages"} or endpoint["credential_env"] not in refs[provider]:
                raise ValueError("Pi endpoint must use its declared dialect and selected credential reference")
            if not refs[provider][endpoint["credential_env"]].startswith("file:/"):
                raise ValueError("Provider gateways require a protected credential file reference")
            origin = f"{url.scheme}://{url.netloc}"
            if origin not in egress.get(provider, []):
                raise ValueError("Pi custom endpoint requires its exact approved egress origin")
            integer(endpoint.get("context_window", 32768), 1024, 2000000)
            integer(endpoint.get("max_tokens", 4096), 1, 32768)
            if type(endpoint.get("reasoning", False)) is not bool:
                raise ValueError("Pi model reasoning declaration must be boolean")
        integer(pi.get("max_sessions", 100), 1, 100)
        integer(pi.get("max_state_bytes", 64 * 1024**2), 1024**2, 1024**3)
        integer(pi.get("max_active", 2), 1, 2)
        integer(pi.get("max_per_principal", 2), 1, 2)
        integer(pi.get("max_per_task", 1), 1, 2)
        integer(pi.get("max_wall_seconds", 4 * 3600), 60, 4 * 3600)
    if value.get("pi_storage"):
        from .pi_storage import storage_config
        storage_config(value)
    return value
