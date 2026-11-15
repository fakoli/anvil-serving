"""Offline gates for metadata-only diagnostics in the pinned derived image."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'configs/runtime/strata-6f32ec0-sm120/diagnostics-m50'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def diag():
    return load('diagnostics')


MAIN = ('strata prefill timing: 7420 tokens, GPU timeline 27000 ms, wall 28000 ms, '
        'host staging 800 ms: embed+steps 40 (0.1%) wait copy 20000 (74.1%) gemm down 6000 (22.2%)\n')
HOST = ('strata prefill timing: host: chunk setup (PLE rows, the expert stream plan) 10 ms, '
        'waiting for each chunk 27000 ms, after each chunk (the draft layer, progress) 900 ms, PLE 90 ms\n')


@pytest.mark.parametrize('requests,diagnostics', [(False, True), (True, False), (True, True)])
def test_follower_handles_partial_and_overlong_lines_without_suffix_leak(
        diag, monkeypatch, capsys, requests, diagnostics):
    patch = load('patch_server')
    monkeypatch.setitem(sys.modules, 'anvil_diagnostics', diag)
    monkeypatch.delenv('STRATA_REQUEST_LINES', raising=False)
    monkeypatch.delenv('STRATA_DIAGNOSTIC_LINES', raising=False)
    if requests:
        monkeypatch.setenv('STRATA_REQUEST_LINES', '1')
    if diagnostics:
        monkeypatch.setenv('STRATA_DIAGNOSTIC_LINES', '1')
    chunks = iter(['x' * 4097, MAIN, MAIN[:45], '', MAIN[45:], HOST])
    class StopTail(Exception):
        pass
    class Stream(io.StringIO):
        def readline(self, size):
            assert size == 4097
            try:
                return next(chunks)
            except StopIteration:
                raise StopTail
    stream = Stream('ignored prior data')
    searches = []
    engine_request = types.SimpleNamespace(search=lambda line: searches.append(line))
    namespace = {'open': lambda *a, **k: stream, 'os': os,
                 'time': types.SimpleNamespace(sleep=lambda _: None), 'ENGINE_REQUEST': engine_request}
    exec(patch.REPLACEMENT, namespace)
    with pytest.raises(StopTail):
        namespace['echo_requests']('fixed-log', 7)
    rows = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert len(rows) == (2 if diagnostics else 0)
    assert searches == ([MAIN, HOST] if requests else [])


def fixture_pack():
    header = '# strata native experts v4: (n_expert 512, total 24576; fixed)\n'
    return (header + ''.join(f'{i} 21 20 {i * 512} 1 0 0 0 fixture.gguf\n' for i in range(48))).encode()
