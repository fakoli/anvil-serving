"""Small offline regression gate, including python -O evidence rejection."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).parent


class AnalysisChecks(unittest.TestCase):
    def run_optimized(self, script, args):
        return subprocess.run([sys.executable, "-O", str(ROOT / script), *map(str, args)],
                              capture_output=True, text=True, timeout=10)

    def test_incomplete_capture_rejected_under_optimization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = json.loads((ROOT / 'instrumented-scheduler-pilot-c4-trace.json').read_text())
            value['complete'] = False
            source, output = root / 'trace.json', root / 'output.json'
            source.write_text(json.dumps(value))
            result = self.run_optimized('analyze-scheduler.py', [source, '--output', output])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('incomplete capture', result.stderr)
            self.assertFalse(output.exists())

    def test_counter_reset_rejected_under_optimization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stem = 'flare-control-c1-prime'
            (root / (stem + '.json')).write_text(json.dumps({'completed': 1, 'failed': 0, 'requests': 1}))
            (root / (stem + '-before-native.prom')).write_text('flare_requests_completed_total 0\n')
            (root / (stem + '-after-native.prom')).write_text('flare_requests_completed_total -1\n')
            output = root / 'output.json'
            result = self.run_optimized('analyze-native.py', ['flare-control', '--root', root, '--output', output])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('counter reset', result.stderr)
            self.assertFalse(output.exists())

    def test_missing_prior_observation_and_per_rank_outputs(self):
        spec = importlib.util.spec_from_file_location('scheduler_analysis', ROOT / 'analyze-scheduler.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            value = json.loads((ROOT / 'instrumented-scheduler-pilot-c4-trace.json').read_text())
            for sample in value['trace']['samples']:
                sample['selection'].update(observed_at_ns=None, retained_ns=0, freshness='Unknown', origin='Discovery')
            path = Path(directory) / 'trace.json'
            path.write_text(json.dumps(value))
            group = module.analyze(path)['groups'][0]
            self.assertEqual(group['missing_prior_observations'], 44)
            self.assertEqual(group['metrics']['prior_observation_age_ms']['n'], 0)
            self.assertEqual(group['metrics']['measured_cost_over_retained']['n'], 0)
            self.assertEqual(group['metrics']['rank_0_gpu_ms']['n'], 44)
            self.assertEqual(group['metrics']['rank_1_gpu_ms']['n'], 44)


if __name__ == '__main__':
    unittest.main()
