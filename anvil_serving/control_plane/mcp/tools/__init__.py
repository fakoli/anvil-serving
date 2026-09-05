"""Explicit MCP tool-family composition."""

from __future__ import annotations

from ..catalog import build_family_catalog
from .benchmarks import FAMILY as BENCHMARKS
from .external_benchmarks import FAMILY as EXTERNAL_BENCHMARKS
from .host import FAMILY as HOST
from .media_worker import FAMILY as MEDIA_WORKER
from .media import FAMILY as MEDIA
from .models import FAMILY as MODELS
from .openclaw import FAMILY as OPENCLAW
from .operations import build_family as build_operations_family
from .router import FAMILY as ROUTER
from .services import FAMILY as SERVICES
from .serves import FAMILY as SERVES
from .voice import FAMILY as VOICE
from .workflow import FAMILY as WORKFLOW


OPERATIONS = build_operations_family(lambda: TOOLS)

TOOL_FAMILIES = (
    OPERATIONS,
    ROUTER,
    SERVES,
    MEDIA_WORKER,
    MEDIA,
    VOICE,
    HOST,
    SERVICES,
    MODELS,
    OPENCLAW,
    BENCHMARKS,
    WORKFLOW,
    EXTERNAL_BENCHMARKS,
)

TOOLS = build_family_catalog(TOOL_FAMILIES)
