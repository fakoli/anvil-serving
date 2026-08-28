"""A2A 1.0 projections for Anvil's durable media operations."""

from .agent_card import build_agent_card
from .protocol import A2A_VERSION, AGENT_CARD_PATH, A2A_PATH

__all__ = ["A2A_PATH", "A2A_VERSION", "AGENT_CARD_PATH", "build_agent_card"]
