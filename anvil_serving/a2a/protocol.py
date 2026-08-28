"""Frozen A2A 1.0 binding constants and media capability declarations."""

from __future__ import annotations


A2A_VERSION = "1.0"
A2A_LEGACY_DEFAULT_VERSION = "0.3"
A2A_VERSION_HEADER = "A2A-Version"
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
MEDIA_TO_TASK_STATE = {
    "accepted": "TASK_STATE_SUBMITTED",
    "awaiting_approval": "TASK_STATE_INPUT_REQUIRED",
    "preparing": "TASK_STATE_WORKING",
    "submitting": "TASK_STATE_WORKING",
    "queued": "TASK_STATE_SUBMITTED",
    "running": "TASK_STATE_WORKING",
    "completed": "TASK_STATE_COMPLETED",
    "failed": "TASK_STATE_FAILED",
    "canceled": "TASK_STATE_CANCELED",
}


def bearer_security() -> tuple[dict, list[dict]]:
    """Return the public scheme declaration without any credential material."""
    return (
        {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}},
        [{"schemes": {"bearer": {"list": []}}}],
    )


__all__ = [
    "A2A_PATH",
    "A2A_LEGACY_DEFAULT_VERSION",
    "A2A_VERSION",
    "A2A_VERSION_HEADER",
    "AGENT_CARD_PATH",
    "IMAGE_OUTPUT_MODES",
    "INPUT_MODES",
    "JSONRPC_METHODS",
    "MEDIA_TO_TASK_STATE",
    "TERMINAL_TASK_STATES",
    "VIDEO_OUTPUT_MODES",
    "bearer_security",
]
