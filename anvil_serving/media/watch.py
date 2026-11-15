"""Observe one owned MCP job; let the caller's task manager deliver completion."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

from .contracts import JobState, TERMINAL_STATES

MAX_MESSAGE = 10 * 1024 * 1024


def tool_data(result):
    if not isinstance(result, dict) or result.get("isError"):
        raise ValueError("media_watch_tool_failed")
    value = result.get("structuredContent")
    if value is None:
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    value = json.loads(block["text"])
                except (ValueError, KeyError):
                    continue
                break
    if not isinstance(value, dict) or value.get("ok") is False:
        raise ValueError("media_watch_response_invalid")
    return value.get("data", value)


@contextmanager
def mcp_client(config_path, deadline):
    path = Path(config_path).expanduser()
    if path.stat().st_size > 65536:
        raise ValueError("media_watch_config_invalid")
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("media_watch_config_invalid")
    command, args, env = config.get("command"), config.get("args", []), config.get("env", {})
    if (not isinstance(command, str) or not command or not isinstance(args, list)
            or any(not isinstance(a, str) for a in args) or not isinstance(env, dict)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items())):
        raise ValueError("media_watch_config_invalid")
    # The operator-owned connection definition is executable configuration, never shell text.
    process = subprocess.Popen([command, *args], env={**os.environ, **env},
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    replies = queue.Queue(maxsize=32)

    def read():
        try:
            while line := process.stdout.readline(MAX_MESSAGE + 1):
                if len(line) > MAX_MESSAGE:
                    break
                replies.put(line, timeout=1)
        except (OSError, ValueError, queue.Full):
            pass
        finally:
            try:
                replies.put(None, timeout=1)
            except queue.Full:
                pass

    threading.Thread(target=read, daemon=True).start()
    identity = 0

    def rpc(method, parameters):
        nonlocal identity
        identity += 1
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identity,
                                       "method": method, "params": parameters}) + "\n")
        process.stdin.flush()
        request_deadline = min(deadline, time.monotonic() + 30)
        while True:
            remaining = request_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("media_watch_request_timeout")
            try:
                line = replies.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError("media_watch_request_timeout") from None
            if line is None:
                raise ValueError("media_watch_connection_closed")
            response = json.loads(line)
            if response.get("id") == identity:
                if response.get("error") or "result" not in response:
                    raise ValueError("media_watch_rpc_failed")
                return response["result"]

    def call(name, arguments):
        return tool_data(rpc("tools/call", {"name": name, "arguments": arguments}))

    try:
        rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                           "clientInfo": {"name": "anvil-media-watch", "version": "1"}})
        process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()
        yield call
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        process.stdout.close()


def watch(call, job_id, *, deadline, poll_seconds=5, clock=time.monotonic, sleep=time.sleep):
    previous = None
    while clock() < deadline:
        job = call("media_job_status", {"job_id": job_id})["job"]
        if job.get("id") != job_id:
            raise ValueError("media_watch_job_mismatch")
        state = JobState(job["state"])
        if state.value != previous:
            print(json.dumps({"event": "media_job_progress", "jobId": job_id, "state": state.value}), flush=True)
            previous = state.value
        if state in TERMINAL_STATES or state == JobState.AWAITING_APPROVAL:
            artifacts = []
            if state == JobState.COMPLETED:
                for artifact in job.get("artifacts", []):
                    inspected = call("media_artifact_inspect", {"artifact_id": artifact["id"]})["artifact"]
                    if inspected.get("id") != artifact["id"] or inspected.get("jobId") != job_id:
                        raise ValueError("media_watch_artifact_mismatch")
                    artifacts.append(inspected)
            return {"event": "media_job_finished", "jobId": job_id, "state": state.value,
                    "artifacts": artifacts, "requiresOperator": state == JobState.AWAITING_APPROVAL}
        sleep(min(poll_seconds, max(0, deadline - clock())))
    return {"event": "media_watch_timeout", "jobId": job_id, "state": previous,
            "jobCanceled": False, "resumeWithSameJobId": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--client-config", required=True, help="Protected stdio MCP connection definition: command, args, env.")
    parser.add_argument("--timeout-seconds", type=float, default=7200)
    parser.add_argument("--poll-seconds", type=float, default=5)
    args = parser.parse_args(argv)
    if (not re.fullmatch(r"job_[A-Za-z0-9_-]{16,124}", args.job_id)
            or not math.isfinite(args.timeout_seconds) or not 1 <= args.timeout_seconds <= 86400
            or not math.isfinite(args.poll_seconds) or not 1 <= args.poll_seconds <= 60):
        parser.error("invalid job id, timeout (1..86400) or poll interval (1..60)")
    deadline = time.monotonic() + args.timeout_seconds
    try:
        with mcp_client(args.client_config, deadline) as call:
            result = watch(call, args.job_id, deadline=deadline, poll_seconds=args.poll_seconds)
    except TimeoutError:
        expired = time.monotonic() >= deadline
        print(json.dumps({"event": "media_watch_timeout" if expired else "media_watch_error",
                          "jobId": args.job_id, "jobCanceled": False,
                          "resumeWithSameJobId": True}), flush=True)
        return 4 if expired else 1
    except (OSError, ValueError, KeyError, TypeError):
        # Remote errors/configuration may contain credentials; never echo their values.
        print(json.dumps({"event": "media_watch_error", "jobId": args.job_id,
                          "jobCanceled": False}), flush=True)
        return 1
    print(json.dumps(result), flush=True)
    if result["event"] == "media_watch_timeout":
        return 4
    if result["requiresOperator"]:
        return 3
    return 0 if result["state"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
