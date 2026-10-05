"""The trace leaf forwards only fixed metadata and preserves the parent parser."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'configs/runtime/strata-6f32ec0-sm120'
LEAF = ROOT / 'diagnostics-trace-m50'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def parser(monkeypatch):
    base = load(ROOT / 'diagnostics-m50/diagnostics.py', 'trace_parent')
    monkeypatch.setitem(sys.modules, 'anvil_diagnostics_base', base)
    return load(LEAF / 'trace_diagnostics.py', 'trace_extension')


@pytest.mark.parametrize('line,expected', [
    ('strata trace: lent 1546 slots for 8192 tokens in 3.2 ms',
     {'event': 'anvil_strata_prefill_lent', 'slots': 1546, 'tokens': 8192, 'elapsed_ms': 3.2}),
    ('strata trace: refilled 1546 slots on 1 stage(s) in 12345.6 ms\r\n',
     {'event': 'anvil_strata_prefill_refilled', 'slots': 1546, 'stages': 1, 'elapsed_ms': 12345.6}),
    ('strata trace: read 7410 tokens (batched) in 12532.3 ms\n',
     {'event': 'anvil_strata_prefill_read', 'tokens': 7410, 'path': 'batched', 'elapsed_ms': 12532.3}),
    ('strata trace: read 7 tokens (windows) in 104.2 ms',
     {'event': 'anvil_strata_prefill_read', 'tokens': 7, 'path': 'windows', 'elapsed_ms': 104.2}),
    ('strata: prompt experts on the fused int8 kernels (STRATA_PF_FUSED=1, #136)',
     {'event': 'anvil_strata_fused_activation', 'observed_at_least_one_layer': True}),
])
def test_exact_native_records(parser, line, expected, capsys):
    assert parser.parse_diagnostic(line) == expected
    parser.emit_diagnostic(line)
    assert json.loads(capsys.readouterr().out) == expected


@pytest.mark.parametrize('line', [
    'strata trace: request 7410 0',
    'strata trace: read 7 tokens (secret) in 104.2 ms',
    'strata trace: lent -1 slots for 8192 tokens in 3.2 ms',
    'strata trace: lent 1 slots for 8192 tokens in nan ms',
    'strata trace: lent 1 slots for 8192 tokens in 1e9 ms',
    'strata trace: lent 1 slots for 8192 tokens in 2.22 ms',
    'strata trace: lent 9999999999999 slots for 8192 tokens in 3.2 ms',
    'prefix strata trace: lent 1 slots for 8192 tokens in 3.2 ms',
    'strata trace: lent 1 slots for 8192 tokens in 3.2 ms secret',
    'strata: prompt experts on the fused int8 kernels (STRATA_PF_FUSED=1, #136) secret',
    'strata trace: read 7 tokens (windows) in 104.2 ms\nsecret',
    'x' * 4097,
])
def test_malformed_or_unapproved_text_is_silent(parser, line, capsys):
    assert parser.parse_diagnostic(line) is None
    parser.emit_diagnostic(line)
    assert capsys.readouterr().out == ''


def test_parent_parser_is_delegated_unchanged(parser):
    base = sys.modules['anvil_diagnostics_base']
    assert parser.parse_timing is base.parse_timing
    line = ('strata prefill timing: host: chunk setup (PLE rows, the expert stream plan) 3 ms, '
            'waiting for each chunk 4 ms, after each chunk (the draft layer, progress) 5 ms, PLE 6 ms')
    assert parser.parse_diagnostic(line) == base.parse_timing(line)


@pytest.mark.parametrize('failure', ['helper', 'server', 'alias', 'syntax', None])
def test_install_verifies_before_mutation_and_preserves_parent(tmp_path, monkeypatch, failure):
    installer = load(LEAF / 'install_trace.py', 'install_trace_test')
    original = (ROOT / 'diagnostics-m50/diagnostics.py').read_bytes()
    # Use a synthetic pinned server to exercise installation independently of private captured source.
    server = b'# fixed server\n'
    monkeypatch.setattr(installer, 'SERVER_SHA256', hashlib.sha256(server).hexdigest())
    (tmp_path / 'serve').mkdir()
    (tmp_path / 'anvil').mkdir()
    helper_path = tmp_path / 'anvil_diagnostics.py'
    helper_path.write_bytes(original if failure != 'helper' else b'changed')
    (tmp_path / 'serve/server.py').write_bytes(server if failure != 'server' else b'changed')
    alias = tmp_path / 'anvil_diagnostics_base.py'
    if failure == 'alias':
        alias.write_bytes(b'existing')
    extension = (LEAF / 'trace_diagnostics.py').read_bytes() if failure != 'syntax' else b'def !'
    (tmp_path / 'anvil/trace_diagnostics.py').write_bytes(extension)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    if failure:
        with pytest.raises((ValueError, SyntaxError)):
            installer.install(tmp_path)
        assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    else:
        installer.install(tmp_path)
        assert alias.read_bytes() == original
        assert helper_path.read_bytes() == extension
        assert (tmp_path / 'serve/server.py').read_bytes() == server


def test_leaf_keeps_image_entrypoint_and_environment():
    dockerfile = (LEAF / 'Dockerfile').read_text()
    assert dockerfile.startswith('FROM anvil-strata:6f32ec0-sm120-diagnostics-m50-v1@sha256:2451bf190e65d96337dbf15683c58465f08e2a8fe49c9a591c741f3c94c3d3b3\n')
    assert '\nENTRYPOINT ' not in dockerfile
    assert '\nENV ' not in dockerfile
