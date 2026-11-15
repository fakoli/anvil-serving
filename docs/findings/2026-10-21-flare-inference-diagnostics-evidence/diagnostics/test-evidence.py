"""Small negative checks for offline evidence assembly; no model calls."""
import importlib.util
import json
import tempfile
import subprocess
import sys
import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).parent


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class EvidenceChecks(unittest.TestCase):
    def test_gpu_missing_window_duplicate_and_analyzer_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'analyze-gpu.py'
            script.write_bytes((ROOT / script.name).read_bytes())
            arms = ('incumbent-before', 'flare-control', 'flare-instrumented', 'incumbent-after')
            payload = {'status': 'success', 'data': {'result': [
                {'metric': {'__name__': 'nvidia_smi_power_limit_watts', 'host': 'primary-node', 'gpu_uuid': gpu},
                 'values': [[15, '300']]} for gpu in ('one', 'two')]}}
            for arm in arms:
                (root / (arm + '-prometheus-gpu-range.json')).write_text(json.dumps(payload))
                for c in (1, 4):
                    for r in (1, 2, 3):
                        (root / f'{arm}-c{c}-r{r}.json').write_text(json.dumps({
                            'started_at_unix_ns': (r * 20 - 10) * 10**9,
                            'finished_at_unix_ns': r * 20 * 10**9}))
            result = subprocess.run([sys.executable, '-O', str(script)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = root / 'gpu-observations.json'
            data = json.loads(output.read_text())
            self.assertEqual(data['analyzer_sha256'], hashlib.sha256(script.read_bytes()).hexdigest())
            self.assertTrue(any(x['metric'] == 'power_limit_watts' and x['window'].endswith('r2')
                                for x in data['missing_windows']))
            output.unlink()
            payload['data']['result'].append(payload['data']['result'][0])
            (root / (arms[0] + '-prometheus-gpu-range.json')).write_text(json.dumps(payload))
            result = subprocess.run([sys.executable, '-O', str(script)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'duplicate GPU metric series', result.stderr)
            self.assertFalse(output.exists())

    def test_counter_rejects_missing_and_nonfinite(self):
        analysis = module('analyze-incumbent')
        for raw in ('other 1', 'x{rank="0"} NaN', 'x{rank="0"} -1'):
            with self.assertRaises(ValueError):
                analysis.total(raw, 'x')
        self.assertEqual(analysis.total('x{rank="0"} 3\nx{rank="1"} 5', 'x'), 8)

    def test_identity_redaction_preserves_json(self):
        publication = module('publish-evidence')
        identity = 'a' * 64
        publication.container_ids.add(identity)
        value = {'container_id': identity, 'host': 'primary-node',
                 'path': 'OPERATOR_PATH_b6d8dee06246/config.json',
                 'uuid': 'synthetic-id-ae1908d5eef6',
                 'endpoint': 'http://100.64.0.134:1234'}
        output = publication.scrub(json.dumps(value))
        result = json.loads(output)
        for forbidden in (identity, 'primary-node', 'OPERATOR_PATH_04426f67ee44/private', value['uuid'], '100.64.0.134'):
            self.assertNotIn(forbidden, output)
        self.assertNotEqual(result['container_id'], identity)

    def test_failed_or_incomplete_receipt_creates_no_bundle(self):
        publication = module('publish-evidence')
        with tempfile.TemporaryDirectory() as directory:
            publication.E = Path(directory)
            publication.B = publication.E / 'staging'
            (publication.E / 'restoration-checks.json').write_text('{"ok":true}')
            final = publication.E / 'final-state.json'
            final.write_text('{"ok":false}')
            with self.assertRaises(ValueError):
                publication.main()
            self.assertFalse(publication.B.exists())

            final.write_text(json.dumps({'ok': True, 'candidate_absent': True,
                                         'primary_ready_admitting': True, 'sources': []}))
            with self.assertRaises(ValueError):
                publication.main()
            self.assertFalse(publication.B.exists())

    def test_references_reject_escape_duplicates_hash_and_symlink(self):
        publication = module('publish-evidence')
        with tempfile.TemporaryDirectory() as directory:
            publication.E = Path(directory)
            source = publication.E / 'safe.json'
            source.write_text('{}')
            good = {'path': 'safe.json', 'sha256': publication.sha(b'{}')}
            publication.check_references([good], {'safe.json'})
            for refs in ([good, good], [{'path': '../safe.json', 'sha256': good['sha256']}],
                         [{'path': str(source), 'sha256': good['sha256']}],
                         [{'path': 'safe.json', 'sha256': '0' * 64}]):
                with self.assertRaises(ValueError):
                    publication.check_references(refs, {'safe.json'})
            source.rename(publication.E / 'target.json')
            source.symlink_to('target.json')
            with self.assertRaises(ValueError):
                publication.check_references([good], {'safe.json'})


if __name__ == '__main__':
    unittest.main()
