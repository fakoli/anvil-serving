#!/usr/bin/env python
"""Bounded correctness preflight for a local OpenAI-compatible model endpoint.

Validate an SGLang/vLLM serve on Blackwell sm_120 BEFORE trusting throughput.
Tests: (1) long-context needle retrieval, (2) shared-prefix tool-calling batch
(detects sm_120 garbage / spec-decode tool corruption), (3) structured JSON,
(4) short coding smoke. Stdlib only (urllib).

Usage:
  anvil-serving eval preflight --base-url http://127.0.0.1:30000/v1 \
    --model coder-specialist --needle-ctx 128000 --confirm
Exit code 0 = all pass, 1 = any fail.
"""
import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from .model_controls import REASONING_EFFORT_CHOICES, validate_reasoning_control
except ImportError:  # direct ``python anvil_serving/preflight.py`` compatibility
    from model_controls import REASONING_EFFORT_CHOICES, validate_reasoning_control


def _console_safe(value):
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(value).encode(encoding, errors="backslashreplace").decode(encoding)


def _atomic_write_json(path, value):
    out = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(out) or os.getcwd()
    if not os.path.isdir(parent):
        raise OSError("output directory does not exist: %s" % parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=parent,
                prefix=".%s." % os.path.basename(out), suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            json.dump(value, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, out)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _validate_output_path(path):
    """Fail before live probes when an output target cannot be replaced safely."""
    if not path or path == "-":
        return None
    out = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(out) or os.getcwd()
    if not os.path.isdir(parent):
        raise OSError("output directory does not exist: %s" % parent)
    if os.path.islink(out):
        raise OSError("output path cannot be a symbolic link: %s" % out)
    if os.path.exists(out) and not os.path.isfile(out):
        raise OSError("output path is not a regular file: %s" % out)
    if not os.access(parent, os.W_OK):
        raise OSError("output directory is not writable: %s" % parent)
    return out

def chat(base, model, messages, key=None, max_tokens=256, temperature=0.0,
         tools=None, tool_choice=None, timeout=900, chat_template_kwargs=None,
         reasoning_effort=None, mm_processor_kwargs=None):
    url = base.rstrip("/") + "/chat/completions"
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if tools: body["tools"] = tools
    if tool_choice: body["tool_choice"] = tool_choice
    # chat_template_kwargs (e.g. {"enable_thinking": False}) is honored by SGLang/vLLM
    # for Qwen3.x / GLM so reasoning models don't burn the token budget on hidden
    # thinking and return empty content. NOTE: gpt-oss ignores this kwarg (it gates
    # reasoning via "reasoning effort", not the chat template) -> needs adequate tokens.
    if chat_template_kwargs: body["chat_template_kwargs"] = chat_template_kwargs
    if reasoning_effort is not None: body["reasoning_effort"] = reasoning_effort
    if mm_processor_kwargs: body["mm_processor_kwargs"] = mm_processor_kwargs
    headers = {"Content-Type": "application/json"}
    if key: headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()), time.time() - t0
    except urllib.error.HTTPError as exc:
        raw = exc.read(4097)
        bounded = raw[:4096].decode("utf-8", errors="replace").strip()
        if len(raw) > 4096:
            bounded += "...[truncated]"
        detail = bounded or exc.reason or "no response body"
        raise RuntimeError("HTTP %s: %s" % (exc.code, detail)) from exc


def responses_request(base, model, prompt, key=None, max_tokens=256,
                      temperature=0.0, chat_template_kwargs=None, timeout=900):
    """Send the bounded stateless Responses API subset used by preflight."""
    url = base.rstrip("/") + "/responses"
    body = {
        "model": model,
        "input": prompt,
        "max_output_tokens": max_tokens,
        "temperature": temperature,
    }
    if chat_template_kwargs:
        body["chat_template_kwargs"] = chat_template_kwargs
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read()), time.time() - started
    except urllib.error.HTTPError as exc:
        raw = exc.read(4097)
        bounded = raw[:4096].decode("utf-8", errors="replace").strip()
        if len(raw) > 4096:
            bounded += "...[truncated]"
        detail = bounded or exc.reason or "no response body"
        raise RuntimeError("HTTP %s: %s" % (exc.code, detail)) from exc


