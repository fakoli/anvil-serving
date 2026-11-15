"""Deadline and partial-evidence custody for the HTTP-only context fixture."""
import datetime
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import time

root = pathlib.Path(__file__).parent
fixture = root / "concurrent-context-high-workload.py"
spec = importlib.util.spec_from_file_location("context_fixture", fixture)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
output = pathlib.Path(sys.argv[1])
module.require(output.parent == root and not output.exists(), "new fixture output required")
receipt_path = output.with_suffix(".command.json")
module.reserve(receipt_path)
cmd = [sys.executable, str(fixture), str(output), sys.argv[2]]
receipt = {"argv": cmd, "script_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
           "wrapper_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
           "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "outer_timeout_s": 420}
started = time.monotonic()
try:
    with output.with_suffix(".stdout").open("x") as stdout, output.with_suffix(".stderr").open("x") as stderr:
        receipt["returncode"] = subprocess.run(cmd, stdout=stdout, stderr=stderr, timeout=420).returncode
except subprocess.TimeoutExpired:
    receipt["timeout"] = True
finally:
    receipt["drain_samples"] = []
    until = time.monotonic() + 30
    while time.monotonic() < until:
        try:
            observed = module.metrics()
            receipt["drain_samples"].append({"at_ns": time.monotonic_ns(), "metrics": observed})
            if observed["active"] == observed["pending"] == 0:
                receipt["drained"] = True
                break
        except Exception as exc:
            receipt["drain_samples"].append({"at_ns": time.monotonic_ns(), "error": str(exc)[:1024]})
        time.sleep(1)
    receipt.setdefault("drained", False)
    receipt["elapsed_s"] = time.monotonic() - started
    if output.exists() and output.stat().st_size == 0:
        journal = output.with_suffix(".journal.jsonl")
        lines = journal.read_text().splitlines(keepends=True) if journal.exists() else []
        records = []
        for line in lines:
            if not line.endswith("\n"):
                break
            try:
                records.append(json.loads(line))
            except ValueError:
                break
        partial = {"schema": "flare-concurrent-context-diagnostic-result/v1", "partial": True,
                   "reason": "fixture terminated before final artifact", "records": records, "promoted": False}
        module.atomic_write_json(output, partial)
    module.atomic_write_json(receipt_path, receipt)
print(json.dumps({k: receipt.get(k) for k in ["returncode", "timeout", "drained", "elapsed_s"]}), flush=True)
raise SystemExit(0 if receipt.get("returncode") == 0 and receipt["drained"] else 1)
