"""Read bounded Buildx history metadata from one explicitly bound builder."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
import subprocess
from pathlib import Path

from .reasoning_probe import _json, _read_bytes
from .transports import _ProcessOutputOverflow, _ProcessPipeError, _run_bounded_process

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_REF = re.compile(r"[a-z0-9]{25}\Z")
_TIME = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)\Z")
_LIST_FORMAT = '{"id":{{json .ID}},"status":{{json .Status}},"created_at":{{json .CreatedAt}},"duration":{{json .Duration}}}'
_DETAIL_FORMAT = '{"ref":{{json .Ref}},"status":{{json .Status}},"started_at":{{json .StartedAt}},"completed_at":{{json .CompletedAt}},"completed_steps":{{json .NumCompletedSteps}},"total_steps":{{json .NumTotalSteps}},"duration":{{json .Duration}}}'


def _time_ns(value, *, utc=False):
    if not isinstance(value, str) or (match := _TIME.fullmatch(value)) is None or utc and match[3] != 'Z':
        raise ValueError('invalid timestamp')
    try:
        dt = datetime.fromisoformat(match[1] + ('+00:00' if match[3] == 'Z' else match[3]))
        seconds = int((dt.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds())
    except (ValueError, OverflowError) as exc:
        raise ValueError('invalid timestamp') from exc
    return seconds * 1_000_000_000 + int((match[2] or '').ljust(9, '0'))


def _plan(path):
    plan = _json(_read_bytes(Path(path)))
    required = {'schema', 'context', 'builder', 'expected_engine_id', 'started_after', 'started_before'}
    if not required <= set(plan) or set(plan) - required - {'ref'} or plan['schema'] != 'anvil-serving.build-history-plan/v1':
        raise ValueError('invalid plan')
    if any(not isinstance(plan[k], str) or not _NAME.fullmatch(plan[k]) for k in ('context', 'builder')):
        raise ValueError('invalid context or builder')
    engine = plan['expected_engine_id']
    if not isinstance(engine, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}', engine):
        raise ValueError('invalid engine identity')
    window = _time_ns(plan['started_before'], utc=True) - _time_ns(plan['started_after'], utc=True)
    if not 0 < window <= 7 * 86400 * 1_000_000_000:
        raise ValueError('invalid time window')
    if 'ref' in plan and (not isinstance(plan['ref'], str) or not _REF.fullmatch(plan['ref'])):
        raise ValueError('invalid build reference')
    return plan


def inspect_history(plan_path, *, runner=_run_bounded_process):
    plan = _plan(plan_path)
    def run(args):
        completed = runner(['docker', '--context', plan['context'], *args], timeout=30, max_output_bytes=1024 * 1024)
        if completed.returncode:
            raise ValueError('Docker history operation unavailable')
        return completed.stdout.decode('utf-8')
    def binding():
        lines = run(['buildx', 'ls', '--format', '{{json .}}']).splitlines()
        if len(lines) > 256:
            raise ValueError('builder inventory exceeds bound')
        rows = [_json(line.encode()) for line in lines if line.strip()]
        found = [row for row in rows if row.get('Name') == plan['builder']]
        if not found or len({json.dumps(row, sort_keys=True) for row in found}) != 1:
            raise ValueError('missing or ambiguous builder')
        row = found[0]
        nodes = row.get('Nodes')
        if (row.get('Driver') != 'docker' or not isinstance(nodes, list) or len(nodes) != 1
                or not isinstance(nodes[0], dict) or nodes[0].get('Endpoint') != plan['context']
                or nodes[0].get('Status') != 'running'):
            raise ValueError('builder is not a running single-node context builder')
        engine = json.loads(run(['info', '--format', '{{json .ID}}']))
        if engine != plan['expected_engine_id']:
            raise ValueError('engine identity mismatch')
        return {'engine_id': engine, 'builder': plan['builder'], 'context': plan['context'],
                'node': nodes[0]}
    first = binding()
    prefix = ['buildx', '--builder', plan['builder'], 'history']
    lines = run([*prefix, 'ls', '--no-trunc', '--filter', 'startedAt>' + plan['started_after'],
                 '--filter', 'startedAt<' + plan['started_before'], '--format', _LIST_FORMAT]).splitlines()
    if not lines or len(lines) > 256:
        raise ValueError('missing or excessive history records')
    after, before = _time_ns(plan['started_after'], utc=True), _time_ns(plan['started_before'], utc=True)
    records, ids = [], set()
    for line in lines:
        row = _json(line.encode())
        if set(row) != {'id', 'status', 'created_at', 'duration'} or not isinstance(row['id'], str) or not _REF.fullmatch(row['id']) or row['id'] in ids:
            raise ValueError('invalid or duplicate history record')
        if (not isinstance(row['status'], str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', row['status'])
                or not after < _time_ns(row['created_at']) < before):
            raise ValueError('invalid history record status or timestamp')
        if not _duration(row['duration']):
            raise ValueError('invalid history duration')
        ids.add(row['id'])
        records.append(row)
    detail = None
    if 'ref' in plan:
        if plan['ref'] not in ids:
            raise ValueError('requested record missing from window')
        detail = _json(run([*prefix, 'inspect', '--format', _DETAIL_FORMAT, plan['ref']]).encode())
        if (set(detail) != {'ref', 'status', 'started_at', 'completed_at', 'completed_steps', 'total_steps', 'duration'}
                or detail['ref'] != plan['ref'] or not isinstance(detail['status'], str)
                or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', detail['status'])
                or not after < _time_ns(detail['started_at']) < before
                or _time_ns(detail['completed_at']) < _time_ns(detail['started_at'])
                or any(type(detail[k]) is not int or not 0 <= detail[k] <= 1_000_000 for k in ('completed_steps', 'total_steps'))
                or detail['completed_steps'] > detail['total_steps'] or not _duration(detail['duration'])):
            raise ValueError('invalid history detail')
        selected = next(row for row in records if row['id'] == plan['ref'])
        if detail['status'].lower() != selected['status'].lower():
            raise ValueError('history record status changed')
    if binding() != first:
        raise ValueError('builder or engine changed')
    return {'schema': 'anvil-serving.build-history/v1', 'context': plan['context'], 'builder': plan['builder'],
            'engine_id': first['engine_id'], 'started_after': plan['started_after'], 'started_before': plan['started_before'],
            'records': records, 'detail': detail,
            'limitation': 'history_metadata_does_not_prove_builder_quiescence_or_exclusive_ownership'}


def _duration(value):
    return (type(value) is int and 0 <= value <= 9223372036854775807 or
            isinstance(value, str) and len(value) <= 64 and bool(re.fullmatch(
                r'(?:0|(?:[0-9]+(?:\.[0-9]+)?(?:h|ms|us|µs|μs|ns|m|s))+)', value)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = inspect_history(args.plan)
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError,
            _ProcessOutputOverflow, _ProcessPipeError) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__}))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
