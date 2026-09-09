"""Bounded, local-owner NCCL collectives in an existing pinned runtime image.

The host stays stdlib-only. PyTorch is supplied by the explicitly selected
container image; no model, package, image or credential is downloaded/mounted.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import uuid

from .benchmarking.artifacts import atomic_write_json
from .guard import confirmation_authorized


SOURCE = Path(__file__).with_name('_nccl_probe') / 'collectives.py.txt'
GPU_UUID = re.compile(r'GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
IMAGE = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}')
DOCKER = ['docker', '--host', 'unix:///var/run/docker.sock']
LABEL = 'io.anvil-serving.nccl-probe'
MARKER = 'ANVIL_NCCL_RESULT '
MAX_LOG_CHARS = 256 * 1024


def _run(argv, *, timeout=15, _runner=subprocess.run):
    env = os.environ.copy()
    # Pin the local system daemon even when the caller selected a remote context.
    env.pop('DOCKER_CONTEXT', None)
    env['DOCKER_HOST'] = 'unix:///var/run/docker.sock'
    return _runner(argv, capture_output=True, text=True, timeout=timeout, env=env)


def _checked(argv, *, _runner=subprocess.run):
    result = _run(argv, _runner=_runner)
    if result.returncode:
        raise ValueError(f'{argv[0]} inspection failed: {result.stderr[:2000]}')
    return result.stdout


def inventory(gpu_uuids, *, _runner=subprocess.run):
    """Fail closed on unavailable inventory, active compute or display devices."""
    raw = _checked([
        'nvidia-smi', '--query-gpu=uuid,name,pci.bus_id,memory.total,memory.used,display_active,driver_version',
        '--format=csv,noheader,nounits',
    ], _runner=_runner)
    rows = {}
    for values in csv.reader(raw.splitlines()):
        if len(values) != 7:
            raise ValueError('malformed GPU inventory')
        ident, name, bus, total, used, display, driver = [v.strip() for v in values]
        rows[ident] = dict(uuid=ident, name=name, pci_bus_id=bus, total_mib=int(total),
                           used_mib=int(used), display_active=display, driver=driver)
    if any(ident not in rows for ident in gpu_uuids):
        raise ValueError('requested UUID pair is not visible on the local owner')
    processes = _checked([
        'nvidia-smi', '--query-compute-apps=gpu_uuid,pid', '--format=csv,noheader,nounits',
    ], _runner=_runner)
    busy = set()
    for values in csv.reader(processes.splitlines()):
        if len(values) != 2 or not values[1].strip().isdigit():
            raise ValueError('malformed compute-process inventory')
        busy.add(values[0].strip())
    selected = [rows[ident] for ident in gpu_uuids]
    blockers = []
    for row in selected:
        if row['uuid'] in busy or row['used_mib'] > 512:
            blockers.append(f"{row['uuid']} is occupied; unload its managed owner first")
        if row['display_active'] != 'Disabled':
            blockers.append(f"{row['uuid']} is not a verified non-display device")
        if row['total_mib'] - row['used_mib'] < 2048:
            blockers.append(f"{row['uuid']} has less than 2048 MiB free")
    return selected, blockers


def _iommu_domains(devices):
    result = {}
    for row in devices:
        # NVIDIA prints an eight-digit domain; Linux sysfs uses four digits.
        domain, bus, dev = str(row['pci_bus_id']).split(':')
        bdf = f'{int(domain, 16):04x}:{bus.lower()}:{dev.lower()}'
        device = Path('/sys/bus/pci/devices') / bdf
        group = device / 'iommu_group'
        result[row['uuid']] = ('unknown' if not device.is_dir() else
                               (group / 'type').read_text().strip()
                               if group.is_symlink() or group.exists() else 'none')
    return result


def _cleanup(name, run_id, *, _runner=subprocess.run):
    """Remove only a container carrying this invocation's unpredictable label."""
    info = _run(DOCKER + ['inspect', '--format', '{{json .Config.Labels}}', name],
                _runner=_runner)
    if info.returncode:
        if 'no such object' in info.stderr.lower() or 'no such container' in info.stderr.lower():
            return {'ok': True, 'state': 'absent'}
        return {'ok': False, 'error': info.stderr[:2000]}
    labels = json.loads(info.stdout)
    if not isinstance(labels, dict) or labels.get(LABEL) != run_id:
        return {'ok': False, 'error': 'cleanup refused: container ownership label differs'}
    result = _run(DOCKER + ['rm', '--force', name], _runner=_runner)
    if result.returncode:
        return {'ok': False, 'error': result.stderr[:2000]}
    verify = _run(DOCKER + ['inspect', '--format', '{{.Id}}', name], _runner=_runner)
    absent = verify.returncode != 0 and (
        'no such object' in verify.stderr.lower() or 'no such container' in verify.stderr.lower())
    return {'ok': absent, 'state': 'removed' if absent else 'removal-unverified',
            'verification_exit_code': verify.returncode, 'verification_detail': verify.stderr[:2000]}