def chat_stream(base, model, messages, key=None, max_tokens=256, tools=None,
                tool_choice=None, chat_template_kwargs=None, timeout=900):
    """Read a bounded Chat Completions SSE stream and retain parsed events."""
    url = base.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        body["tools"] = tools
    if tool_choice:
        body["tool_choice"] = tool_choice
    if chat_template_kwargs:
        body["chat_template_kwargs"] = chat_template_kwargs
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers
    )
    started = time.time()
    events = []
    done = False
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    done = True
                    break
                if payload:
                    events.append(json.loads(payload))
                if len(events) > 10000:
                    raise RuntimeError("SSE event limit exceeded")
    except urllib.error.HTTPError as exc:
        raw = exc.read(4097)
        bounded = raw[:4096].decode("utf-8", errors="replace").strip()
        detail = bounded or exc.reason or "no response body"
        raise RuntimeError("HTTP %s: %s" % (exc.code, detail)) from exc
    return events, done, time.time() - started


def load_image_data(path):
    """Return a bounded image data URL plus non-sensitive input identity."""
    if not path:
        raise ValueError("multimodal checks require --image-path")
    image_path = os.path.abspath(os.path.expanduser(path))
    if os.path.islink(image_path):
        raise ValueError("image path cannot be a symbolic link: %s" % image_path)
    if not os.path.isfile(image_path):
        raise ValueError("image path is not a regular file: %s" % image_path)
    size = os.path.getsize(image_path)
    if not 0 < size <= 10 * 1024 * 1024:
        raise ValueError("image must be from 1 byte through 10 MiB")
    mime, _encoding = mimetypes.guess_type(image_path)
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("image must be PNG, JPEG, or WebP")
    with open(image_path, "rb") as handle:
        raw = handle.read(10 * 1024 * 1024 + 1)
    if len(raw) != size:
        raise ValueError("image changed while it was being read")
    identity = {
        "bytes": size,
        "mime": mime,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    encoded = base64.b64encode(raw).decode("ascii")
    return "data:%s;base64,%s" % (mime, encoded), identity


def load_video_data(path):
    """Return a bounded video data URL plus non-sensitive input identity."""
    if not path:
        raise ValueError("video check requires --video-path")
    video_path = os.path.abspath(os.path.expanduser(path))
    if os.path.islink(video_path):
        raise ValueError("video path cannot be a symbolic link: %s" % video_path)
    if not os.path.isfile(video_path):
        raise ValueError("video path is not a regular file: %s" % video_path)
    size = os.path.getsize(video_path)
    max_bytes = 128 * 1024 * 1024
    if not 0 < size <= max_bytes:
        raise ValueError("video must be from 1 byte through 128 MiB")
    mime, _encoding = mimetypes.guess_type(video_path)
    if mime not in {"video/mp4", "video/webm", "video/quicktime"}:
        raise ValueError("video must be MP4, WebM, or QuickTime")
    with open(video_path, "rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) != size:
        raise ValueError("video changed while it was being read")
    identity = {
        "bytes": size,
        "mime": mime,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    encoded = base64.b64encode(raw).decode("ascii")
    return "data:%s;base64,%s" % (mime, encoded), identity


def response_observation(response):
    """Retain the evidence needed to distinguish bad output from budget starvation."""
    choices = response.get("choices") if isinstance(response, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else {}
    choice = choice if isinstance(choice, dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), str) else ""
    reasoning_field = None
    reasoning = ""
    for field in ("reasoning", "reasoning_content"):
        value = message.get(field)
        if isinstance(value, str):
            reasoning_field = field
            if value:
                reasoning = value
                break
    usage = response.get("usage") if isinstance(response, dict) else None
    details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
    return {
        "content": content,
        "finish_reason": choice.get("finish_reason"),
        "content_chars": len(content),
        "content_excerpt": content[:200],
        "reasoning_field": reasoning_field,
        "reasoning_chars": len(reasoning),
        "reasoning_excerpt": reasoning[:200],
        "reasoning_tokens": details.get("reasoning_tokens") if isinstance(details, dict) else None,
        "usage": usage,
    }

def _capture(evidence, test, response, seconds, request_index=None):
    observation = response_observation(response)
    observation.update({"test": test, "seconds": round(seconds, 3)})
    if request_index is not None:
        observation["request_index"] = request_index
    if evidence is not None:
        evidence.append(observation)
    return observation

def _evidence_note(observation):
    return "finish=%r visible=%s reasoning_chars=%s reasoning_tokens=%r" % (
        observation["finish_reason"], observation["content_chars"],
        observation["reasoning_chars"], observation["reasoning_tokens"],
    )

def t_needle(base, model, key, ctx_tokens, ctk=None, max_tokens=256,
             reasoning_effort=None, evidence=None, timeout=900):
    secret = "ZEBRA-42917-QUARTZ"
    line = "The engineers continued routine checks while the telemetry stayed nominal. "
    words = int(ctx_tokens * 0.75)
    reps = max(1, words // max(1, len(line.split())))
    body = line * reps
    cut = int(len(body) * 0.58)
    doc = body[:cut] + f"\n\nIMPORTANT: The launch code is {secret}.\n\n" + body[cut:]
    msgs = [{"role": "user", "content": doc + "\n\nQuestion: What is the launch code? Reply with ONLY the code."}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=max_tokens,
                        chat_template_kwargs=ctk, reasoning_effort=reasoning_effort,
                        timeout=timeout)
        obs = _capture(evidence, "needle", resp, dt)
        out = resp["choices"][0]["message"].get("content") or ""
        ok = secret.replace("-", "") in out.replace("-", "").replace(" ", "")
        obs.update({"passed": ok, "validation_detail": "needle present" if ok else "needle missing"})
        return ok, f"{dt:.1f}s ctx~{ctx_tokens} got={out.strip()[:50]!r} {_evidence_note(obs)}"
    except Exception as e:
        return False, f"error: {e}"

TOOLS = [{"type": "function", "function": {
    "name": "get_weather",
    "description": "Get current weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]

def validate_tool_call(message, expected_name="get_weather", required_args=None):
    """Validate that a chat response produced a usable OpenAI-style tool call."""
    required_args = required_args or ["city"]
    content = message.get("content") or ""
    if any(g in content for g in ("<<tool", "<|", "function=", "�")):
        return False, f"garbage content: {content[:60]!r}"

    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        return False, "response did not include tool_calls"

    first = tool_calls[0] or {}
    function = first.get("function") or {}
    if function.get("name") != expected_name:
        return False, f"wrong function name: {function.get('name')!r}"

    raw_args = function.get("arguments")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except Exception as exc:
        return False, f"arguments are not valid JSON: {exc}"
    if not isinstance(args, dict):
        return False, "arguments are not a JSON object"

    missing = []
    for arg in required_args:
        value = args.get(arg)
        if not isinstance(value, str) or not value.strip():
            missing.append(arg)
    if missing:
        return False, "missing required string argument(s): " + ", ".join(missing)

    shown = ", ".join(f"{arg}={args[arg]!r}" for arg in required_args)
    return True, f"valid tool_call {expected_name}({shown})"

def t_tool_one(base, model, key, shared_prefix, ctk=None, max_tokens=256,
               reasoning_effort=None, evidence=None, request_index=None, timeout=900):
    msgs = [{"role": "system", "content": shared_prefix},
            {"role": "user", "content": "What's the weather in Oakland? Use the tool."}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=max_tokens, tools=TOOLS,
                        tool_choice="auto", chat_template_kwargs=ctk,
                        reasoning_effort=reasoning_effort, timeout=timeout)
        obs = _capture(evidence, "tools", resp, dt, request_index)
        m = resp["choices"][0]["message"]
        ok, detail = validate_tool_call(m)
        obs.update({"passed": ok, "validation_detail": detail})
        return ok, f"{dt:.1f}s {detail} {_evidence_note(obs)}"
    except Exception as e:
        return False, f"error: {e}"

def t_tool_batch(base, model, key, n, ctk=None, max_tokens=256,
                 reasoning_effort=None, evidence=None, timeout=900):
    # big stable shared prefix to exercise prefix cache + reproduce fan-out
    shared = ("You are a coding specialist agent. Follow the harness contract.\n" * 400)
    oks = []
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(t_tool_one, base, model, key, shared, ctk, max_tokens,
                          reasoning_effort, evidence, index, timeout) for index in range(n)]
        details = []
        for f in as_completed(futs):
            ok, d = f.result(); oks.append(ok); details.append(d)
    passed = sum(oks)
    return passed == n, f"{passed}/{n} clean (sample: {details[0] if details else 'n/a'})"


def t_streaming_tool(base, model, key, ctk=None, max_tokens=256,
                     evidence=None, timeout=900):
    """Require one valid tool call reconstructed from an SSE stream."""
    messages = [{
        "role": "user",
        "content": "What's the weather in Oakland? Use the tool.",
    }]
    try:
        events, done, seconds = chat_stream(
            base, model, messages, key, max_tokens=max_tokens, tools=TOOLS,
            tool_choice="auto", chat_template_kwargs=ctk, timeout=timeout,
        )
        calls = {}
        finish_reason = None
        usage = None
        reasoning = ""
        for event in events:
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
            choices = event.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0] if isinstance(choices[0], dict) else {}
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            for field in ("reasoning", "reasoning_content"):
                if isinstance(delta.get(field), str):
                    reasoning += delta[field]
            for raw_call in delta.get("tool_calls") or []:
                if not isinstance(raw_call, dict):
                    continue
                index = raw_call.get("index", 0)
                call = calls.setdefault(index, {
                    "id": "", "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
                call["id"] += raw_call.get("id") or ""
                function = raw_call.get("function") or {}
                call["function"]["name"] += function.get("name") or ""
                call["function"]["arguments"] += function.get("arguments") or ""
        message = {"content": "", "tool_calls": [calls[key] for key in sorted(calls)]}
        valid, detail = validate_tool_call(message)
        passed = done and valid and finish_reason == "tool_calls"
        if evidence is not None:
            evidence.append({
                "test": "streaming-tools",
                "seconds": round(seconds, 3),
                "finish_reason": finish_reason,
                "content_chars": 0,
                "content_excerpt": "",
                "reasoning_field": "stream_delta" if reasoning else None,
                "reasoning_chars": len(reasoning),
                "reasoning_excerpt": reasoning[:200],
                "reasoning_tokens": None,
                "usage": usage,
                "sse_done": done,
                "event_count": len(events),
                "passed": passed,
                "validation_detail": detail,
            })
        return passed, (
            f"{seconds:.1f}s done={done} events={len(events)} {detail} "
            f"finish={finish_reason!r}"
        )
    except Exception as exc:
        return False, "error: %s" % exc


def t_tool_result(base, model, key, ctk=None, max_tokens=256,
                  reasoning_effort=None, evidence=None, timeout=900):
    """Complete a tool-call, tool-result, final-answer exchange."""
    messages = [{
        "role": "user",
        "content": "What's the weather in Oakland? Use the tool.",
    }]
    try:
        first, first_seconds = chat(
            base, model, messages, key, max_tokens=max_tokens, tools=TOOLS,
            tool_choice="auto", chat_template_kwargs=ctk,
            reasoning_effort=reasoning_effort, timeout=timeout,
        )
        first_obs = _capture(evidence, "tool-result-initial", first, first_seconds)
        assistant = first["choices"][0]["message"]
        valid, detail = validate_tool_call(assistant)
        first_obs.update({"passed": valid, "validation_detail": detail})
        if not valid:
            return False, detail
        tool_call = assistant["tool_calls"][0]
        messages.extend([
            {
                "role": "assistant",
                "content": assistant.get("content"),
                "tool_calls": assistant["tool_calls"],
            },
            {
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": '{"city":"Oakland","temperature_f":72,"condition":"sunny"}',
            },
            {
                "role": "user",
                "content": "Reply exactly OAKLAND 72F using the tool result.",
            },
        ])
        final, final_seconds = chat(
            base, model, messages, key, max_tokens=max_tokens,
            chat_template_kwargs=ctk, reasoning_effort=reasoning_effort,
            timeout=timeout,
        )
        final_obs = _capture(
            evidence, "tool-result-continuation", final, final_seconds
        )
        output = final_obs["content"].casefold().replace(" ", "")
        passed = "oakland" in output and "72f" in output
        final_obs.update({
            "passed": passed,
            "validation_detail": (
                "tool result retained" if passed else "tool result missing"
            ),
        })
        return passed, (
            f"{first_seconds + final_seconds:.1f}s "
            f"{'tool result retained' if passed else 'tool result missing'} "
            f"{_evidence_note(final_obs)}"
        )
    except Exception as exc:
        return False, "error: %s" % exc


def t_responses(base, model, key, ctk=None, max_tokens=256,
                evidence=None, timeout=900):
    """Require a visible completed answer from the stateless Responses subset."""
    try:
        response, seconds = responses_request(
            base, model, "Reply with exactly READY", key,
            max_tokens=max_tokens, chat_template_kwargs=ctk, timeout=timeout,
        )
        text_parts = []
        reasoning = ""
        for item in response.get("output") or []:
            if not isinstance(item, dict):
                continue
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
                if part.get("type") == "reasoning_text" and isinstance(part.get("text"), str):
                    reasoning += part["text"]
        output = "".join(text_parts).strip()
        status = response.get("status")
        passed = status == "completed" and output == "READY"
        if evidence is not None:
            evidence.append({
                "test": "responses",
                "seconds": round(seconds, 3),
                "finish_reason": "stop" if status == "completed" else status,
                "content_chars": len(output),
                "content_excerpt": output[:200],
                "reasoning_field": "reasoning_text" if reasoning else None,
                "reasoning_chars": len(reasoning),
                "reasoning_excerpt": reasoning[:200],
                "reasoning_tokens": None,
                "usage": response.get("usage"),
                "response_status": status,
                "passed": passed,
                "validation_detail": (
                    "completed exact READY" if passed
                    else "status=%r output=%r" % (status, output[:80])
                ),
            })
        return passed, (
            f"{seconds:.1f}s status={status!r} output={output[:80]!r} "
            f"reasoning_chars={len(reasoning)}"
        )
    except Exception as exc:
        return False, "error: %s" % exc


def t_json(base, model, key, ctk=None, max_tokens=256, reasoning_effort=None,
           evidence=None, timeout=900):
    msgs = [{"role": "user", "content": 'Return ONLY a JSON object: {"language":"python","ok":true}. No prose.'}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=max_tokens,
                        chat_template_kwargs=ctk, reasoning_effort=reasoning_effort,
                        timeout=timeout)
        obs = _capture(evidence, "json", resp, dt)
        out = (resp["choices"][0]["message"].get("content") or "").strip()
        s = out[out.find("{"): out.rfind("}") + 1]
        obj = json.loads(s)
        ok = "language" in obj
        obs.update({"passed": ok, "validation_detail": "parsed keys=%r" % list(obj)[:4]})
        return ok, f"parsed keys={list(obj)[:4]} {_evidence_note(obs)}"
    except Exception as e:
        return False, f"error: {e}"

def t_smoke(base, model, key, ctk=None, max_tokens=256, reasoning_effort=None,
            evidence=None, timeout=900):
    msgs = [{"role": "user", "content": "Write a Python one-liner that returns the sum of a list `xs`."}]
    try:
        resp, dt = chat(base, model, msgs, key, max_tokens=max_tokens,
                        chat_template_kwargs=ctk, reasoning_effort=reasoning_effort,
                        timeout=timeout)
        obs = _capture(evidence, "smoke", resp, dt)
        out = (resp["choices"][0]["message"].get("content") or "")
        ok = "sum(" in out
        obs.update({"passed": ok, "validation_detail": "contains sum(" if ok else "missing sum("})
        return ok, f"{dt:.1f}s got={out.strip()[:50]!r} {_evidence_note(obs)}"
    except Exception as e:
        return False, f"error: {e}"


def t_multimodal(base, model, key, data_url, image_identity, expectations, *,
                 check, ctk=None, max_tokens=256, reasoning_effort=None,
                 evidence=None, timeout=900):
    prompts = {
        "image": (
            "Inspect this image. Describe what it shows and report the most "
            "important labels, values, and status."
        ),
        "ocr": (
            "Transcribe all visible text in this image exactly. Preserve numbers "
            "and short labels. Return only the transcription."
        ),
    }
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": data_url}},
        {"type": "text", "text": prompts[check]},
    ]}]
    try:
        resp, dt = chat(
            base, model, messages, key, max_tokens=max_tokens,
            chat_template_kwargs=ctk, reasoning_effort=reasoning_effort,
            timeout=timeout,
        )
        obs = _capture(evidence, check, resp, dt)
        out = (resp["choices"][0]["message"].get("content") or "")
        folded = out.casefold()
        missing = [item for item in expectations if item.casefold() not in folded]
        ok = not missing
        obs.update({
            "passed": ok,
            "validation_detail": (
                "all expected text present"
                if ok else "missing expected text: %r" % missing
            ),
            "image": image_identity,
            "expected": list(expectations),
        })
        return ok, (
            f"{dt:.1f}s "
            f"{'all expected text present' if ok else 'missing=' + repr(missing)} "
            f"{_evidence_note(obs)}"
        )
    except Exception as e:
        return False, f"error: {e}"


def t_video(base, model, key, data_url, video_identity, expectations, *,
            ctk=None, max_tokens=256, reasoning_effort=None, evidence=None,
            timeout=900):
    """Run one bounded OpenAI ``video_url`` correctness probe."""
    messages = [{"role": "user", "content": [
        {"type": "video_url", "video_url": {"url": data_url}},
        {"type": "text", "text": (
            "Inspect the complete video. Describe the ordered events, visible "
            "state changes, and any readable labels or text."
        )},
    ]}]
    try:
        resp, dt = chat(
            base, model, messages, key, max_tokens=max_tokens,
            chat_template_kwargs=ctk, reasoning_effort=reasoning_effort,
            timeout=timeout,
        )
        obs = _capture(evidence, "video", resp, dt)
        out = (resp["choices"][0]["message"].get("content") or "")
        folded = out.casefold()
        missing = [item for item in expectations if item.casefold() not in folded]
        ok = not missing
        obs.update({
            "passed": ok,
            "validation_detail": (
                "all expected text present"
                if ok else "missing expected text: %r" % missing
            ),
            "video": video_identity,
            "expected": list(expectations),
        })
        return ok, (
            f"{dt:.1f}s "
            f"{'all expected text present' if ok else 'missing=' + repr(missing)} "
            f"{_evidence_note(obs)}"
        )
    except Exception as e:
        return False, f"error: {e}"


def resolve_api_key(api_key_env=None):
    """Resolve auth for probes from an environment variable reference."""
    if api_key_env:
        value = os.environ.get(api_key_env)
        if not value:
            raise ValueError("environment variable %s is not set" % api_key_env)
        return value
    return None

def main(argv=None, *, prog="anvil-serving eval preflight"):
    ap = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Run bounded correctness gates before trusting endpoint performance.\n\n"
            "Examples:\n"
            "  anvil-serving eval preflight --tier heavy --checks smoke,json --dry-run\n"
            "  anvil-serving eval preflight --tier heavy --checks smoke,json,needle,tools "
            "--output preflight.json --confirm\n"
            "  anvil-serving eval preflight --base-url http://127.0.0.1:30002/v1 "
            "--model MODEL --thinking-mode enabled --reasoning-headroom-tokens 4096 "
            "--reasoning-evidence required --confirm"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    endpoint = ap.add_argument_group("direct endpoint input")
    endpoint.add_argument("--base-url", help="OpenAI-compatible endpoint base URL")
    endpoint.add_argument("--model", help="served model id")
    manifest = ap.add_argument_group("serves manifest input")
    manifest.add_argument("--manifest", help="serves manifest TOML (used with --tier)")
    manifest.add_argument("--tier", help="serve name in the manifest; fills endpoint and model")
    recipe = ap.add_argument_group("serve recipe input")
    recipe.add_argument("--recipe", help="recorded recipe model selector")
    recipe.add_argument("--registry", help="serve-recipe registry used with --recipe")
    ap.add_argument("--api-key-env", default=None,
                    help="read the bearer token from this environment variable")
    ap.add_argument("--needle-ctx", type=int, default=128000)
    ap.add_argument("--tool-batch", type=int, default=20)
    ap.add_argument(
        "--checks", default="smoke,json,needle,tools",
        help=(
            "comma-separated checks: smoke,json,needle,tools,streaming-tools,"
            "tool-result,responses,image,ocr,video"
        ),
    )
    ap.add_argument("--image-path",
                    help="PNG, JPEG, or WebP input for image and OCR checks")
    ap.add_argument("--image-expect", action="append", default=[],
                    help="case-insensitive text required in the image-check response; repeatable")
    ap.add_argument("--ocr-expect", action="append", default=[],
                    help="case-insensitive text required in the OCR response; repeatable")
    ap.add_argument("--video-path",
                    help="MP4, WebM, or QuickTime input for the video check")
    ap.add_argument("--video-expect", action="append", default=[],
                    help="case-insensitive text required in the video response; repeatable")
    ap.add_argument("--thinking-mode", choices=("default", "enabled", "disabled", "unsupported"),
                    default="default", help="model-family thinking control to request")
    ap.add_argument("--reasoning-effort",
                    choices=REASONING_EFFORT_CHOICES,
                    help="top-level OpenAI reasoning_effort for model families that use it")
    ap.add_argument("--visible-answer-tokens", type=int, default=256,
                    help="visible-answer allocation recorded by the gate")
    ap.add_argument("--reasoning-headroom-tokens", type=int, default=0,
                    help="reasoning headroom added to the API completion cap")
    ap.add_argument("--json-out", "--output", dest="json_out",
                    help="write machine-readable gate evidence atomically")
    ap.add_argument("--timeout", "--timeout-seconds", dest="timeout", type=float,
                    default=900.0, help="per-request timeout, 1..3600 seconds")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate and print the plan; send no requests and write nothing")
    ap.add_argument("--reasoning-evidence", choices=("any", "required", "forbidden"),
                    default="any", help="assert effective reasoning-channel behavior")
    ap.add_argument("--allowed-finish-reasons", default="stop,tool_calls",
                    help="comma-separated finish reasons accepted by the gate")
    ap.add_argument("--no-thinking", action="store_true",
                    help="compatibility alias for --thinking-mode disabled; valid only for "
                         "chat-template-controlled model families")
    a = ap.parse_args(argv)
    if a.no_thinking:
        if a.thinking_mode not in ("default", "disabled") or a.reasoning_effort is not None:
            ap.error("--no-thinking conflicts with explicit thinking controls")
        a.thinking_mode = "disabled"
    if a.reasoning_effort is not None and a.thinking_mode != "default":
        ap.error("--reasoning-effort cannot be combined with --thinking-mode")
    if a.visible_answer_tokens < 1 or a.reasoning_headroom_tokens < 0:
        ap.error("token allocations must be visible>=1 and reasoning>=0")
    max_tokens = a.visible_answer_tokens + a.reasoning_headroom_tokens
    if max_tokens > 65536:
        ap.error("combined completion allocation cannot exceed 65536")
    if not 1 <= a.needle_ctx <= 1000000:
        ap.error("--needle-ctx must be from 1 through 1000000")
    if not 1 <= a.tool_batch <= 128:
        ap.error("--tool-batch must be from 1 through 128")
    if not 0 < a.timeout <= 3600:
        ap.error("--timeout must be greater than 0 and at most 3600 seconds")
    selected = [item.strip() for item in a.checks.split(",") if item.strip()]
    known_checks = {
        "smoke", "json", "needle", "tools", "streaming-tools",
        "tool-result", "responses", "image", "ocr", "video",
    }
    unknown = sorted(set(selected) - known_checks)
    if not selected or unknown:
        ap.error(
            "--checks must select smoke,json,needle,tools,streaming-tools,"
            "tool-result,responses,image,ocr,video; unknown=%s"
            % unknown
        )
    image_data = None
    image_identity = None
    if {"image", "ocr"} & set(selected):
        if "image" in selected and not a.image_expect:
            ap.error("--checks image requires at least one --image-expect")
        if "ocr" in selected and not a.ocr_expect:
            ap.error("--checks ocr requires at least one --ocr-expect")
        try:
            image_data, image_identity = load_image_data(a.image_path)
        except ValueError as exc:
            ap.error(str(exc))
    video_data = None
    video_identity = None
    if "video" in selected:
        if not a.video_expect:
            ap.error("--checks video requires at least one --video-expect")
        try:
            video_data, video_identity = load_video_data(a.video_path)
        except ValueError as exc:
            ap.error(str(exc))
    allowed_finish_reasons = {
        item.strip() for item in a.allowed_finish_reasons.split(",") if item.strip()
    }
    if not allowed_finish_reasons:
        ap.error("--allowed-finish-reasons cannot be empty")
    try:
        _validate_output_path(a.json_out)
    except OSError as exc:
        ap.error(str(exc))
    from .eval import resolve_endpoint_target
    try:
        a.base_url, a.model, _selected = resolve_endpoint_target(
            tier=a.tier,
            manifest=a.manifest,
            base_url=a.base_url,
            model=a.model,
            recipe=a.recipe,
            registry=a.registry,
        )
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    try:
        validate_reasoning_control(
            a.model,
            thinking_mode=a.thinking_mode,
            no_thinking=a.no_thinking,
            reasoning_effort=a.reasoning_effort,
        )
    except ValueError as exc:
        ap.error(str(exc))
    try:
        api_key = resolve_api_key(a.api_key_env)
    except ValueError as exc:
        ap.error(str(exc))
    ctk = ({"enable_thinking": True} if a.thinking_mode == "enabled" else
           {"enable_thinking": False} if a.thinking_mode == "disabled" else None)
    evidence = []
    if a.dry_run:
        print(json.dumps({
            "schema": "anvil-serving.eval-plan/v1",
            "workload": "preflight",
            "target": {"base_url": a.base_url, "model": a.model, "tier": a.tier,
                       "manifest": a.manifest},
            "checks": selected,
            "budget": {"visible_answer_tokens": a.visible_answer_tokens,
                       "reasoning_headroom_tokens": a.reasoning_headroom_tokens,
                       "max_completion_tokens": max_tokens},
            "timeout_seconds": a.timeout,
            "multimodal_input": image_identity,
            "video_input": video_identity,
            "image_expect": a.image_expect,
            "ocr_expect": a.ocr_expect,
            "video_expect": a.video_expect,
            "output": a.json_out,
            "deferred": ["endpoint identity", "model requests", "artifact write"],
        }, indent=2, sort_keys=True, ensure_ascii=True))
        return 0
    available = {
        "smoke": ("smoke (short coding)", lambda: t_smoke(a.base_url, a.model, api_key, ctk, max_tokens, a.reasoning_effort, evidence, a.timeout)),
        "json": ("structured JSON", lambda: t_json(a.base_url, a.model, api_key, ctk, max_tokens, a.reasoning_effort, evidence, a.timeout)),
        "needle": (f"needle @ ~{a.needle_ctx} ctx", lambda: t_needle(a.base_url, a.model, api_key, a.needle_ctx, ctk, max_tokens, a.reasoning_effort, evidence, a.timeout)),
        "tools": (f"shared-prefix tool batch x{a.tool_batch}", lambda: t_tool_batch(a.base_url, a.model, api_key, a.tool_batch, ctk, max_tokens, a.reasoning_effort, evidence, a.timeout)),
        "streaming-tools": ("streaming tool call", lambda: t_streaming_tool(
            a.base_url, a.model, api_key, ctk, max_tokens, evidence, a.timeout,
        )),
        "tool-result": ("tool-result continuation", lambda: t_tool_result(
            a.base_url, a.model, api_key, ctk, max_tokens,
            a.reasoning_effort, evidence, a.timeout,
        )),
        "responses": ("Responses API subset", lambda: t_responses(
            a.base_url, a.model, api_key, ctk, max_tokens, evidence, a.timeout,
        )),
        "image": ("general image understanding", lambda: t_multimodal(
            a.base_url, a.model, api_key, image_data, image_identity,
            a.image_expect, check="image", ctk=ctk, max_tokens=max_tokens,
            reasoning_effort=a.reasoning_effort, evidence=evidence, timeout=a.timeout,
        )),
        "ocr": ("verbatim image OCR", lambda: t_multimodal(
            a.base_url, a.model, api_key, image_data, image_identity,
            a.ocr_expect, check="ocr", ctk=ctk, max_tokens=max_tokens,
            reasoning_effort=a.reasoning_effort, evidence=evidence, timeout=a.timeout,
        )),
        "video": ("bounded video understanding", lambda: t_video(
            a.base_url, a.model, api_key, video_data, video_identity,
            a.video_expect, ctk=ctk, max_tokens=max_tokens,
            reasoning_effort=a.reasoning_effort, evidence=evidence, timeout=a.timeout,
        )),
    }
    tests = [available[name] for name in selected]
    allok = True
    print(_console_safe(
        f"PRE-FLIGHT  {a.base_url}  model={a.model}  thinking={a.thinking_mode} "
        f"visible={a.visible_answer_tokens} reasoning_headroom={a.reasoning_headroom_tokens} "
        f"max_tokens={max_tokens}\n" + "-"*60
    ))
    results = []
    for name, fn in tests:
        ok, detail = fn()
        allok &= ok
        results.append({"name": name, "passed": ok, "detail": detail})
        print(_console_safe(f"[{'PASS' if ok else 'FAIL'}] {name:38} {detail}"))
    bad_finishes = [item for item in evidence if item.get("finish_reason") not in allowed_finish_reasons]
    reasoning_seen = any(
        (item.get("reasoning_chars") or 0) > 0 or (item.get("reasoning_tokens") or 0) > 0
        for item in evidence
    )
    policy_errors = []
    if bad_finishes:
        policy_errors.append("disallowed finish_reason: %s" % sorted({
            repr(item.get("finish_reason")) for item in bad_finishes
        }))
    if a.reasoning_evidence == "required" and not reasoning_seen:
        policy_errors.append("reasoning evidence was required but not observed")
    if a.reasoning_evidence == "forbidden" and reasoning_seen:
        policy_errors.append("reasoning evidence was forbidden but observed")
    for error in policy_errors:
        print(f"[FAIL] {'evidence policy':38} {error}")
    allok &= not policy_errors
    print("-"*60); print("RESULT:", "ALL PASS" if allok else "FAILURES PRESENT")
    artifact = {
        "schema_version": "preflight/v2", "base_url": a.base_url, "model": a.model,
        "thinking": {"mode": a.thinking_mode, "chat_template_kwargs": ctk,
                     "reasoning_effort": a.reasoning_effort},
        "budget": {"visible_answer_tokens": a.visible_answer_tokens,
                   "reasoning_headroom_tokens": a.reasoning_headroom_tokens,
                   "max_completion_tokens": max_tokens},
        "multimodal_input": image_identity,
        "video_input": video_identity,
        "image_expect": a.image_expect,
        "ocr_expect": a.ocr_expect,
        "video_expect": a.video_expect,
        "checks": selected, "results": results, "observations": evidence,
        "evidence_policy": {"reasoning": a.reasoning_evidence,
                            "allowed_finish_reasons": sorted(allowed_finish_reasons),
                            "errors": policy_errors},
        "passed": allok,
    }
    if a.json_out:
        try:
            _atomic_write_json(a.json_out, artifact)
        except OSError as exc:
            ap.error(str(exc))
    return 0 if allok else 1

if __name__ == "__main__":
    raise SystemExit(main())
