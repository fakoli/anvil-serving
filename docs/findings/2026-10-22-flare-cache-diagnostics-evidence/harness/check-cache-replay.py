"""Offline transport, ordering, hash and failure check; no model requests."""
import contextlib
import http.server
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading

spec = importlib.util.spec_from_file_location('fixture', Path(__file__).with_name('cache-replay.py'))
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
state = {'requests': 0, 'reset': False, 'bodies': [], 'stuck': False, 'echo': None}

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass
    def do_GET(self):
        if self.path == '/v1/models':
            payload = {'data': [{'id': f.MODEL}]}
            if state['echo'] == 'identity': payload['echo'] = ['offline-test-key']
            data = json.dumps(payload).encode()
        else:
            values = dict.fromkeys(f.COUNTERS, 0)
            values.update(dict.fromkeys(f.IDLE, 0))
            n = state['requests']
            values.update(flare_requests_completed_total=n,
                          flare_prefill_tokens_total=n * 100,
                          flare_generated_tokens_total=n,
                          flare_active_requests=0, flare_uptime_seconds=100 + n,
                          flare_host_cache_outbound_in_flight=0,
                          flare_host_cache_inbound_in_flight=0)
            if state['reset'] and n:
                values['flare_requests_completed_total'] = 0
            if state['stuck']:
                values['flare_host_cache_outbound_in_flight'] = 1
            data = ''.join(f'{k} {v}\n' for k, v in values.items()).encode()
            if state['echo'] == 'metrics': data += b'# offline-test-key\n'
        self.send_response(200); self.end_headers(); self.wfile.write(data)
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        state['bodies'].append(body)
        state['requests'] += 1
        delta = {'reasoning_content': 'reasoning only'} if state['requests'] == 1 else {'content': 'ok'}
        packet = {'id': 'test-id', 'choices': [{'delta': delta, 'finish_reason': 'stop'}],
                  'usage': {'prompt_tokens': 100, 'completion_tokens': 1}}
        data = ('data: ' + json.dumps(packet) + '\n\ndata: [DONE]\n\n').encode()
        self.send_response(200); self.end_headers(); self.wfile.write(data)

server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
f.BASE = f'http://127.0.0.1:{server.server_port}'
f.read_key = lambda _: ('offline-test-key', (1, 2, 3))
try:
    with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
        root = Path(temp)
        f.run(root / 'forward', 'forward', 52, 1500)
        f.run(root / 'reverse', 'reverse', 52, 1500)
        a = json.loads((root / 'forward/results.json').read_text())
        b = json.loads((root / 'reverse/results.json').read_text())
        assert [r['index'] for r in a] == list(range(52))
        assert [r['index'] for r in b] == list(reversed(range(52)))
        assert [r['body_sha256'] for r in a] == list(reversed([r['body_sha256'] for r in b]))
        assert state['bodies'][:52] == list(reversed(state['bodies'][52:]))
        assert not a[0]['visible'] and sum(r['visible'] for r in a) == 51
        assert json.loads((root / 'forward/summary.json').read_text())['completed'] == 52
        state.update(requests=0, reset=True)
        try:
            f.run(root / 'bad-counter', 'forward', 12, 1500)
            raise AssertionError('counter mismatch was accepted')
        except RuntimeError as error:
            assert 'counter reset' in str(error)
        failed = json.loads((root / 'bad-counter/summary.json').read_text())
        assert failed['completed'] == 0 and failed['error']
        assert (root / 'bad-counter/000-response.json').exists()
        assert len(state['bodies']) == 105  # No later request after failure.
        state['stuck'] = True
        f.IDLE_TIMEOUT = .3
        try:
            f.run(root / 'bad-drain', 'forward', 12, 1500)
            raise AssertionError('unsettled host copy accepted')
        except RuntimeError as error:
            assert 'did not drain' in str(error)
        assert len(state['bodies']) == 105
        state.update(stuck=False, reset=False)
        for target in ['identity', 'metrics']:
            state['echo'] = target
            try:
                f.run(root / target, 'forward', 12, 1500)
                raise AssertionError('credential echo accepted')
            except ValueError as error:
                assert 'protected content' in str(error)
            assert all(b'offline-test-key' not in p.read_bytes() for p in (root / target).iterdir())
        assert len(state['bodies']) == 105
        try:
            f.run(root / 'forward', 'forward', 52, 1500)
            raise AssertionError('existing evidence overwritten')
        except FileExistsError:
            pass
    print('PASS: real local HTTP/SSE transport; exact body/order reversal; reasoning-only retained; counter mismatch stops; evidence not overwritten')
finally:
    server.shutdown(); server.server_close(); thread.join()
