"""Synthetic contract fixtures; no model/runtime dependency."""
from __future__ import annotations

import json

import pytest

from anvil_serving import collectors
from anvil_serving.tensorfold_telemetry import BUCKETS, HISTOGRAMS, parse_metrics, parse_request_counts
from tests.conftest import Response


def metrics(*, active=0, complete=1, kv=1, bound=0):
    rows = [
        'tensorfold:anvil_contract_info{version="1"} 1',
        f'tensorfold:requests_running {active}',
        f'tensorfold:anvil_streams_active {active}',
        f'tensorfold:anvil_streams_observed {active}',
        f'tensorfold:anvil_streams_complete {complete}',
        f'tensorfold:anvil_kv_allocation_available {kv}',
        f'tensorfold:anvil_full_context_bound_available {bound}',
    ]
    for index in range(active):
        rows += [f'tensorfold:anvil_stream_context_tokens{{stream="{index}"}} 128',
                 f'tensorfold:kv_cache_usage_ratio{{pool="{index}"}} 0.125',
                 f'tensorfold:kv_cache_usage_perc{{stream="{index}"}} 0.125']
    if kv:
        rows += ['tensorfold:anvil_kv_pool_info{scope="global_logical_decode_kv_pool"} 1',
                 'tensorfold:anvil_kv_pool_partitions 1',
                 'tensorfold:anvil_kv_allocated_pages 10', 'tensorfold:anvil_kv_free_pages 90',
                 'tensorfold:anvil_kv_total_pages 100', 'tensorfold:anvil_kv_reserved_pages 8',
                 'tensorfold:anvil_kv_page_size_tokens 128']
    rows += ['tensorfold:anvil_arrivals_total 8', 'tensorfold:anvil_admissions_total 8',
             'tensorfold:anvil_prefill_started_total 8', 'tensorfold:anvil_first_tokens_total 8']
    for stage in HISTOGRAMS:
        name = 'tensorfold:anvil_' + stage + '_seconds'
        rows += [name + '_bucket{le="' + ('+Inf' if edge == float('inf') else str(edge)) + '"} 8' for edge in BUCKETS]
        rows += [name + '_count 8', name + '_sum 0.04']
    if bound:
        rows += ['tensorfold:anvil_full_context_bound_info{semantics="additional_full_context_requests",policy="full_context_reservation",formula_version="1"} 1',
                 'tensorfold:anvil_full_context_requested_tokens 1024',
                 'tensorfold:anvil_full_context_reserve_pages 2',
                 'tensorfold:anvil_full_context_per_request_reserve_tokens 128',
                 'tensorfold:anvil_full_context_admission_limit 4',
                 'tensorfold:anvil_full_context_active_requests 1',
                 'tensorfold:anvil_full_context_bound 3']
    return '\n'.join(rows) + '\n'


@pytest.mark.parametrize('active', [0, 1, 4])
def test_idle_c1_c4_stream_and_kv_reconciliation(active):
    result = parse_metrics(metrics(active=active))
    assert result['version'] == 1
    assert result['streams']['active'] == result['streams']['observed'] == active
    assert len(result['streams']['usage_ratios']) == active
    assert result['kv']['allocated_pages'] + result['kv']['free_pages'] == 100
    assert result['kv']['reserved_pages'] == 8
    assert result['timing']['histograms']['arrival_to_admission']['count'] == 8
    assert result['full_context_bound']['status'] == 'unavailable'
    assert 'full-context-concurrency' not in result['capabilities']


def test_missing_contract_and_incomplete_sources_never_become_zero():
    old = parse_metrics('tensorfold:prompt_cache_bytes 123456\n')
    assert old['version'] is None
    assert all(old[key]['status'] == 'unavailable' for key in ('streams', 'kv', 'timing', 'full_context_bound'))
    result = parse_metrics(metrics(active=1, complete=0, kv=0))
    assert result['streams']['status'] == result['kv']['status'] == 'unavailable'
    assert result['streams']['active'] == 1
    assert 'allocated_pages' not in result['kv']


