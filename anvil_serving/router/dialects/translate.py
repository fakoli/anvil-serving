"""Cross-dialect wire translation for tool-carrying requests.

The relay backends (:class:`~anvil_serving.router.backends.cloud.CloudBackend` /
:class:`~anvil_serving.router.backends.relay.RelayBackend`) rebuild the upstream
body from the normalized :class:`~anvil_serving.router.internal.InternalRequest`,
whose messages are FLATTENED to plain text. That is lossless for chat, but a
coding harness's agent loop rides on structure the flattening drops:

* the ``tools`` / ``tool_choice`` request fields (the model can never call a
  tool it was never offered), and
* the ``tool_use`` / ``tool_result`` content blocks (Anthropic) or
  ``assistant.tool_calls`` / ``role:"tool"`` messages (OpenAI) that carry the
  agent's own history between turns.

This module restores that fidelity. It is deliberately pure + stdlib-only:

* :func:`has_tool_artifacts` — cheap detector: does the raw wire body carry any
  tool structure at all? When it returns ``False`` the caller keeps the
  existing flattened-text body byte-identical (regression safety).
* :func:`anthropic_tools_to_openai` / :func:`openai_tools_to_anthropic` — tool
  *definition* translation.
* :func:`anthropic_tool_choice_to_openai` / :func:`openai_tool_choice_to_anthropic`
  — ``tool_choice`` translation.
* :func:`anthropic_messages_to_openai` / :func:`openai_messages_to_anthropic`
  — message-history translation that preserves tool calls and tool results.

Every function is defensive: malformed entries are skipped (never raised), so a
weird body degrades to the old flattened behaviour rather than a 500.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional

from ..internal import flatten_content


def _is_seq(v: Any) -> bool:
    return isinstance(v, (list, tuple))


def has_tool_artifacts(raw: Mapping[str, Any]) -> bool:
    """True when the raw wire body carries any tool structure worth preserving.

    Checks (cheap, in order): a non-empty ``tools`` array, a ``tool_choice``
    field, an OpenAI ``role:"tool"`` message or ``assistant.tool_calls``, or an
    Anthropic ``tool_use`` / ``tool_result`` content block.
    """
    if raw.get("tools"):
        return True
    if raw.get("tool_choice") is not None:
        return True
    messages = raw.get("messages")
    if not _is_seq(messages):
        return False
    for m in messages:
        if not isinstance(m, Mapping):
            continue
        if m.get("role") == "tool" or m.get("tool_calls"):
            return True
        content = m.get("content")
        if _is_seq(content):
            for block in content:
                if isinstance(block, Mapping) and block.get("type") in (
                    "tool_use", "tool_result",
                ):
                    return True
    return False


# --------------------------------------------------------------------------- #
# tool definitions
# --------------------------------------------------------------------------- #
def anthropic_tools_to_openai(tools: Any) -> List[Dict[str, Any]]:
    """``[{name, description, input_schema}]`` -> OpenAI function-tool array.

    Anthropic *server* tools (``type`` other than a custom/function tool, e.g.
    ``web_search_20250305``) have no OpenAI equivalent and are skipped.
    """
    out: List[Dict[str, Any]] = []
    if not _is_seq(tools):
        return out
    for t in tools:
        if not isinstance(t, Mapping) or not t.get("name"):
            continue
        ttype = t.get("type")
        if ttype not in (None, "custom", "tool"):
            continue  # server-side tool: not translatable
        fn: Dict[str, Any] = {
            "name": str(t["name"]),
            "parameters": t.get("input_schema") or {"type": "object"},
        }
        if t.get("description"):
            fn["description"] = str(t["description"])
        out.append({"type": "function", "function": fn})
    return out


def openai_tools_to_anthropic(tools: Any) -> List[Dict[str, Any]]:
    """OpenAI function-tool array -> ``[{name, description, input_schema}]``."""
    out: List[Dict[str, Any]] = []
    if not _is_seq(tools):
        return out
    for t in tools:
        if not isinstance(t, Mapping):
            continue
        fn = t.get("function")
        if not isinstance(fn, Mapping) or not fn.get("name"):
            continue
        entry: Dict[str, Any] = {
            "name": str(fn["name"]),
            "input_schema": fn.get("parameters") or {"type": "object"},
        }
        if fn.get("description"):
            entry["description"] = str(fn["description"])
        out.append(entry)
    return out


# --------------------------------------------------------------------------- #
# tool_choice
# --------------------------------------------------------------------------- #
def anthropic_tool_choice_to_openai(choice: Any) -> Optional[Any]:
    """Anthropic ``tool_choice`` object -> OpenAI form (``None`` = omit)."""
    if not isinstance(choice, Mapping):
        return None
    ctype = choice.get("type")
    if ctype == "auto":
        return "auto"
    if ctype == "any":
        return "required"
    if ctype == "none":
        return "none"
    if ctype == "tool" and choice.get("name"):
        return {"type": "function", "function": {"name": str(choice["name"])}}
    return None


def openai_tool_choice_to_anthropic(choice: Any) -> Optional[Dict[str, Any]]:
    """OpenAI ``tool_choice`` -> Anthropic object form (``None`` = omit)."""
    if isinstance(choice, str):
        return {
            "auto": {"type": "auto"},
            "required": {"type": "any"},
            "none": {"type": "none"},
        }.get(choice)
    if isinstance(choice, Mapping):
        fn = choice.get("function")
        if isinstance(fn, Mapping) and fn.get("name"):
            return {"type": "tool", "name": str(fn["name"])}
    return None


# --------------------------------------------------------------------------- #
# message history
# --------------------------------------------------------------------------- #
def _parse_arguments(args: Any) -> Dict[str, Any]:
    """Best-effort dict from an OpenAI ``function.arguments`` JSON string."""
    if isinstance(args, Mapping):
        return dict(args)
    if isinstance(args, str) and args.strip():
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def anthropic_messages_to_openai(messages: Any) -> List[Dict[str, Any]]:
    """Anthropic message array -> OpenAI messages, preserving tool traffic.

    * assistant ``tool_use`` blocks -> ``assistant.tool_calls`` entries
      (``input`` dict serialized to the OpenAI JSON-string ``arguments``);
    * user ``tool_result`` blocks -> one ``role:"tool"`` message each
      (``tool_use_id`` -> ``tool_call_id``; block content flattened to text),
      emitted BEFORE any user text from the same wire message so they directly
      follow the assistant turn that issued the calls;
    * text blocks / string content -> flattened text, as before.
    """
    out: List[Dict[str, Any]] = []
    if not _is_seq(messages):
        return out
    for m in messages:
        if not isinstance(m, Mapping):
            continue
        role = str(m.get("role") or "user")
        content = m.get("content")

        if not _is_seq(content):
            out.append({"role": role, "content": flatten_content(content)})
            continue

        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        tool_results: List[Dict[str, Any]] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
                continue
            if not isinstance(block, Mapping):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                tool_calls.append({
                    "id": str(block.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name") or ""),
                        "arguments": json.dumps(
                            block.get("input")
                            if isinstance(block.get("input"), (dict, list))
                            else {}
                        ),
                    },
                })
            elif btype == "tool_result":
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": str(block.get("tool_use_id") or ""),
                    "content": flatten_content(block.get("content")),
                })
            elif "text" in block:
                text_parts.append(str(block.get("text") or ""))

        # tool results first: they answer the PREVIOUS assistant tool_calls turn.
        out.extend(tool_results)
        text = "".join(text_parts)
        if role == "assistant" and tool_calls:
            msg: Dict[str, Any] = {"role": "assistant", "content": text or None}
            msg["tool_calls"] = tool_calls
            out.append(msg)
        elif text or not tool_results:
            out.append({"role": role, "content": text})
    return out


def openai_messages_to_anthropic(messages: Any) -> List[Dict[str, Any]]:
    """OpenAI message array -> Anthropic messages, preserving tool traffic.

    * ``assistant.tool_calls`` -> assistant ``tool_use`` content blocks
      (``arguments`` parsed back to a dict, best-effort);
    * ``role:"tool"`` messages -> user ``tool_result`` content blocks;
    * ``role:"system"`` messages are DROPPED here (the caller carries the
      system prompt on the top-level ``system`` field);
    * plain content -> flattened text.

    Consecutive ``role:"tool"`` messages are merged into ONE user message with
    multiple ``tool_result`` blocks (the Anthropic API requires strict
    user/assistant alternation).
    """
    out: List[Dict[str, Any]] = []
    if not _is_seq(messages):
        return out

    pending_results: List[Dict[str, Any]] = []

    def _flush_results() -> None:
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for m in messages:
        if not isinstance(m, Mapping):
            continue
        role = str(m.get("role") or "user")
        if role == "system":
            continue
        if role == "tool":
            pending_results.append({
                "type": "tool_result",
                "tool_use_id": str(m.get("tool_call_id") or ""),
                "content": flatten_content(m.get("content")),
            })
            continue
        _flush_results()

        raw_tc = m.get("tool_calls")
        if role == "assistant" and _is_seq(raw_tc) and raw_tc:
            blocks: List[Dict[str, Any]] = []
            text = flatten_content(m.get("content"))
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in raw_tc:
                if not isinstance(tc, Mapping):
                    continue
                fn = tc.get("function")
                fn = fn if isinstance(fn, Mapping) else {}
                blocks.append({
                    "type": "tool_use",
                    "id": str(tc.get("id") or ""),
                    "name": str(fn.get("name") or ""),
                    "input": _parse_arguments(fn.get("arguments")),
                })
            out.append({"role": "assistant", "content": blocks})
            continue

        out.append({"role": role, "content": flatten_content(m.get("content"))})

    _flush_results()
    return out
