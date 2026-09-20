"""Parse every shipped module so one broken view cannot prevent application boot."""

from pathlib import Path
import shutil
import subprocess

import pytest

STATIC = Path(__file__).parents[2] / "anvil_serving/observability/dashboard/static"


@pytest.mark.parametrize(
    "source", sorted(STATIC.rglob("*.js")), ids=lambda source: str(source.relative_to(STATIC))
)
def test_shipped_esmodule_parses(source, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the frontend syntax gate")
    module = tmp_path / "module.mjs"
    module.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run(
        [node, "--check", module],
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, f"{source.relative_to(STATIC)}: {result.stderr}"


def test_workbench_only_pairs_one_closed_operation_and_benchmark_reference(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the frontend behavior gate")
    source = (STATIC / "views" / "workbench.js").read_text(encoding="utf-8")
    for name in ("common", "api", "operations", "experiments"):
        source = source.replace(f'./{name}.js', f'./{name}.mjs')
    (tmp_path / "workbench.mjs").write_text(source, encoding="utf-8")
    (tmp_path / "common.mjs").write_text(
        "export const badge=()=>null,button=()=>null,el=()=>null,empty=()=>null,heading=()=>null,"
        "kv=()=>null,notice=()=>null,route=()=>'',select=()=>null,table=()=>null,timestamp=()=>null;",
        encoding="utf-8",
    )
    (tmp_path / "api.mjs").write_text("export const query=()=>'',request=async()=>({});", encoding="utf-8")
    (tmp_path / "operations.mjs").write_text(
        "export const evidenceDialog=()=>{},metadataDialog=()=>{},openOperation=()=>{};", encoding="utf-8"
    )
    (tmp_path / "experiments.mjs").write_text("export const experimentsView=async()=>null;", encoding="utf-8")
    script = """
import assert from 'node:assert/strict';
import {coalesceRuns} from './workbench.mjs';
const key = 'benchmark-job-' + 'a'.repeat(64);
const row = (source, id, correlation) => ({id, source, native_id: id, correlation_id: correlation, updated_at: '2026-09-19T00:00:00Z'});
const states = new Map([
  ['operations', {source: {id: 'operations'}, items: [row('operations', 'operation', key), row('operations', 'bare-operation', 'context-001')]}],
  ['benchmark', {source: {id: 'benchmark'}, items: [row('benchmark', 'benchmark', key), row('benchmark', 'bare-benchmark', 'context-001')]}],
  ['workspace', {source: {id: 'workspace-tasks'}, items: [row('workspace-tasks', 'task', key)]}],
]);
const rows = coalesceRuns(states);
assert.equal(rows.length, 4);
assert.equal(rows.filter((item) => item.representations.length === 2).length, 1);
assert.equal(rows.filter((item) => item.representations.length === 1).length, 3);
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", script],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
