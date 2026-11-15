"""Require complete GPU receipts before attributing a native closing token."""
import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / 'configs/runtime-patches/vllm/8244d1c4-marker-trace/analyze.py'
spec = importlib.util.spec_from_file_location('marker_analysis', PATH)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def fixture():
    records = []
    for rank in (0, 1):
        records.append(dict(kind='request_start', rank=rank, request=1, slot=0,
                            prompt_length=65, budget=64, max_tokens=512))
        for number, force in ((1, 1), (2, 0)):
            position = 64 + number
            event = [1, force, analysis.END if force else -1, 0, -1 if force else 65, position, position-1, 0 if force else -1]
            state = dict(total_len=position, cache=[0, -1, number], markers={str(analysis.END): []})
            records.append(dict(kind='step', rank=rank, request=1, step=number, slot=0,
                before_budget=state, before_commit=state, after_budget=state,
                after_commit=dict(total_len=position+1), force_rows=[event],
                local_positions=[0], expanded_mapping=[0], effective_positions=[position],
                counts_before_commit=[1, 0], counts_after_commit=[1, 0], verified_count=1,
                boundary_enabled=False, boundary_metadata=None, boundary_stop_tokens=[], boundary_capture=None,
                query_start=[0, 1], logits_only=False, selected_tokens=[analysis.END], verified_tokens=[analysis.END],
                marker_logits_after=[[0, 1e9 if force else 0]],
                committed=[dict(position=position, token=analysis.END, budget_store=force, stored_token=event[2])]))
        records.append(dict(kind='request_end', rank=rank, request=1, slot=0,
                            prompt_length=65, steps=2, generated=2))
    response = {'prompt_token_ids': [154841] + [20] * 64, 'choices': [{'token_ids': [analysis.END, analysis.END]}]}
    return records, [response]
