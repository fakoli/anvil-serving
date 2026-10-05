"""Metadata-only diagnostics for one pinned Strata runtime; never echo log text."""
import hashlib
import json
from pathlib import Path
import re

MAX_LINE = 4096
PACK = Path('/data/prepared/pack/native_experts.txt')
PACK_BYTES = 5803
PACK_SHA256 = 'f9a8bdec38ab634fec9a31807cc3284e6497ef24c1afa0d8853127b0537c68db'
PHASES = ('embed+steps', 'hc read', 'gdn', 'qsa proj', 'qsa indexer', 'qsa select',
          'qsa attn', 'router+shared', 'host grouping', 'gather', 'wait copy', 'dequant',
          'gemm gate/up', 'gemm down', 'combine', 'ple', 'kv stage', 'gdn conv+gates',
          'gdn recurrence', 'gdn out proj')
N = r'([0-9]{1,12})'
MAIN = re.compile(r'strata prefill timing: ' + N + r' tokens, GPU timeline ' + N +
                  r' ms, wall ' + N + r' ms, host staging ' + N + r' ms:(.*)')
PHASE = re.compile(r' (' + '|'.join(re.escape(x) for x in PHASES) +
                   r') ' + N + r' \(([0-9]{1,3}\.[0-9])%\)')
HOST = re.compile(r'strata prefill timing: host: chunk setup \(PLE rows, the expert stream plan\) ' + N +
                  r' ms, waiting for each chunk ' + N +
                  r' ms, after each chunk \(the draft layer, progress\) ' + N + r' ms, PLE ' + N + r' ms')


def parse_timing(line):
    if len(line) > MAX_LINE:
        return None
    text = line.removesuffix('\n').removesuffix('\r')
    match = MAIN.fullmatch(text)
    if match:
        phases = {}
        suffix = match[5]
        at = 0
        last = -1
        while at < len(suffix):
            part = PHASE.match(suffix, at)
            if not part or PHASES.index(part[1]) <= last or float(part[3]) > 100:
                return None
            last = PHASES.index(part[1])
            phases[part[1]] = {'ms': int(part[2]), 'percent': float(part[3])}
            at = part.end()
        return {'event': 'anvil_strata_prefill_timing', 'tokens': int(match[1]),
                'gpu_timeline_ms': int(match[2]), 'wall_ms': int(match[3]),
                'host_staging_ms': int(match[4]), 'phases': phases}
    match = HOST.fullmatch(text)
    if match:
        return {'event': 'anvil_strata_prefill_host_timing', 'chunk_setup_ms': int(match[1]),
                'chunk_wait_ms': int(match[2]), 'after_chunk_ms': int(match[3]), 'ple_ms': int(match[4])}
    return None


def emit_diagnostic(line):
    record = parse_timing(line)
    if record is not None:
        print(json.dumps(record, sort_keys=True), flush=True)


def pack_metadata(data):
    if len(data) != PACK_BYTES or hashlib.sha256(data).hexdigest() != PACK_SHA256:
        raise ValueError('Pinned native expert metadata identity mismatch')
    lines = data.decode('utf-8').splitlines()
    header = re.search(r'\(n_expert ([0-9]+), total ([0-9]+);', lines[0])
    if not header or int(header[1]) != 512 or len(lines) != 49:
        raise ValueError('Unexpected pinned native expert metadata shape')
    total = 0
    counts = {}
    blobs = {}
    for layer, line in enumerate(lines[1:]):
        fields = line.split()
        if len(fields) not in (8, 9) or not all(x.isdecimal() for x in fields[:8]):
            raise ValueError('Malformed native expert metadata row')
        values = list(map(int, fields[:8]))
        if values[0] != layer or values[3] != total or values[4] <= 0:
            raise ValueError('Inconsistent native expert metadata offsets')
        total += values[4] * 512
        key = str(values[1]) + ':' + str(values[2])
        counts[key] = counts.get(key, 0) + 1
        blobs[str(layer)] = values[4]
    if total != int(header[2]):
        raise ValueError('Inconsistent native expert metadata total')
    return {'event': 'anvil_strata_expert_layout', 'sha256': PACK_SHA256,
            'layers': 48, 'experts_per_layer': 512, 'total_expert_bytes': total,
            'format_pair_layer_counts': counts, 'layer_blob_bytes': blobs}


def emit_pack_metadata():
    # Fixed prepared output only. Read at most one byte beyond its pinned length.
    with PACK.open('rb') as stream:
        data = stream.read(PACK_BYTES + 1)
    print(json.dumps(pack_metadata(data), sort_keys=True), flush=True)
