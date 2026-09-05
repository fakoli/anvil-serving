"""Bounded engine metadata reads; never generate, load, or select a model."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from .contracts import MAX_BYTES


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Metadata credentials never leave the exact declared endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open


def inspect(binding: dict, *, open_url=_OPEN, timeout: float = 3.0) -> dict:
    result = {"engine": binding["engine"], "ready": None, "endpoint_reachable": None,
              "model_state": "unknown", "loaded_models": None, "advertised_models": None}
    endpoint = binding.get("endpoint")
    if not endpoint:
        return result
    engine = binding["engine"]
    default_path = "/api/ps" if engine == "ollama" else "/api/v0/models" if engine == "lmstudio" else (
        "/health" if engine in {"none", "parakeet", "kokoro"} else "/v1/models")
    path = binding.get("models_path") or binding.get("health_path") or default_path
    headers = {"Accept": "application/json"}
    if binding.get("api_key_env"):
        token = os.environ.get(binding["api_key_env"], "")
        if not token:
            return {**result, "ready": False, "error": "credential_unavailable"}
        headers["Authorization"] = "Bearer " + token
    url = urlsplit(endpoint)
    # Metadata paths are absolute on the owning host, independent of an API base suffix.
    request = urllib.request.Request(urlunsplit((url.scheme, url.netloc, path, "", "")), headers=headers)
    try:
        with open_url(request, timeout=min(timeout, 10.0)) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return {**result, "ready": False, "error": "response_too_large"}
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("metadata must be an object")
        result.update(endpoint_reachable=True, ready=True)
        if engine == "ollama" and path == "/api/ps":
            rows = data.get("models")
            if not isinstance(rows, list):
                raise ValueError("missing models")
            names = [row.get("name", row.get("model")) for row in rows if isinstance(row, dict)]
            if len(names) != len(rows) or any(not isinstance(name, str) or len(name) > 512 for name in names):
                raise ValueError("invalid models")
            result["loaded_models"] = names
        elif engine == "lmstudio" and path == "/api/v0/models":
            rows = data.get("data")
            if not isinstance(rows, list) or any(not isinstance(row, dict) or
                    not isinstance(row.get("id"), str) or len(row["id"]) > 512 or row.get("state") not in {"loaded", "not-loaded"} for row in rows):
                raise ValueError("invalid model states")
            result["loaded_models"] = [row["id"] for row in rows if row["state"] == "loaded"]
            result["advertised_models"] = [row["id"] for row in rows]
        elif "data" in data:
            rows = data["data"]
            if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("id"), str) or len(row["id"]) > 512 for row in rows):
                raise ValueError("invalid model metadata")
            result["advertised_models"] = [row["id"] for row in rows]
        elif str(data.get("status", "")).lower() not in {"ok", "healthy", "ready"} and data.get("ok") is not True:
            result["ready"] = False
        expected = binding.get("model")
        if result["loaded_models"] is not None:
            loaded = result["loaded_models"]
            result["model_state"] = "loaded" if (expected in loaded if expected else bool(loaded)) else "not_loaded"
            if expected and expected not in loaded:
                result["ready"] = False
        elif expected and result["advertised_models"] is not None:
            result["ready"] = expected in result["advertised_models"]
        for key in ("loaded_models", "advertised_models"):
            if result[key] is not None and len(result[key]) > 128:
                result[key] = result[key][:128]
                result["inventory_truncated"] = True
        return result
    except urllib.error.HTTPError as exc:
        return {**result, "ready": False, "endpoint_reachable": True, "error": f"http_{exc.code}"}
    except (OSError, ValueError, KeyError, TypeError):
        return {**result, "ready": False, "error": "metadata_unavailable"}
