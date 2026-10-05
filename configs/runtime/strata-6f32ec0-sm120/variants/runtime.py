"""Select one baked Strata experiment without modifying the verified pack."""
import argparse
import json
import os
from pathlib import Path
import sys

import prepare as base

CONFIG_DIR = Path('/opt/strata/anvil/runtime-configs')
CGROUP = Path('/sys/fs/cgroup')
NAMES = ('32k-c1-r36', '128k-c1-r36', '128k-c4-r36', '262k-c1-r36', '128k-c1-r48')


def selected_config(name):
    if name not in NAMES:
        raise ValueError('Unknown baked runtime configuration')
    manifest = json.loads((CONFIG_DIR / 'manifest.json').read_text())
    if manifest.get('schema') != 'anvil-strata-runtime-configs/v1' or set(manifest['configs']) != set(NAMES):
        raise ValueError('Invalid baked runtime configuration manifest')
    row = manifest['configs'][name]
    path = CONFIG_DIR / (name + '.json')
    base.verify(path, row['bytes'], row['sha256'])
    return path, row, json.loads(path.read_text())


def check_runtime_memory(row):
    # Each experiment is coupled to one managed hard ceiling and no swap.
    # Preparation-only retains its separate 16GiB managed recipe.
    maximum = (CGROUP / 'memory.max').read_text().strip()
    swap = (CGROUP / 'memory.swap.max').read_text().strip()
    if maximum != str(row['required_ram_limit_mib'] * 1024 * 1024) or swap != '0':
        raise ValueError('Runtime configuration requires its exact managed RAM ceiling and zero swap')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--runtime-config', choices=NAMES, default=NAMES[0])
    args = parser.parse_args(argv)
    path, row, cfg = selected_config(args.runtime_config)
    if not args.prepare_only:
        check_runtime_memory(row)
    base.DATA.mkdir(exist_ok=True)
    with (base.DATA / '.preparation.lock').open('a') as lock:
        base.fcntl.flock(lock, base.fcntl.LOCK_EX | base.fcntl.LOCK_NB)
        base.prepare()
        receipt = json.loads((base.FINAL / 'preparation.json').read_text())
        original = json.loads((base.FINAL / 'config.json').read_text())
        print(json.dumps({'event': 'anvil_strata_preparation_verified',
                          'preparation_receipt': receipt, 'original_config': original,
                          'runtime_config_name': args.runtime_config,
                          'runtime_config_sha256': row['sha256'], 'runtime_config': cfg},
                         sort_keys=True), flush=True)
    if args.prepare_only:
        return
    os.chdir(base.ROOT)
    os.execv(sys.executable, [sys.executable, '-m', 'serve.server', '--engine', 'strata',
                            '--config', str(path), '--port', str(cfg['port'])])


if __name__ == '__main__':
    main()
