"""Request construction, transport, and response normalization."""

import json
import time
import urllib.request

from ..preflight import (
    resolve_api_key as resolve_api_key,
    response_observation as response_observation,
)

FILLER = "def helper_%d():\n    return compute(%d)  # routine specialist context\n"
CHARS_PER_TOKEN = 3.0
DEFAULT_CTX_MARGIN = 1024


def ctx_cap(max_model_len, max_tokens, margin=DEFAULT_CTX_MARGIN):
    """Return the usable prompt budget beneath the serve context limit."""
    if not max_model_len:
        return None
    residual = int(max_model_len) - int(max_tokens) - int(margin)
    if residual <= 0:
        raise ValueError(
            "max_model_len must exceed max_tokens plus the context margin"
        )
    return residual


def clamp_ctx(ctx, cap):
    """Clamp a sampled or fixed target to the usable prompt budget."""
    return ctx if cap is None else min(ctx, cap)


def _fill_lines(char_budget, *, offset=0):
    """Repeat FILLER (offset by index) until the joined text reaches char_budget."""
    lines = []
    filled = 0
    index = 0
    while filled < char_budget:
        line = FILLER % (offset + index, index)
        lines.append(line)
        filled += len(line)
        index += 1
    return "".join(lines)


def make_prompt(shared_prefix, ctx_tokens, uniq, max_prompt_tokens=None,
                chars_per_token=CHARS_PER_TOKEN):
    """Build a calibrated prompt without exceeding the requested token budget."""
    budget = ctx_tokens if max_prompt_tokens is None else min(ctx_tokens, max_prompt_tokens)
    tail = f"\n# request {uniq}: summarize the above in one line."
    if int(budget * chars_per_token) <= len(tail):
        raise ValueError("context target is too small for the benchmark prompt envelope")
    char_budget = int(budget * chars_per_token) - len(shared_prefix) - len(tail) - 1
    if char_budget <= 0:
        return shared_prefix[: max(0, int(budget * chars_per_token) - len(tail))] + tail
    filler = _fill_lines(char_budget, offset=uniq)[:max(0, char_budget)]
    return shared_prefix + "\n" + filler + tail


def make_shared_prefix(target_tokens, *, chars_per_token=CHARS_PER_TOKEN):
    """Build a stable shared prefix within its declared estimated-token budget."""
    if target_tokens <= 0:
        return ""
    char_budget = int(target_tokens * chars_per_token)
    return _fill_lines(char_budget)[:char_budget]


def build_body(model, prompt, max_tokens, chat_template_kwargs=None, reasoning_effort=None):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0.0, "stream": True,
            "stream_options": {"include_usage": True}}
    if chat_template_kwargs:
        body["chat_template_kwargs"] = chat_template_kwargs
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    return body


def _choice_messages(response):
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(choices, list):
        return []
    messages = []
    for choice in choices:
        if isinstance(choice, dict) and isinstance(choice.get("message"), dict):
            messages.append(choice["message"])
    return messages


def validate_function_tool_call(message, expected_name, required_args):
    """Return a schema/usefulness result for one OpenAI-compatible tool call."""
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not tool_calls:
        return {
            "valid": False,
            "error": "response did not include tool_calls",
            "arguments": None,
        }

    first = tool_calls[0] if isinstance(tool_calls[0], dict) else {}
    function = first.get("function") if isinstance(first, dict) else {}
    if not isinstance(function, dict):
        return {"valid": False, "error": "tool_call missing function object", "arguments": None}
    if function.get("name") != expected_name:
        return {
            "valid": False,
            "error": "wrong function name: %r" % function.get("name"),
            "arguments": None,
        }

    raw_args = function.get("arguments")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except Exception as exc:
        return {"valid": False, "error": "arguments are not valid JSON: %s" % exc, "arguments": None}
    if not isinstance(args, dict):
        return {"valid": False, "error": "arguments are not a JSON object", "arguments": None}

    for key, expected_value in required_args.items():
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            return {
                "valid": False,
                "error": "missing required string argument: %s" % key,
                "arguments": args,
            }
        if expected_value is not None and value.strip() != expected_value:
            return {
                "valid": False,
                "error": "wrong argument %s: %r" % (key, value),
                "arguments": args,
            }

    return {"valid": True, "error": None, "arguments": args}


def post_chat(base, model, key, messages, max_tokens=128, timeout=120,
              tools=None, chat_template_kwargs=None, reasoning_effort=None):
    """Non-streaming OpenAI-compatible chat call for smoke/tool probes."""
    url = base.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if chat_template_kwargs:
        body["chat_template_kwargs"] = chat_template_kwargs
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read())
    return {"latency_s": time.perf_counter() - t0, "response": data}


def detect_max_model_len(base, model=None, key=None, timeout=15):
    """Best-effort probe of ``<base>/models`` for the serve's context window."""
    url = base.rstrip("/") + "/models"
    headers = {}
    if key:
        headers["Authorization"] = "Bearer " + key
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read())
    except Exception:
        return None
    entries = data.get("data") if isinstance(data, dict) else None
    if not entries:
        return None
    chosen = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if model and entry.get("id") == model:
            chosen = entry
            break
        if chosen is None:
            chosen = entry
    if not isinstance(chosen, dict):
        return None
    for key_name in ("max_model_len", "max_context_length", "context_length"):
        value = chosen.get(key_name)
        if isinstance(value, int) and value > 0:
            return value
    return None


def stream_chat(base, model, prompt, key, max_tokens, timeout=900,
                chat_template_kwargs=None, reasoning_effort=None):
    url = base.rstrip("/") + "/chat/completions"
    body = build_body(
        model, prompt, max_tokens, chat_template_kwargs, reasoning_effort
    )
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t0 = time.perf_counter()
    time_to_first_output = None
    ttft = None
    content_chunks = 0
    reasoning_chunks = 0
    usage = None
    with urllib.request.urlopen(req, timeout=timeout) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except Exception:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    if time_to_first_output is None:
                        time_to_first_output = time.perf_counter() - t0
                    reasoning_chunks += 1
                if delta.get("content"):
                    if time_to_first_output is None:
                        time_to_first_output = time.perf_counter() - t0
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    content_chunks += 1
    e2e = time.perf_counter() - t0
    completion_tokens = (
        usage.get("completion_tokens") if isinstance(usage, dict) else None
    )
    has_usage_tokens = (
        isinstance(completion_tokens, int)
        and not isinstance(completion_tokens, bool)
        and completion_tokens >= 0
    )
    return {
        "time_to_first_output": time_to_first_output,
        "ttft": ttft,
        "e2e": e2e,
        "out_toks": completion_tokens if has_usage_tokens else content_chunks,
        "output_token_source": "usage" if has_usage_tokens else "content_chunks",
        "content_chunks": content_chunks,
        "reasoning_chunks": reasoning_chunks,
        "usage": usage,
    }


def validate_stream_result(result):
    """Require a completed capacity/context response to contain visible output."""
    if not isinstance(result, dict) or result.get("ttft") is None:
        raise ValueError("stream completed without visible content")
    return result