def probe(*, image, gpu_uuids, p2p='disabled', cumem=0, max_mib=16, iterations=20,
          timeout=90, dry_run=True, _runner=subprocess.run, _domains=_iommu_domains):
    if (platform.system() != 'Linux' or Path('/.dockerenv').exists() or
            any(value in platform.release().lower() for value in ('microsoft', 'wsl'))):
        raise ValueError('this probe requires the native Linux resource owner')
    if not IMAGE.fullmatch(image):
        raise ValueError('image must be an existing repository@sha256:full-digest reference')
    if len(gpu_uuids) != 2 or len(set(gpu_uuids)) != 2 or any(
            not GPU_UUID.fullmatch(value) for value in gpu_uuids):
        raise ValueError('select exactly two distinct full GPU UUIDs')
    if p2p not in ('enabled', 'disabled') or cumem not in (0, 1):
        raise ValueError('invalid P2P or cuMem mode')
    if not 1 <= max_mib <= 64 or not 1 <= iterations <= 100 or not 30 <= timeout <= 180:
        raise ValueError('bounds: max_mib 1..64, iterations 1..100, timeout 30..180 seconds')
    image_id = json.loads(_checked(DOCKER + ['image', 'inspect', '--format', '{{json .Id}}', image],
                                  _runner=_runner))
    if not isinstance(image_id, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
        raise ValueError('image inspection did not resolve an immutable local image ID')
    devices, blockers = inventory(gpu_uuids, _runner=_runner)
    domains = _domains(devices)
    if p2p == 'enabled' and any(mode not in ('none', 'identity') for mode in domains.values()):
        blockers.append('P2P requires a non-translated GPU IOMMU path on bare-metal Linux')
    source = SOURCE.read_text(encoding='utf-8')
    run_id = uuid.uuid4().hex
    name = 'anvil-nccl-' + run_id
    environment = {
        'CUDA_VISIBLE_DEVICES': ','.join(gpu_uuids), 'CUDA_DEVICE_ORDER': 'PCI_BUS_ID',
        'NCCL_P2P_DISABLE': '1' if p2p == 'disabled' else '0', 'NCCL_P2P_LEVEL': 'SYS',
        'NCCL_CUMEM_ENABLE': str(cumem), 'NCCL_DEBUG': 'INFO',
        'NCCL_DEBUG_SUBSYS': 'INIT,GRAPH,P2P,SHM', 'NCCL_IB_DISABLE': '1',
        'NCCL_SOCKET_IFNAME': '=lo', 'NCCL_SOCKET_FAMILY': 'AF_INET',
        'OMP_NUM_THREADS': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'TORCH_NCCL_ASYNC_ERROR_HANDLING': '1',
    }
    path = '/tmp/anvil-nccl.py'
    bootstrap = (f'from pathlib import Path; import os,sys; Path({path!r}).write_text({source!r}); '
                 f'os.execv(sys.executable, [sys.executable, {path!r}, {str(max_mib)!r}, '
                 f'{str(iterations)!r}])')
    command = DOCKER + [
        'create', '--name', name, '--label', f'{LABEL}={run_id}', '--pull', 'never',
        '--gpus', '"device=' + ','.join(gpu_uuids) + '"', '--network', 'none',
        '--ipc', 'private', '--shm-size', '256m', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--memory', '4g', '--cpus', '4',
        '--pids-limit', '256', '--tmpfs', '/tmp:rw,nosuid,size=128m',
        '--ulimit', 'memlock=-1', '--entrypoint', 'python3',
    ]
    for key, value in environment.items():
        command += ['--env', f'{key}={value}']
    command += [image_id, '-c', bootstrap]
    artifact = dict(schema='anvil-serving.nccl-probe/v1',
                    observed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    image=image, image_id=image_id, gpu_uuids=list(gpu_uuids), devices=devices,
                    iommu_domains=domains, p2p=p2p, environment=environment,
                    max_mib=max_mib, iterations=iterations, timeout_seconds=timeout,
                    source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                    container=name, executed=False, blockers=blockers, ok=not blockers,
                    promoted=False, dry_run=dry_run, topology=_checked(
                        ['nvidia-smi', 'topo', '-m'], _runner=_runner))
    if dry_run:
        artifact['plan'] = dict(temporary_container=True, model_weights_loaded=False,
                                downloads=False, host_mounts=False, cleanup='exact-owned-container',
                                memory_limit_mib=4096, max_buffer_mib=max_mib,
                                network='none', local_docker_socket='/var/run/docker.sock')
        return artifact
    if blockers:
        return artifact
    # Re-read immediately before creating any GPU workload; never evict an owner.
    _, blockers = inventory(gpu_uuids, _runner=_runner)
    if blockers:
        return {**artifact, 'ok': False, 'blockers': blockers}
    artifact['executed'] = True
    try:
        created = _run(command, timeout=30, _runner=_runner)
        if created.returncode:
            raise ValueError('probe container creation failed: ' + created.stderr[:2000])
        started = _run(DOCKER + ['start', '--attach', name], timeout=timeout, _runner=_runner)
        artifact['exit_code'] = started.returncode
    except subprocess.TimeoutExpired:
        artifact['error'] = 'bounded NCCL probe timed out'
    except (ValueError, OSError) as exc:
        artifact['error'] = str(exc)
    finally:
        try:
            logs = _run(DOCKER + ['logs', '--tail', '1500', name], _runner=_runner)
            combined = logs.stdout + '\n' + logs.stderr
            artifact['logs'] = combined[-MAX_LOG_CHARS:]
            artifact['logs_truncated'] = len(combined) > MAX_LOG_CHARS
            state = _run(DOCKER + ['inspect', '--format', '{{json .State}}', name], _runner=_runner)
            artifact['container_state'] = json.loads(state.stdout) if state.returncode == 0 else None
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            artifact['log_error'] = str(exc)
        try:
            artifact['cleanup'] = _cleanup(name, run_id, _runner=_runner)
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            artifact['cleanup'] = {'ok': False, 'error': str(exc)}
    result_lines = [line[len(MARKER):] for line in artifact.get('logs', '').splitlines()
                    if line.startswith(MARKER)]
    try:
        result = json.loads(result_lines[0]) if len(result_lines) == 1 else None
    except ValueError:
        result = None
    artifact['result'] = result
    # The PyTorch build version can differ from the dynamically loaded NCCL.
    artifact['nccl_runtime_versions'] = sorted(set(re.findall(
        r'NCCL INFO NCCL version (\S+)', artifact.get('logs', ''))))
    artifact['p2p_transport_observed'] = 'via P2P' in artifact.get('logs', '')
    artifact['shm_transport_observed'] = 'via SHM' in artifact.get('logs', '')
    ranks = result.get('ranks', []) if isinstance(result, dict) else []
    expected_sizes = sorted(set([8, 1024, 65536, max_mib * 1024 * 1024]))
    identity_ok = isinstance(ranks, list) and len(ranks) == 2 and all(
        isinstance(row, dict) and row.get('rank') == rank and
        row.get('uuid') == gpu_uuids[rank] for rank, row in enumerate(ranks))
    measurements_ok = identity_ok and all(
        isinstance(row.get('measurements'), list) and
        [entry.get('bytes') for entry in row['measurements'] if isinstance(entry, dict)] == expected_sizes and
        len(row['measurements']) == len(expected_sizes) and all(
            entry.get('correct') is True and entry.get('iterations') == iterations and
            type(entry.get('median_ms')) in (int, float) and
            math.isfinite(entry['median_ms']) and entry['median_ms'] > 0
            for entry in row['measurements']) for row in ranks)
    directions = set(re.findall(r'([01])\[[^\]]+\] -> ([01])\[[^\]]+\] via P2P',
                                artifact.get('logs', '')))
    transport_ok = (not artifact['p2p_transport_observed'] if p2p == 'disabled' else
                    directions == {('0', '1'), ('1', '0')} and
                    not artifact['shm_transport_observed'])
    artifact['ok'] = bool(
        artifact.get('exit_code') == 0 and not artifact.get('error') and
        artifact.get('cleanup', {}).get('ok') and measurements_ok and
        all(row.get('correct') is True for row in ranks) and
        transport_ok)
    if not artifact['ok'] and 'error' not in artifact:
        artifact['error'] = 'collective correctness, exact rank identity, transport, exit or cleanup gate failed'
    return artifact


def build_parser():
    parser = argparse.ArgumentParser(prog='anvil-serving host nccl')
    parser.add_argument('--image', required=True, help='Existing repository@sha256:digest image with PyTorch/NCCL')
    parser.add_argument('--gpu-uuid', action='append', required=True, help='Exact GPU UUID; repeat twice in rank order')
    parser.add_argument('--p2p', choices=('disabled', 'enabled'), default='disabled')
    parser.add_argument('--cumem', type=int, choices=(0, 1), default=0)
    parser.add_argument('--max-mib', type=int, default=16)
    parser.add_argument('--iterations', type=int, default=20)
    parser.add_argument('--timeout', type=int, default=90)
    parser.add_argument('--output', help='Private JSON artifact path (required for confirmed execution)')
    parser.add_argument('--dry-run', action='store_true')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    dry_run = args.dry_run or not confirmation_authorized()
    if not dry_run and (not args.output or not Path(args.output).expanduser().parent.is_dir()):
        parser.error('confirmed execution requires --output in an existing private directory')
    try:
        result = probe(image=args.image, gpu_uuids=args.gpu_uuid, p2p=args.p2p,
                       cumem=args.cumem, max_mib=args.max_mib, iterations=args.iterations,
                       timeout=args.timeout, dry_run=dry_run)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        result = {'schema': 'anvil-serving.nccl-probe/v1', 'ok': False, 'error': str(exc),
                  'dry_run': dry_run}
    if not dry_run:
        atomic_write_json(args.output, result)
    summary = {key: value for key, value in result.items() if key not in ('logs', 'topology')}
    summary['artifact'] = args.output if not dry_run else None
    print(json.dumps(summary, indent=2))
    return 0 if result['ok'] else 1
