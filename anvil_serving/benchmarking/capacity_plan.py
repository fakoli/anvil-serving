"""Bounded per-request capacity plans and native prompt calibration."""
import json
import re
import time
import urllib.request
from pathlib import Path

from .requests import build_body, make_prompt

def api_json(url, key, timeout, body):
    """Bound one native adapter call; calibration never retries transport errors."""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(url, headers=headers, data=json.dumps(body).encode())
    deadline = time.monotonic() + timeout
    chunks = []
    size = 0
    with urllib.request.urlopen(request, timeout=timeout) as response:
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("native token count deadline")
            chunk = response.read1(min(65536, 1048577 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > 1048576:
                raise ValueError("native token response exceeds 1 MiB")
    if time.monotonic() >= deadline:
        raise TimeoutError("native token count deadline")
    return json.loads(b"".join(chunks))


def load_plan(path, max_model_len, margin):
    with Path(path).open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('capacity plan exceeds 64 KiB')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate capacity plan key')
            result[key] = value
        return result
    plan = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(plan, dict) or set(plan) != {'schema', 'tokenizer_api', 'requests'} or plan['schema'] != 'anvil-serving.capacity-plan/v1':
        raise ValueError('invalid capacity plan schema or fields')
    if plan['tokenizer_api'] not in ('llamacpp-chat', 'vllm-chat'):
        raise ValueError('unsupported capacity tokenizer')
    rows = plan['requests']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 256:
        raise ValueError('capacity plan requires 1..256 requests')
    if not max_model_len:
        raise ValueError('capacity plan requires explicit --max-model-len')
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'label', 'total_tokens', 'max_tokens'}:
            raise ValueError('invalid capacity request fields')
        if not isinstance(row['label'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', row['label']):
            raise ValueError('invalid capacity request label')
        total, reserve = row['total_tokens'], row['max_tokens']
        if type(total) is not int or type(reserve) is not int or not 1 <= reserve <= 65536 or not 32 <= total <= max_model_len or total - reserve - margin < 32:
            raise ValueError('invalid capacity request window or reserve')
    if sum(row['total_tokens'] - row['max_tokens'] for row in rows) > 67108864:
        raise ValueError('capacity plan exceeds aggregate prompt limit')
    return plan


def calibrate(plan, row, *, base_url, model, key, timeout, shared, index, marker, response_words, unique_prefix, chat_template_kwargs, reasoning_effort, temperature, top_p, margin):
    """Build the actual capacity prompt and bound native token count below its reserve."""
    target = row['total_tokens'] - row['max_tokens'] - margin
    size = target
    for _ in range(8):
        if size > (8 * 1024 * 1024) // 3:
            raise ValueError("capacity calibration prompt exceeds 8 MiB")
        prompt = make_prompt(shared, size, index, marker=marker, response_words=response_words, unique_prefix=unique_prefix)
        if len(prompt.encode('utf-8')) > 8 * 1024 * 1024:
            raise ValueError('capacity calibration prompt exceeds 8 MiB')
        if plan['tokenizer_api'] == 'llamacpp-chat':
            body = build_body(model, prompt, row['max_tokens'], chat_template_kwargs, reasoning_effort, temperature, top_p)
            response = api_json(base_url.rstrip('/') + '/chat/completions/input_tokens', key, min(timeout, 30), body)
            if not isinstance(response, dict) or response.get('object') != 'response.input_tokens':
                raise ValueError('invalid native token response')
            count = response.get('input_tokens')
        else:
            body = {'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'add_generation_prompt': True, 'chat_template_kwargs': chat_template_kwargs or {}}
            if reasoning_effort is not None:
                body['reasoning_effort'] = reasoning_effort
            response = api_json(base_url.rstrip('/')[:-3] + '/tokenize', key, min(timeout, 30), body)
            count = response.get('count') if isinstance(response, dict) else None
        if type(count) is not int or count <= 0:
            raise ValueError('native token count must be positive')
        if target - max(8, int(target * .005)) <= count <= target:
            return prompt, count
        size = max(1, int(size * (target - max(8, int(target * .002))) / count))
        if size > 8 * 1024 * 1024:
            raise ValueError('capacity calibration exceeds 8 MiB')
    raise ValueError('native token calibration failed')
