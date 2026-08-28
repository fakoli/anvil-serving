"""Frozen A2A 1.0 binding constants and media capability declarations."""

from __future__ import annotations


A2A_VERSION = "1.0"
A2A_PATH = "/a2a"
AGENT_CARD_PATH = "/.well-known/agent-card.json"
JSONRPC_METHODS = frozenset(
    {"SendMessage", "SendStreamingMessage", "GetTask", "CancelTask", "SubscribeToTask"}
)
INPUT_MODES = ("application/json", "text/plain")
IMAGE_OUTPUT_MODES = ("application/json", "image/png")
VIDEO_OUTPUT_MODES = ("application/json", "video/mp4")
TERMINAL_TASK_STATES = frozenset(
    {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}
)


def bearer_security() -> tuple[dict, list[dict]]:
    """Return the public scheme declaration without any credential material."""
    return (
        {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}},
        [{"schemes": {"bearer": {"list": []}}}],
    )


__all__ = [
    "A2A_PATH",
    "A2A_VERSION",
    "AGENT_CARD_PATH",
    "IMAGE_OUTPUT_MODES",
    "INPUT_MODES",
    "JSONRPC_METHODS",
    "TERMINAL_TASK_STATES",
    "VIDEO_OUTPUT_MODES",
    "bearer_security",
]
