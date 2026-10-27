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


def test_mixed_capacity_native_calibration_and_journal(tmp_path, monkeypatch, capsys):
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
        return {'ttft': 1.0 if max_tokens == 65536 else .1, 'time_to_first_output': .05, 'e2e': 1.2 if max_tokens == 65536 else .2, 'out_toks': 128,
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
    assert data['case_metrics']['anchor']['metrics']['ttft_p50_ms'] == 1000
    assert data['case_metrics']['peer']['metrics']['ttft_p50_ms'] == 100
    assert 'ttft_p50_ms' not in data['metrics']
    assert 'TTFT  mean' not in capsys.readouterr().out
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
    value = plan(path); value['requests'][2]['max_tokens'] = 12000; path.write_text(json.dumps(value))
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


def test_plan_file_rejects_symlink_fifo_and_accepts_crlf(tmp_path):
    import os
    path = tmp_path / 'plan.json'; value = plan(path)
    path.write_bytes(json.dumps(value, indent=2).replace('\n', '\r\n').encode())
    assert capacity_plan.load_plan(path, 327680, 0) == value
    link = tmp_path / 'link'; link.symlink_to(path)
    with pytest.raises((OSError, ValueError)): capacity_plan.load_plan(link, 327680, 0)
    fifo = tmp_path / 'fifo'; os.mkfifo(fifo)
    with pytest.raises(ValueError): capacity_plan.load_plan(fifo, 327680, 0)
    path.write_text('[' * 1100 + ']' * 1100)
    with pytest.raises(ValueError): capacity_plan.load_plan(path, 327680, 0)


def test_native_http_wall_deadline_rejects_slow_response():
    import http.server
    import threading
    import time
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200); self.end_headers()
            time.sleep(.4)
            try: self.wfile.write(b'{}')
            except OSError: pass
        def log_message(self, *args): pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    start = time.monotonic()
    try:
        with pytest.raises((TimeoutError, OSError)):
            capacity_plan.api_json('http://127.0.0.1:%d/tokenize' % server.server_port, None, .05, {})
        assert time.monotonic() - start < .25
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_aggregate_calibration_deadline_blocks_dispatch(tmp_path, monkeypatch):
    import time
    import anvil_serving.benchmarking.cli as cli
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps({'schema':'anvil-serving.capacity-plan/v1', 'tokenizer_api':'llamacpp-chat',
                                'requests':[{'label':'anchor','total_tokens':200,'max_tokens':100}]}))
    def slow(*args, **kwargs):
        time.sleep(.05)
        return 'prompt', 100
    monkeypatch.setattr(cli, 'calibrate_capacity', slow)
    out = tmp_path / 'out.json'
    assert main(['capacity','--base-url','http://127.0.0.1:30002/v1','--model','test',
                 '--request-plan',str(path),'--max-model-len','200','--margin','0','--timeout','.01','--output',str(out)],
                stream_request=lambda *a, **k: pytest.fail('generation dispatched')) == 1
    journal = [json.loads(line) for line in (tmp_path / 'out.json.journal.jsonl').read_text().splitlines()]
    assert journal[-1]['record_type'] == 'calibration_failure'


def test_native_http_trickle_cannot_extend_wall_deadline():
    import http.server
    import threading
    import time
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200); self.end_headers()
            try:
                for byte in b'{"count":123456789}':
                    self.wfile.write(bytes([byte])); self.wfile.flush(); time.sleep(.02)
            except OSError: pass
        def log_message(self, *args): pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    start = time.monotonic()
    try:
        with pytest.raises((TimeoutError, OSError)):
            capacity_plan.api_json('http://127.0.0.1:%d/tokenize' % server.server_port, None, .08, {})
        assert time.monotonic() - start < .25
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_mixed_dry_run_labels_native_plan(tmp_path, capsys):
    path = tmp_path / 'plan.json'; plan(path)
    out = tmp_path / 'result.json'
    assert main(['capacity', '--base-url', 'http://127.0.0.1:30002/v1', '--model', 'test',
                 '--request-plan', str(path), '--max-model-len', '327680', '--margin', '0',
                 '--output', str(out), '--dry-run']) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview['capacity']['context_tokens'] == 'native-per-request-plan'
    assert [case['label'] for case in preview['request_plan']['requests']] == ['anchor', 'peer', 'peer', 'peer']
