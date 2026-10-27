import json
import re

import pytest

from anvil_serving.benchmarking import capacity_plan
from anvil_serving.benchmarking.cli import main


def plan(path):
    value = {'schema': 'anvil-serving.capacity-plan/v1', 'tokenizer_api': 'llamacpp-chat', 'requests': [
        {'label': 'anchor', 'total_tokens': 327680, 'max_tokens': 65536},
        *[{'label': 'peer', 'total_tokens': 32768, 'max_tokens': 16384} for _ in range(3)],
    ]}
    path.write_text(json.dumps(value))
    return value


def test_mixed_capacity_native_calibration_and_journal(tmp_path, monkeypatch):
    path = tmp_path / 'plan.json'; plan(path)
    counts = {}; captured = []
    def tokenize(url, key, timeout, body):
        prompt = body['messages'][0]['content']
        count = len(prompt) // 3
        counts[prompt] = count
        captured.append(body)
        return {'object': 'response.input_tokens', 'input_tokens': count}
    monkeypatch.setattr(capacity_plan, 'api_json', tokenize)
    def stream(base, model, prompt, key, max_tokens, **kwargs):
        marker = re.search(r'ANVIL_REQ_\d+_\d+', prompt).group()
        return {'ttft': .1, 'time_to_first_output': .05, 'e2e': .2, 'out_toks': 128,
                'output_token_source': 'usage', 'usage': {'prompt_tokens': counts[prompt], 'completion_tokens': 128},
                'finish_reasons': ['stop'], 'stream_terminal_observed': True,
                'visible_content': marker + ' ' + ' '.join(['code'] * 128)}
    out = tmp_path / 'out.json'
    assert main(['capacity', '--base-url', 'http://127.0.0.1:30002/v1', '--model', 'test',
                 '--request-plan', str(path), '--concurrency', '4', '--max-model-len', '327680', '--margin', '0',
                 '--prompt-cache-mode', 'unique', '--request-canaries', '--response-words', '128',
                 '--controlled-output-policy', 'strict', '--temperature', '1', '--top-p', '1', '--output', str(out)],
                stream_request=stream) == 0
    data = json.loads(out.read_text())
    rows = data['request_timings']
    assert [row['case_label'] for row in rows] == ['anchor', 'peer', 'peer', 'peer']
    assert [row['max_tokens'] for row in rows] == [65536, 16384, 16384, 16384]
    assert all(row['native_usage_parity'] and row['native_input_tokens'] == row['prompt_tokens'] for row in rows)
    assert all(row['native_input_tokens'] + row['max_tokens'] <= row['total_tokens'] for row in rows)
    assert all(body['temperature'] == 1 and body['top_p'] == 1 and body['stream_options'] == {'include_usage': True} for body in captured)
    journal = [json.loads(line) for line in (tmp_path / 'out.json.journal.jsonl').read_text().splitlines()]
    assert len([row for row in journal if row['record_type'] == 'outcome']) == 4
    assert journal[-1]['record_type'] == 'completion'


def test_native_adapters_match_controls_and_fail_closed(monkeypatch):
    seen = []
    def query(url, key, timeout, body):
        seen.append((url, body)); return {'object': 'response.input_tokens', 'input_tokens': 100}
    monkeypatch.setattr(capacity_plan, 'api_json', query)
    row = {'label': 'anchor', 'total_tokens': 200, 'max_tokens': 100}
    kwargs = dict(base_url='http://127.0.0.1:30002/v1', model='test', key=None, timeout=20,
                  shared='', index=0, marker=None, response_words=0, unique_prefix=False,
                  chat_template_kwargs={'enable_thinking': True}, reasoning_effort='high', temperature=1, top_p=.9, margin=0)
    capacity_plan.calibrate({'tokenizer_api': 'llamacpp-chat'}, row, **kwargs)
    assert seen[-1][1]['chat_template_kwargs'] == {'enable_thinking': True}
    assert seen[-1][1]['reasoning_effort'] == 'high' and seen[-1][1]['top_p'] == .9
    def vllm_query(url, key, timeout, body):
        seen.append((url, body)); return {'count': 100}
    monkeypatch.setattr(capacity_plan, 'api_json', vllm_query)
    capacity_plan.calibrate({'tokenizer_api': 'vllm-chat'}, row, **kwargs)
    assert seen[-1][0].endswith('/tokenize')
    assert seen[-1][1]['chat_template_kwargs'] == {'enable_thinking': True}
    assert seen[-1][1]['reasoning_effort'] == 'high'
    assert seen[-1][1]['add_generation_prompt'] is True
    monkeypatch.setattr(capacity_plan, 'api_json', lambda *args: {'object': 'response.input_tokens', 'input_tokens': True})
    with pytest.raises(ValueError): capacity_plan.calibrate({'tokenizer_api': 'llamacpp-chat'}, row, **kwargs)


