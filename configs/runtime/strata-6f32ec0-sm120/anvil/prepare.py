"""Pinned image preparation; no network, no model start in --prepare-only mode."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path('/opt/strata')
DATA = Path('/data')
FINAL = DATA / 'prepared'
MANIFEST = ROOT / 'anvil/artifact-manifest.json'

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def verify(path, size, sha):
    if not path.is_file() or path.stat().st_size != size or digest(path) != sha:
        raise ValueError('Artifact identity mismatch: ' + str(path))

def snapshot(repo, revision):
    return Path('/root/.cache/huggingface/hub') / ('models--' + repo.replace('/', '--')) / 'snapshots' / revision

def run(*args):
    subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, check=True)

def config(main, vision):
    return {
        'exe': str(ROOT / 'engine/strata'),
        'cwd': str(ROOT),
        'args': ['--pack', str(FINAL / 'pack'), '--native', str(main),
                 '--resident-budget-gib', '36', '--expert-profile', str(ROOT / 'data/expert-profile.bin'),
                 '--expert-cache', 'auto', '--prefill', 'auto', '--spec', '4', '--spec-min-p', '0.5',
                 '--mtp', str(FINAL / 'mtp/rt'), '--max-context', '32768', '--kv', 'int8',
                 '--vision', '--vram-reserve-mib', '4096'],
        'tokenizer': str(FINAL / 'pack/tokenizer'),
        'model_name': 'qwen38-flash-next-strata-iq4xs-32k-c1',
        'log': '/tmp/strata-iq4xs.log', 'host': '0.0.0.0', 'port': 39128,
        'parallel': 1, 'gpu': 0, 'open_browser': False,
        'vision': {'exe': str(ROOT / 'engine/strata-vision'), 'mmproj': str(vision),
                   'model': str(main), 'gpu': True, 'max_tokens': 1024},
    }

def prepare():
    m = json.loads(MANIFEST.read_text())
    main_root = snapshot(m['main_repo'], m['main_revision'])
    for name, size, sha in m['main_files']:
        verify(main_root / name, size, sha)
    main = main_root / m['main_files'][0][0]
    name, size, sha = m['vision_file']
    vision = snapshot(m['vision_repo'], m['vision_revision']) / name
    verify(vision, size, sha)
    mtp_root = snapshot(m['mtp']['repo'], m['mtp']['revision'])
    for row in m['mtp']['files']:
        verify(mtp_root / row['rfilename'], row['size'], row['lfs']['sha256'])
    if FINAL.exists():
        receipt = json.loads((FINAL / 'preparation.json').read_text())
        if receipt['manifest_sha256'] != digest(MANIFEST):
            raise ValueError('Existing preparation belongs to another input manifest')
        for name, row in receipt['outputs'].items():
            verify(FINAL / name, row['bytes'], row['sha256'])
        if json.loads((FINAL / 'config.json').read_text()) != config(main, vision):
            raise ValueError('Existing config differs from this pinned profile')
        print('Existing preparation verified', flush=True)
        return
    temporary = DATA / ('preparing-' + uuid.uuid4().hex)
    temporary.mkdir()
    run('tools/iq_pack.py', '--gguf', main, '--out', temporary / 'pack', '--compat-bf16')
    # Reuse the pinned upstream range/extraction code against verified local files only.
    # There is no HTTP implementation or fallback-to-main in this preparation path.
    spec = importlib.util.spec_from_file_location('pinned_mtp', ROOT / 'tools/mtp_fetch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.REVISION != m['mtp']['revision']:
        raise ValueError('MTP source pin changed')
    index = ROOT / 'anvil/model.safetensors.index.json'
    if digest(index) != m['mtp_index_sha256']:
        raise ValueError('MTP index hash mismatch')
    allowed = {row['rfilename'] for row in m['mtp']['files']}
    def local_get(url, start=None, end=None, retries=0):
        if not url.startswith(module.REPO):
            raise ValueError('Unexpected MTP repository')
        name = url[len(module.REPO):]
        if name == 'model.safetensors.index.json':
            path = index
        elif name in allowed:
            path = mtp_root / name
        else:
            raise ValueError('Unexpected MTP file')
        with path.open('rb') as f:
            if start is not None:
                f.seek(start)
                return f.read(end - start + 1)
            return f.read()
    module.get = local_get
    module.fetch(str(temporary / 'mtp'), None)
    run('tools/mtp_pack.py', '--src', temporary / 'mtp', '--experts', 'q2_0', '--out', temporary / 'mtp/mtp-q2_0.gguf')
    run('tools/mtp_rt.py', '--gguf', temporary / 'mtp/mtp-q2_0.gguf', '--out', temporary / 'mtp/rt')
    shutil.copyfile(ROOT / 'data/draft_vocab.bin', temporary / 'mtp/rt/draft_vocab.bin')
    (temporary / 'config.json').write_text(json.dumps(config(main, vision), indent=2))
    outputs = {}
    for path in sorted(temporary.rglob('*')):
        if path.is_file():
            outputs[path.relative_to(temporary).as_posix()] = {'bytes': path.stat().st_size, 'sha256': digest(path)}
    (temporary / 'preparation.json').write_text(json.dumps({
        'schema': 'anvil-strata-preparation/v1', 'manifest_sha256': digest(MANIFEST),
        'source_revision': m['strata_revision'], 'outputs': outputs,
    }, indent=2))
    temporary.rename(FINAL)
    print('Preparation completed and committed atomically', flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-only', action='store_true')
    args, serving = parser.parse_known_args()
    DATA.mkdir(exist_ok=True)
    # One writer; shared cache inputs are only read. Unfinished work remains for diagnosis.
    with (DATA / '.preparation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prepare()
    if args.prepare_only:
        return
    os.chdir(ROOT)
    os.execv(sys.executable, [sys.executable, '-m', 'serve.server', '--engine', 'strata',
                            '--config', str(FINAL / 'config.json'), *serving])

if __name__ == '__main__':
    main()
