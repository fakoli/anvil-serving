"""Require complete GPU receipts before attributing a native closing token."""
import copy
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / 'configs/runtime-patches/vllm/8244d1c4-marker-trace/analyze.py'
spec = importlib.util.spec_from_file_location('marker_analysis', PATH)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def fixture():
    records = []
    for rank in (0, 1):
        records.append(dict(kind='request_start', rank=rank, request=1, slot=0,
                            prompt_length=1, budget=64, max_tokens=512))
        for number, force in ((1, 1), (2, 0)):
            event = [1, force, analysis.END if force else -1, 0, -1, number, number-1, -1]
            state = dict(total_len=number, cache=[0, -1, number], markers={str(analysis.END): []})
            records.append(dict(kind='step', rank=rank, request=1, step=number, slot=0,
                before_budget=state, before_commit=state, after_budget=state,
                after_commit=dict(total_len=number+1), force_rows=[event],
                local_positions=[0], expanded_mapping=[0], effective_positions=[number],
                counts_before_commit=[1, 0], counts_after_commit=[1, 0], verified_count=1,
                boundary_enabled=False, boundary_metadata=None, boundary_stop_tokens=[], boundary_capture=None,
                query_start=[0, 1], logits_only=False, selected_tokens=[analysis.END], verified_tokens=[analysis.END],
                marker_logits_after=[[0, 1e9 if force else 0]],
                committed=[dict(position=number, token=analysis.END, budget_store=force, stored_token=event[2])]))
        records.append(dict(kind='request_end', rank=rank, request=1, slot=0,
                            prompt_length=1, steps=2, generated=2))
    response = {'prompt_token_ids': [154841], 'choices': [{'token_ids': [analysis.END, analysis.END]}]}
    return records, [response]


def test_exact_two_rank_force_and_nonforce_reconciliation():
    records, responses = fixture()
    result = analysis.reconcile(records, responses)
    assert result['status'] == 'reconciled'
    assert [c['classification'] for c in result['summaries'][0]['closes']] == [
        'budget_forced', 'sampled_without_budget_force']


@pytest.mark.parametrize('damage', ['rank', 'end', 'force', 'history', 'response', 'disagreement'])
def test_incomplete_or_conflicting_trace_is_not_attribution(damage):
    records, responses = copy.deepcopy(fixture())
    if damage == 'rank': records = records[:4]
    elif damage == 'end': records.pop()
    elif damage == 'force': records[2]['force_rows'][0][0] = -1
    elif damage == 'history': records[2]['before_budget']['total_len'] = 20
    elif damage == 'response': responses[0]['choices'][0]['token_ids'][1] = 42
    elif damage == 'disagreement': records[6]['after_budget']['cache'][1] = 1
    with pytest.raises(ValueError):
        analysis.reconcile(records, responses)


def test_invalid_and_truncated_receipts_fail():
    with pytest.raises(ValueError):
        analysis.extract(b'ANVIL_MARKER_TRACE {"schema":"marker-trace/v1","kind":"invalid"}\n')
    with pytest.raises(ValueError):
        analysis.extract(b'ANVIL_MARKER_TRACE {"schema":')


def test_boundary_metadata_and_uncommitted_force_rows_are_required():
    records, responses = fixture()
    del records[1]["boundary_metadata"]
    with pytest.raises(KeyError):
        analysis.reconcile(records, responses)
    records, responses = fixture()
    step = records[1]
    step["force_rows"].append([-1] * 8)
    step["local_positions"] = [0, 1]
    step["expanded_mapping"] = [0, 0]
    step["effective_positions"] = [1, 2]
    with pytest.raises(ValueError, match="speculative GPU"):
        analysis.reconcile(records, responses)


def test_startup_selftest_is_required_and_bounded():
    import json
    receipt = dict(schema="marker-trace/v1", kind="gpu_selftest", passed=True, steps=2,
                   original_logits_and_cache_equal=True, peak_tensor_bytes=1024)
    line = (analysis.PREFIX + json.dumps(receipt) + "\n").encode()
    assert analysis.validate_startup(line) == receipt
    for invalid in (b"", line + line, line.replace(b"1024", b"999999999")):
        with pytest.raises(ValueError):
            analysis.validate_startup(invalid)


def test_observed_boundary_truncation_reconciles_both_ranks():
    records, responses = fixture()
    for index in (1, 5):
        step = records[index]
        step.update(boundary_enabled=True, boundary_metadata=[1, 1, 0, 1, 513, 1, 0],
                    boundary_stop_tokens=[analysis.END], boundary_capture=[[[0, 0, 0, 0]]] * 3,
                    selected_tokens=[analysis.END, 42], verified_tokens=[analysis.END, 42],
                    verified_count=2, counts_before_commit=[2, 0], counts_after_commit=[1, 1],
                    local_positions=[0, 1], expanded_mapping=[0, 0], effective_positions=[1, 2])
        step['force_rows'].append([1, 0, -1, 0, 1, 2, 1, -1])
    assert analysis.reconcile(records, responses)['status'] == 'reconciled'
    records[1]['counts_after_commit'][1] = 0
    with pytest.raises(ValueError, match='accounting'):
        analysis.reconcile(records, responses)