def test_derived_bound_recomputes_all_policy_and_reserve_inputs():
    raw = metrics(active=1, bound=1)
    derived = parse_metrics(raw)['full_context_bound']
    assert derived['value'] == 3 and derived['status'] == 'estimated'
    constrained = raw.replace('anvil_kv_free_pages 90', 'anvil_kv_free_pages 20').replace('anvil_kv_total_pages 100', 'anvil_kv_total_pages 30').replace('anvil_full_context_bound 3', 'anvil_full_context_bound 1')
    assert parse_metrics(constrained)['full_context_bound']['value'] == 1
    missing = raw.replace('tensorfold:anvil_full_context_per_request_reserve_tokens 128\n', '')
    assert parse_metrics(missing)['full_context_bound']['status'] == 'unavailable'
    with pytest.raises(ValueError, match='formula mismatch'):
        parse_metrics(raw.replace('anvil_full_context_bound 3', 'anvil_full_context_bound 4'))


def test_missing_policy_pool_and_timing_inputs_remain_unavailable():
    raw = metrics()
    for missing, section in [('tensorfold:anvil_kv_pool_partitions 1\n', 'kv'),
                             ('tensorfold:anvil_admissions_total 8\n', 'timing')]:
        assert parse_metrics(raw.replace(missing, ''))[section]['status'] == 'unavailable'
    assert parse_metrics(raw.replace('anvil_kv_pool_partitions 1', 'anvil_kv_pool_partitions 2'))['kv']['status'] == 'unavailable'
    partial = raw.replace('anvil_arrivals_total 8', 'anvil_arrivals_total 9').replace('anvil_admissions_total 8', 'anvil_admissions_total 9')
    assert parse_metrics(partial)['timing']['reason'] == 'incomplete_timing_boundaries'


def test_partitioned_census_preserves_capacity_and_temporary_twin_subset():
    raw = metrics().replace('anvil_kv_pool_partitions 1', 'anvil_kv_pool_partitions 2')
    assert parse_metrics(raw)['kv']['status'] == 'unavailable'
    raw += 'tensorfold:anvil_kv_temporary_twin_pages 4\n'
    result = parse_metrics(raw)
    assert result['kv'] == dict(status='measured', complete=True, scope='global_logical_decode_kv_pool',
                               partitions=2, allocated_pages=10, free_pages=90, total_pages=100,
                               reserved_pages=8, page_size_tokens=128, temporary_twin_pages=4)
    assert result['full_context_bound']['reason'] == 'partitioned_admission_policy_unqualified'
    assert 'global-kv-allocation' in result['capabilities']
    assert 'full-context-concurrency' not in result['capabilities']
    assert parse_metrics(metrics())['kv']['temporary_twin_pages'] == 0
    assert parse_metrics(metrics() + 'tensorfold:anvil_kv_temporary_twin_pages 1\n')['kv']['temporary_twin_pages'] == 1
    assert parse_metrics(raw.replace('anvil_kv_pool_partitions 2', 'anvil_kv_pool_partitions 3'))['kv']['status'] == 'unavailable'
    for replacement in ('11', '-1', '1.5', '9007199254740993'):
        with pytest.raises(ValueError):
            parse_metrics(raw.replace('anvil_kv_temporary_twin_pages 4', 'anvil_kv_temporary_twin_pages ' + replacement))
    with pytest.raises(ValueError, match='pool partitioning'):
        parse_metrics(raw.replace('anvil_full_context_bound_available 0', 'anvil_full_context_bound_available 1'))


