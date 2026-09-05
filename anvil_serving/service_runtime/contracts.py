"""Small shared lifecycle contracts; no process or network side effects."""
from __future__ import annotations

import re


class ServiceError(ValueError):
    """A bounded service operation failed without losing its error category."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


READ_ACTIONS = ("status", "discover", "capabilities", "logs")
MUTATING_ACTIONS = ("adopt", "install", "up", "down", "restart", "enable", "disable")
MODEL_ENGINES = frozenset({"mlx-lm", "mlx-vlm", "vllm", "sglang", "llama-cpp",
                           "ollama", "lmstudio", "parakeet", "kokoro", "generic"})
ENGINES = MODEL_ENGINES | {"none"}
IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
MAX_BYTES = 1024 * 1024


def identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ServiceError("bad_config", f"{field} must be a bounded identifier")
    return value


def validate_platform(binding: dict, host_os: str) -> None:
    manager, engine = binding["manager"], binding["engine"]
    if host_os not in {"macos", "windows", "linux"}:
        raise ServiceError("unsupported_platform", "only macOS, Windows and Linux are supported")
    if manager == "launchd":
        if host_os != "macos":
            raise ServiceError("unsupported_platform", "launchd requires the owning macOS host")
        legacy = binding.get("support") == "legacy" and engine in {"parakeet", "kokoro"}
        if engine not in {"mlx-lm", "mlx-vlm", "none"} and not legacy:
            raise ServiceError("unsupported_engine", "native model serving requires MLX or an adopted legacy binding")
    elif engine in {"mlx-lm", "mlx-vlm"}:
        raise ServiceError("unsupported_engine", "MLX requires native macOS execution")


def capabilities(host_os: str) -> dict:
    return {
        "schema": "anvil-services-capabilities/v1",
        "host_os": host_os,
        "managers": ["launchd", "docker"] if host_os == "macos" else ["docker"]
        if host_os in {"windows", "linux"} else [],
        "native_engines": ["mlx-lm", "mlx-vlm"] if host_os == "macos" else [],
        "actions": list(READ_ACTIONS + MUTATING_ACTIONS),
        "cloud": "not_implemented",
        "neocloud": "not_implemented",
        "controller_self_mutation": "requires_recovery_transport",
    }
