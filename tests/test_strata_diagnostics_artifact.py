"""Offline gates for metadata-only diagnostics in the pinned derived image."""
import ast
import hashlib
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


def test_timing_reconstructs_only_numeric_allowed_fields(diag, capsys):
    diag.emit_diagnostic(MAIN)
    diag.emit_diagnostic(HOST)
    rows = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert rows[0]['tokens'] == 7420
    assert rows[0]['phases']['wait copy'] == {'ms': 20000, 'percent': 74.1}
    assert rows[1]['chunk_setup_ms'] == 10
    assert rows[1]['after_chunk_ms'] == 900


@pytest.mark.parametrize('line', [
    'prompt: secret', MAIN + 'secret', MAIN.replace('7420', '-1'),
    MAIN.replace('7420', 'nan'), MAIN.replace('wait copy', 'secret prompt'),
    MAIN.replace('74.1%', '101.0%'), MAIN.replace('20000', '9999999999999'),
    MAIN.replace('gemm down 6000 (22.2%)', 'wait copy 6000 (22.2%)'),
    MAIN.replace('embed+steps 40 (0.1%)', 'ple 40 (0.1%)'),
    'x' * 5000 + MAIN, HOST.replace('10 ms', 'secret ms'),
])
def test_unknown_or_malformed_text_never_echoed(diag, line, capsys):
    diag.emit_diagnostic(line)
    assert capsys.readouterr().out == ''


def test_pinned_source_refuses_wrong_hash_before_patch():
    patch = load('patch_server')
    with pytest.raises(ValueError, match='source identity'):
        patch.patch_source(b'not the pinned source')


def test_patch_keeps_separate_enable_flags_and_fails_changed_anchor(monkeypatch):
    patch = load('patch_server')
    data = ('def echo_requests(log_path: str, offset: int) -> None:\n    pass\n\n'
            'def experts_loading_words(args, size):\n    pass\n\n'
            'def boot(log):\n    ' + patch.OLD_ENABLE + '\n        pass\n').encode()
    monkeypatch.setattr(patch, 'SOURCE_SHA256', hashlib.sha256(data).hexdigest())
    output = patch.patch_source(data).decode()
    ast.parse(output)
    assert 'or os.environ.get("STRATA_DIAGNOSTIC_LINES") == "1"' in output
    assert 'ENGINE_REQUEST.search(line) if requests else None' in output
    bad = data.replace(b'def experts_loading_words(', b'def changed(')
    monkeypatch.setattr(patch, 'SOURCE_SHA256', hashlib.sha256(bad).hexdigest())
    with pytest.raises(ValueError, match='anchors changed'):
        patch.patch_source(bad)


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


def test_pack_requires_exact_hash_and_complete_layout(diag, monkeypatch):
    data = fixture_pack()
    with pytest.raises(ValueError, match='identity mismatch'):
        diag.pack_metadata(data)
    monkeypatch.setattr(diag, 'PACK_BYTES', len(data))
    monkeypatch.setattr(diag, 'PACK_SHA256', hashlib.sha256(data).hexdigest())
    out = diag.pack_metadata(data)
    assert out['total_expert_bytes'] == 24576
    assert out['format_pair_layer_counts'] == {'21:20': 48}
    assert 'fixture.gguf' not in json.dumps(out)
    bad = data.replace(b'0 21 20 0 1', b'1 21 20 0 1')
    monkeypatch.setattr(diag, 'PACK_SHA256', hashlib.sha256(bad).hexdigest())
    with pytest.raises(ValueError, match='offsets'):
        diag.pack_metadata(bad)


def test_fixed_pack_reader_rejects_extra_bytes(diag, monkeypatch, tmp_path):
    data = fixture_pack()
    path = tmp_path / 'native_experts.txt'
    path.write_bytes(data + b'x')
    monkeypatch.setattr(diag, 'PACK', path)
    monkeypatch.setattr(diag, 'PACK_BYTES', len(data))
    monkeypatch.setattr(diag, 'PACK_SHA256', hashlib.sha256(data).hexdigest())
    with pytest.raises(ValueError, match='identity mismatch'):
        diag.emit_pack_metadata()


def test_wrapper_preserves_reviewed_launcher_and_verifies_before_metadata(monkeypatch):
    calls = []
    def original():
        calls.append('prepare')
    base = types.SimpleNamespace(prepare=original)
    runtime = types.SimpleNamespace(base=base)
    def main():
        base.prepare()
        calls.append('reviewed launcher continued')
    monkeypatch.setitem(sys.modules, 'runtime_m50', types.SimpleNamespace(runtime=runtime, main=main))
    monkeypatch.setitem(sys.modules, 'diagnostics', types.SimpleNamespace(
        emit_pack_metadata=lambda: calls.append('verified metadata')))
    load('runtime_diagnostics').main()
    assert calls == ['prepare', 'verified metadata', 'reviewed launcher continued']
    assert base.prepare is original


def test_layer_pins_parent_and_preserves_config():
    dockerfile = (ROOT / 'Dockerfile').read_text()
    assert 'sha256:d9c5ae90a028ba7ae5cee9c01ebddc1524da3b831be47de0daf7c6a3e211817a' in dockerfile
    assert 'ENV ' not in dockerfile
    assert 'STRATA_PF_FUSED' not in dockerfile
    assert not (ROOT / 'runtime-configs').exists()
