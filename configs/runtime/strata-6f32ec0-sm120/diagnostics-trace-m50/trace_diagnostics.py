"""Extend pinned phase parsing with fixed activation and numeric trace records."""
import json
import re

from anvil_diagnostics_base import MAX_LINE, parse_timing

COUNT = r'([0-9]{1,12})'
MS = r'([0-9]{1,12}\.[0-9])'
LENT = re.compile(r'strata trace: lent ' + COUNT + r' slots for ' + COUNT + r' tokens in ' + MS + r' ms')
REFILLED = re.compile(r'strata trace: refilled ' + COUNT + r' slots on ' + COUNT + r' stage\(s\) in ' + MS + r' ms')
READ = re.compile(r'strata trace: read ' + COUNT + r' tokens \((windows|batched)\) in ' + MS + r' ms')
FUSED = 'strata: prompt experts on the fused int8 kernels (STRATA_PF_FUSED=1, #136)'


def parse_diagnostic(line):
    if len(line) > MAX_LINE:
        return None
    original = parse_timing(line)
    if original is not None:
        return original
    text = line.removesuffix('\n').removesuffix('\r')
    if text == FUSED:
        return {'event': 'anvil_strata_fused_activation', 'observed_at_least_one_layer': True}
    match = LENT.fullmatch(text)
    if match:
        return {'event': 'anvil_strata_prefill_lent', 'slots': int(match[1]),
                'tokens': int(match[2]), 'elapsed_ms': float(match[3])}
    match = REFILLED.fullmatch(text)
    if match:
        return {'event': 'anvil_strata_prefill_refilled', 'slots': int(match[1]),
                'stages': int(match[2]), 'elapsed_ms': float(match[3])}
    match = READ.fullmatch(text)
    if match:
        return {'event': 'anvil_strata_prefill_read', 'tokens': int(match[1]),
                'path': match[2], 'elapsed_ms': float(match[3])}
    return None


def emit_diagnostic(line):
    record = parse_diagnostic(line)
    if record is not None:
        print(json.dumps(record, sort_keys=True), flush=True)