def test_invalid_plan_is_rejected(tmp_path):
    path = tmp_path / 'plan.json'; value = plan(path)
    value['requests'][0]['max_tokens'] = 65537; path.write_text(json.dumps(value))
    with pytest.raises(ValueError): capacity_plan.load_plan(path, 327680, 0)
    value = plan(path); value['requests'][0]['extra'] = 1; path.write_text(json.dumps(value))
    with pytest.raises(ValueError): capacity_plan.load_plan(path, 327680, 0)
    path.write_bytes(b' ' * 65537)
    with pytest.raises(ValueError): capacity_plan.load_plan(path, 327680, 0)
    path.write_text('{"schema":"a","schema":"b"}')
    with pytest.raises(ValueError): capacity_plan.load_plan(path, 327680, 0)


def test_missing_or_mismatched_usage_is_retained_as_failure(tmp_path, monkeypatch):
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps({'schema': 'anvil-serving.capacity-plan/v1', 'tokenizer_api': 'llamacpp-chat',
                                'requests': [{'label': 'anchor', 'total_tokens': 200, 'max_tokens': 100}]}))
    monkeypatch.setattr(capacity_plan, 'api_json', lambda *args: {'object': 'response.input_tokens', 'input_tokens': 100})
    out = tmp_path / 'failure.json'
    def stream(*args, **kwargs):
        return {'ttft': .1, 'e2e': .2, 'out_toks': 1, 'usage': {'prompt_tokens': 99, 'completion_tokens': 1},
                'finish_reasons': ['stop'], 'stream_terminal_observed': True}
    assert main(['capacity', '--base-url', 'http://127.0.0.1:30002/v1', '--model', 'test',
                 '--request-plan', str(path), '--max-model-len', '200', '--margin', '0', '--output', str(out)],
                stream_request=stream) == 1
    failure = json.loads(out.read_text())['failures'][0]
    assert failure['failure_class'] == 'native_usage_mismatch'
    assert failure['case_label'] == 'anchor' and failure['native_input_tokens'] == 100
    assert failure['response_metadata']['usage']['prompt_tokens'] == 99


def test_calibration_transport_error_is_not_retried(monkeypatch):
    calls = []
    def fail(*args):
        calls.append(1)
        raise OSError('offline')
    monkeypatch.setattr(capacity_plan, 'api_json', fail)
    with pytest.raises(OSError):
        capacity_plan.calibrate({'tokenizer_api':'llamacpp-chat'}, {'total_tokens':200,'max_tokens':100},
            base_url='http://127.0.0.1:30002/v1', model='test', key=None, timeout=30,
            shared='', index=0, marker=None, response_words=0, unique_prefix=False,
            chat_template_kwargs=None, reasoning_effort=None, temperature=0, top_p=None, margin=0)
    assert len(calls) == 1


def test_calibration_failure_journal_precedes_any_generation(tmp_path, monkeypatch):
    path = tmp_path / 'plan.json'; plan(path)
    count_calls = []
    def count(*args):
        count_calls.append(1)
        raise OSError('unavailable')
    monkeypatch.setattr(capacity_plan, 'api_json', count)
    out = tmp_path / 'partial.json'
    def unexpected_generation(*args, **kwargs):
        raise AssertionError('generation must not start')
    assert main(['capacity', '--base-url', 'http://127.0.0.1:30002/v1', '--model', 'test',
                 '--request-plan', str(path), '--max-model-len', '327680', '--margin', '0', '--output', str(out)],
                stream_request=unexpected_generation) == 1
    assert count_calls == [1] and not out.exists()
    journal = [json.loads(line) for line in (tmp_path / 'partial.json.journal.jsonl').read_text().splitlines()]
    assert [row['record_type'] for row in journal] == ['header', 'calibration_failure']
