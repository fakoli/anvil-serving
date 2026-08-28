"""Secret-free A2A 1.0 Agent Card for explicitly available media skills."""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterable
from typing import Any

from ..media.contracts import WorkflowDescriptor
from ..media.errors import MediaError
from .protocol import (
    A2A_PATH,
    A2A_VERSION,
    IMAGE_OUTPUT_MODES,
    INPUT_MODES,
    VIDEO_OUTPUT_MODES,
    bearer_security,
)


def _endpoint(public_origin: str) -> str:
    parsed = urllib.parse.urlsplit(public_origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise MediaError("invalid_public_origin", "A2A public origin is invalid")
    path = parsed.path
    if path not in {"", "/"}:
        raise MediaError("invalid_public_origin", "A2A public origin must not contain a path")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, A2A_PATH, "", ""))


def _skill(
    skill_id: str,
    name: str,
    description: str,
    *,
    workflows: list[WorkflowDescriptor],
    output_modes: tuple[str, ...],
) -> dict[str, Any]:
    identities = [f"{workflow.id}@{workflow.version}" for workflow in workflows]
    return {
        "id": skill_id,
        "name": name,
        "description": description,
        "tags": ["media", "named-workflow", *sorted({workflow.kind for workflow in workflows})],
        "examples": [f"Run named workflow {identity}" for identity in identities[:3]],
        "inputModes": list(INPUT_MODES),
        "outputModes": list(output_modes),
    }


def build_agent_card(
    workflows: Iterable[WorkflowDescriptor],
    *,
    public_origin: str,
    server_version: str,
) -> dict[str, Any]:
    """Build a deterministic public card from available workflow declarations."""
    available = sorted(
        (workflow for workflow in workflows if workflow.available),
        key=lambda workflow: (workflow.kind, workflow.id, workflow.version),
    )
    images = [workflow for workflow in available if workflow.kind == "image"]
    videos = [workflow for workflow in available if workflow.kind == "video"]
    skills: list[dict[str, Any]] = []
    if images:
        skills.append(
            _skill(
                "anvil.media.image.generate",
                "Named image generation",
                "Generate an image through an explicitly configured and qualified workflow.",
                workflows=images,
                output_modes=IMAGE_OUTPUT_MODES,
            )
        )
    if videos:
        skills.append(
            _skill(
                "anvil.media.video.generate",
                "Named video generation",
                "Generate a video through an explicitly configured and qualified workflow.",
                workflows=videos,
                output_modes=VIDEO_OUTPUT_MODES,
            )
        )
    if available:
        outputs = tuple(
            sorted({"application/json", *(mime for workflow in available for mime in workflow.output_mime_types)})
        )
        skills.append(
            _skill(
                "anvil.media.workflow.run",
                "Named media workflow",
                "Run one reviewed named workflow; raw workflow graphs are not accepted.",
                workflows=available,
                output_modes=outputs,
            )
        )
    schemes, requirements = bearer_security()
    output_modes = tuple(
        sorted({"application/json", *(mime for workflow in available for mime in workflow.output_mime_types)})
    )
    return {
        "name": "Anvil Media Gateway",
        "description": "Authenticated generation through explicit, bounded, durable media workflows.",
        "supportedInterfaces": [
            {
                "url": _endpoint(public_origin),
                "protocolBinding": "JSONRPC",
                "protocolVersion": A2A_VERSION,
            }
        ],
        "version": server_version,
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "securitySchemes": schemes,
        "securityRequirements": requirements,
        "defaultInputModes": list(INPUT_MODES),
        "defaultOutputModes": list(output_modes),
        "skills": skills,
    }


__all__ = ["build_agent_card"]
