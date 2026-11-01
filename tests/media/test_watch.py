"""The watcher reads owned jobs and wakes its caller without resubmission."""
import json
import sys


from anvil_serving.media.watch import main, tool_data, watch

JOB = "job_" + "x" * 24


def test_watch_timeout_never_cancels_or_resubmits():
    now = [0]
    calls = []

    def call(name, args):
        calls.append(name)
        return {"job": {"id": JOB, "state": "running"}}

    result = watch(call, JOB, deadline=3, clock=lambda: now[0], sleep=lambda n: now.__setitem__(0, now[0] + n))
    assert result["event"] == "media_watch_timeout" and result["jobCanceled"] is False
    assert calls == ["media_job_status"]


def test_watch_uses_real_stdio_client_protocol_and_hides_errors(tmp_path, capsys):
    stub = tmp_path / "stub.py"
    stub.write_text('''import sys,json
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r:continue
 if r['method']=='initialize':v={}
 else:v={'structuredContent':{'ok':True,'data':{'job':{'id':r['params']['arguments']['job_id'],'state':'completed','artifacts':[]}}}}
 print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':v}),flush=True)
''', encoding="utf-8")
    config = tmp_path / "client.json"
    config.write_text(json.dumps({"command": sys.executable, "args": [str(stub)]}), encoding="utf-8")
    from anvil_serving import cli
    assert cli.main(["media", "job", "watch", "--job-id", JOB, "--client-config", str(config)]) == 0
    assert '"state": "completed"' in capsys.readouterr().out
    assert tool_data({"content": [{"type": "text", "text": '{"ok":true,"data":{"value":1}}'}]}) == {"value": 1}
    config.write_text('{"command": "missing-executable-secret-value"}', encoding="utf-8")
    assert main(["--job-id", JOB, "--client-config", str(config)]) == 1
    assert "secret-value" not in capsys.readouterr().out


def test_rpc_crossing_overall_deadline_reports_watch_timeout(tmp_path, capsys):
    stub = tmp_path / "delayed.py"
    stub.write_text('''import sys,json,time
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r:continue
 if r['method']=='tools/call':time.sleep(1.2)
 print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{}}),flush=True)
''', encoding="utf-8")
    config = tmp_path / "client.json"
    config.write_text(json.dumps({"command": sys.executable, "args": [str(stub)]}), encoding="utf-8")
    assert main(["--job-id", JOB, "--client-config", str(config), "--timeout-seconds", "1"]) == 4
    result = json.loads(capsys.readouterr().out)
    assert result["event"] == "media_watch_timeout" and result["jobCanceled"] is False