@pytest.mark.parametrize('replacement', [
    ('anvil_streams_observed 1', 'anvil_streams_observed 2'),
    ('requests_running 1', 'requests_running 0'),
    ('kv_cache_usage_perc{stream="0"} 0.125', 'kv_cache_usage_perc{stream="0"} 0.5'),
    ('anvil_kv_free_pages 90', 'anvil_kv_free_pages 91'),
    ('anvil_kv_reserved_pages 8', 'anvil_kv_reserved_pages 91'),
    ('anvil_kv_page_size_tokens 128', 'anvil_kv_page_size_tokens 0'),
    ('anvil_arrival_to_admission_seconds_bucket{le="+Inf"} 8', 'anvil_arrival_to_admission_seconds_bucket{le="+Inf"} 7'),
    ('anvil_arrival_to_admission_seconds_count 8', 'anvil_arrival_to_admission_seconds_count NaN'),
    ('anvil_admissions_total 8', 'anvil_admissions_total 7'),
    ('anvil_streams_active 1', 'anvil_streams_active 9007199254740993'),
    ('anvil_streams_active 1', 'anvil_streams_active 9007199254740990.5'),
    ('anvil_contract_info{version="1"}', 'anvil_contract_info{version="2"}'),
])
def test_contradictory_or_invalid_metrics_fail_closed(replacement):
    with pytest.raises(ValueError):
        parse_metrics(metrics(active=1).replace(*replacement))


def test_duplicate_series_and_oversize_fail_closed():
    with pytest.raises(ValueError, match='duplicate'):
        parse_metrics(metrics() + 'tensorfold:anvil_streams_active 0\n')
    with pytest.raises(ValueError, match='256 KiB'):
        parse_metrics(' ' * (256 * 1024 + 1))


def test_request_gauges_preserve_unavailable_and_reject_ambiguous_counts():
    raw = 'tensorfold:requests_running 1\ntensorfold:requests_waiting 2\n'
    assert parse_request_counts(raw) == {'running': 1, 'waiting': 2}
    for invalid in (raw.replace('requests_waiting 2', 'requests_waiting 2.5'),
                    raw.replace('requests_waiting 2', 'requests_waiting NaN'),
                    raw + 'tensorfold:requests_running 0\n',
                    raw.replace('requests_waiting 2', 'requests_waiting{model="other"} 2'),
                    'tensorfold:requests_running 1\n'):
        with pytest.raises(ValueError):
            parse_request_counts(invalid)


def test_supported_adapter_preserves_auth_and_explicit_capability_gaps():
    seen = {}
    def opener(request, timeout):
        seen['accept'] = request.get_header('Accept')
        seen['auth'] = request.get_header('Authorization')
        return Response(metrics(active=4).encode())
    config = collectors.CollectorConfig(name='engine', adapter='tensorfold-prometheus-v1',
        endpoint='http://100.64.0.20:30000/metrics', capabilities=('active-streams', 'full-context-concurrency'), auth_env='COLLECTOR_TOKEN')
    result = collectors.inspect_adapter(config, environment={'COLLECTOR_TOKEN': 'secret-token'}, opener=opener)
    assert result['ok'] is False
    assert result['available_capabilities'] == ['active-streams']
    assert result['missing_capabilities'] == ['full-context-concurrency']
    assert result['telemetry']['streams']['active'] == 4
    assert result['telemetry']['timing']['histograms'] == parse_metrics(metrics(active=4))['timing']['histograms']
    assert seen == {'accept': 'text/plain', 'auth': 'Bearer secret-token'}
    assert 'secret-token' not in json.dumps(result)


def test_tensorfold_histograms_survive_collector_and_cli_json_redaction(monkeypatch, capsys):
    from anvil_serving.cli import main

    monkeypatch.setattr(collectors, '_urlopen_no_proxy_no_redirect', lambda request, timeout: Response(metrics().encode()))
    monkeypatch.setenv('ANVIL_HISTOGRAM_TEST_TOKEN', 'opaque-test-secret')
    assert main(['--json', 'collectors', 'inspect', '--name', 'engine',
                 '--adapter', 'tensorfold-prometheus-v1', '--endpoint', 'http://127.0.0.1:30000/metrics',
                 '--capability', 'engine-admission-timing', '--auth-env', 'ANVIL_HISTOGRAM_TEST_TOKEN']) == 0
    output = capsys.readouterr().out
    envelope = json.loads(output)
    result = json.loads(envelope['data'])
    assert result['telemetry']['timing']['histograms'] == parse_metrics(metrics())['timing']['histograms']
    assert 'opaque-test-secret' not in output
