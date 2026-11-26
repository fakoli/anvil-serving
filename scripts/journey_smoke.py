#!/usr/bin/env python3
"""Walk the gateway quickstart and every product journey as a new user would.

Host mode runs this same file inside a stock Python container that holds only the
installed package: no checkout, no operator repository, no Docker, no GPU. The report
says, for every documented step, whether it worked, refused with a message, or crashed.

Two personas run every journey step:

* ``gateway``  - someone who only followed docs/QUICKSTART-GATEWAY.md; the gateway and
  a stand-in model server stay up while the steps run.
* ``operator`` - someone who ran ``init --out-dir`` and exported ANVIL_SERVING_HOME.

A refusal is expected wherever a step needs Docker, a GPU, or a second host. A crash
or a hang never is, and neither is a failed quickstart.

``--real-model`` swaps the stand-in for a small real model served from a second
container, and adds the endpoint qualification a stand-in cannot pass: ``eval
preflight`` against the model directly and through the gateway.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

DEFAULT_IMAGE = "python:3.13-slim"
MARKER = "JOURNEY-SMOKE-REPORT "
STEP_TIMEOUT_SECONDS = 60
UPSTREAM = "http://127.0.0.1:11434/v1"
UPSTREAM_MODEL = "journey-smoke-model"
# The real-model lane. Any OpenAI-compatible server would do; this pair runs on a CPU.
REAL_IMAGE = "ghcr.io/ggml-org/llama.cpp:server"
REAL_MODEL = "Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF:Q4_K_M"
REAL_CACHE_VOLUME = "anvil-journey-smoke-models"
REAL_READY_SECONDS = 900  # first run downloads about 500 MB of weights
GATEWAY = "http://127.0.0.1:8000"
OPERATOR_HOME = "/root/operator"
QUICKSTART_CHECKS = frozenset({
    "init --gateway-only", "router run", "token written to .env", "models lists llm.primary",
    "OpenAI chat", "OpenAI chat, streamed", "Anthropic messages", "OpenAI responses",
    "unknown alias is refused", "missing token is refused",
})

# What a new user would type for each placeholder. Names come from the packaged
# scaffold; ``--tier`` names a serve in the serves manifest, not a router tier id.
VALUES = {
    "<OPERATOR_HOME>": OPERATOR_HOME, "<GROUP>": "llm-stack", "<SERVE>": "primary",
    "<PROFILE>": "dark-audio", "<WORKFLOW>": "image.flux2-klein-4b-fp8-v1", "<VERSION>": "v1",
    "<VOLUME>": "anvil-models", "<URL>": "http://127.0.0.1:8188",
    "<JSON>": '{"prompt":"a red cube","seed":1}', "<JOB_ID>": "job-not-submitted",
    "<ID>": "journey-smoke", "<PATH>": f"{OPERATOR_HOME}/deployment.json",
    "<FILE>": f"{OPERATOR_HOME}/request.json",
}

# Steps that exit 0 today with only the installed package, as (family id, stage). They
# must keep passing for both personas. Every other step may refuse, but must say why.
# Move a step here when its gap closes; the run then guards it.
MUST_PASS = frozenset({
    ("model-serving", "initialize"),
    ("model-serving", "select"),
    ("capability-gateway", "observe"),
    ("capability-gateway", "verify"),
    ("evaluation-evidence", "plan"),
    ("evaluation-evidence", "measure"),
    ("evaluation-evidence", "review"),
    ("anvil-voice", "qualify"),
    ("anvil-media", "discover"),
    ("anvil-media", "preview"),
    ("control-plane-fleet", "inspect-host"),
    ("control-plane-fleet", "integrate"),
})
# These additionally pass once ANVIL_SERVING_HOME points at the scaffold.
MUST_PASS_OPERATOR = frozenset({
    ("model-serving", "observe"),
    ("capability-gateway", "preview"),
    ("anvil-voice", "validate"),
})
# This passes only without the scaffold: its managed serves make Docker a required check.
MUST_PASS_GATEWAY = frozenset({
    ("control-plane-fleet", "diagnose"),
})


# --- inside the container -------------------------------------------------------


class _Upstream(BaseHTTPRequestHandler):
    """A stand-in OpenAI-compatible model server: enough to prove the relay."""

    def log_message(self, *_args: object) -> None:
        pass

    def _send(self, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        if self.path.endswith("/models"):
            body = {"object": "list", "data": [{"id": UPSTREAM_MODEL, "object": "model"}]}
        else:
            body = {"status": "ok"}
        self._send(json.dumps(body).encode())

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        request = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        model = request.get("model", UPSTREAM_MODEL)
        base = {"id": "chatcmpl-smoke", "created": 0, "model": model}
        if request.get("stream"):
            chunks = [
                {**base, "object": "chat.completion.chunk",
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": "pong"}}]},
                {**base, "object": "chat.completion.chunk",
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            self._send(body.encode(), "text/event-stream")
            return
        self._send(json.dumps({
            **base, "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "pong"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode())


def _run(argv: list[str], env: dict[str, str], timeout: int = STEP_TIMEOUT_SECONDS) -> dict[str, object]:
    started = time.monotonic()
    try:
        done = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout, check=False)
        code: int | None = done.returncode
        output = done.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as exc:
        code, output = None, (exc.stdout or b"").decode("utf-8", "replace")
    if code is None:
        status = "timeout"
    elif code < 0 or "Traceback (most recent call last)" in output:
        status = "crash"
    else:
        status = "ok" if code == 0 else "refused"
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return {"command": shlex.join(argv), "status": status, "exit_code": code,
            "seconds": round(time.monotonic() - started, 1),
            "last_line": lines[-1][:300] if lines else "", "tail": output[-1500:]}


def _call(method: str, path: str, headers: dict[str, str], body: dict | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(GATEWAY + path, data=data, method=method,
                                     headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except OSError as exc:
        return 0, str(exc)


def _quickstart(env: dict[str, str], real: bool) -> tuple[list[dict[str, object]], subprocess.Popen | None]:
    """docs/QUICKSTART-GATEWAY.md, in order. Returns the checks and the live gateway."""
    checks: list[dict[str, object]] = []
    reply = "" if real else "pong"  # a real model answers in its own words

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail[:300]})

    init = _run(["anvil-serving", "init", "--gateway-only", "--upstream", UPSTREAM,
                 "--model", UPSTREAM_MODEL], env)
    record("init --gateway-only", init["status"] == "ok", str(init["last_line"]))
    gateway = subprocess.Popen(["anvil-serving", "router", "run"], env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and gateway.poll() is None and _call("GET", "/v1/models", {})[0] == 0:
        time.sleep(0.5)
    if gateway.poll() is not None:
        record("router run", False, (gateway.stdout.read() if gateway.stdout else b"").decode()[-300:])
        return checks, None
    record("router run", True, f"listening on {GATEWAY}")

    # The documented way to load the token without printing it.
    token = subprocess.run(
        ["sh", "-c", "sed -n 's/^ANVIL_ROUTER_TOKEN=//p' \"${ANVIL_SERVING_HOME:-$HOME/.anvil-serving}/.env\""],
        env=env, capture_output=True, text=True, check=False).stdout.strip()
    record("token written to .env", bool(token), "present" if token else "missing")
    bearer = {"Authorization": f"Bearer {token}"}
    hello = [{"role": "user", "content": "hello"}]
    for name, want, needle, method, path, headers, body in (
        ("models lists llm.primary", 200, "llm.primary", "GET", "/v1/models", bearer, None),
        ("OpenAI chat", 200, reply, "POST", "/v1/chat/completions", bearer,
         {"model": "llm.primary", "messages": hello}),
        ("OpenAI chat, streamed", 200, "data:", "POST", "/v1/chat/completions", bearer,
         {"model": "llm.primary", "messages": hello, "stream": True}),
        ("Anthropic messages", 200, reply, "POST", "/v1/messages",
         {"x-api-key": token, "anthropic-version": "2023-06-01"},
         {"model": "llm.primary", "max_tokens": 64, "messages": hello}),
        ("OpenAI responses", 200, reply, "POST", "/v1/responses", bearer,
         {"model": "llm.primary", "input": "hello"}),
        ("unknown alias is refused", 404, "", "POST", "/v1/chat/completions", bearer,
         {"model": "not.configured", "messages": hello}),
        ("missing token is refused", 401, "", "GET", "/v1/models", {}, None),
    ):
        status, text = _call(method, path, headers, body)
        record(name, status == want and needle in text, f"HTTP {status} {text[:200]}")
    if real:
        # What the stand-in cannot prove: the endpoint qualifies, directly and when relayed.
        gate = ["anvil-serving", "eval", "preflight", "--checks", "smoke,json", "--confirm"]
        for name, target in (
            ("preflight, model server", ["--base-url", UPSTREAM, "--model", UPSTREAM_MODEL]),
            ("preflight, through the gateway",
             ["--base-url", GATEWAY + "/v1", "--model", "llm.primary", "--api-key-env", "ANVIL_ROUTER_TOKEN"]),
        ):
            done = _run(gate + target, {**env, "ANVIL_ROUTER_TOKEN": token}, timeout=600)
            record(name, done["status"] == "ok", str(done["tail"])[-300:])
    return checks, gateway


def _journeys(persona: str, env: dict[str, str]) -> list[dict[str, object]]:
    from anvil_serving.product_families import PRODUCT_FAMILIES

    results = []
    for family in PRODUCT_FAMILIES:
        for step in family.journey:
            argv = shlex.split(step.cli)
            for placeholder, value in VALUES.items():
                argv = [token.replace(placeholder, value) for token in argv]
            results.append({"persona": persona, "family": family.id, "stage": step.stage,
                            "documented": step.cli, **_run(argv, env)})
    return results


def _wait_for_real_model() -> bool:
    deadline = time.monotonic() + REAL_READY_SECONDS
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(UPSTREAM[:-len("/v1")] + "/health", timeout=5) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(2)
    return False


def inside(install: str, real: bool = False) -> int:
    env = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    installed = _run([sys.executable, "-m", "pip", "install", "--quiet",
                      "--root-user-action=ignore", install], env)
    version = _run(["anvil-serving", "--version"], env)
    report: dict[str, object] = {"install": installed["status"], "version": version["last_line"],
                                 "python": sys.version.split()[0], "version_status": version["status"],
                                 "expected_steps": [], "quickstart": [], "steps": []}
    report["real_model"] = real
    report["upstream"] = REAL_MODEL if real else "stand-in"
    if real and not _wait_for_real_model():
        report["quickstart"] = [{"check": "real model server ready", "ok": False,
                                 "detail": f"no answer on {UPSTREAM} in {REAL_READY_SECONDS}s"}]
    elif installed["status"] == "ok" and version["status"] == "ok":
        from anvil_serving.product_families import PRODUCT_FAMILIES

        report["expected_steps"] = [[family.id, step.stage]
                                    for family in PRODUCT_FAMILIES for step in family.journey]
        if not real:
            server = ThreadingHTTPServer(("127.0.0.1", 11434), _Upstream)
            threading.Thread(target=server.serve_forever, daemon=True).start()
        report["quickstart"], gateway = _quickstart(env, real)
        # The gateway stays up for both personas: `router status` should see it.
        report["steps"] = (_journeys("gateway", env)
                           + _journeys("operator", {**env, "ANVIL_SERVING_HOME": OPERATOR_HOME}))
        if gateway is not None:
            gateway.terminate()
    print(MARKER + json.dumps(report))
    return 0


# --- on the host ----------------------------------------------------------------


def failures(report: dict) -> list[str]:
    problems = []
    if report["install"] != "ok":
        problems.append("package did not install")
    if report.get("version_status") != "ok":
        problems.append("version command did not succeed")
    checks = [check["check"] for check in report["quickstart"]]
    required_checks = QUICKSTART_CHECKS
    if report.get("real_model"):
        required_checks = required_checks | {"preflight, model server", "preflight, through the gateway"}
    if set(checks) != required_checks or len(checks) != len(required_checks):
        problems.append("quickstart report is incomplete or duplicated")
    expected = [tuple(step) for step in report.get("expected_steps", [])]
    observed = [(step["persona"], step["family"], step["stage"]) for step in report["steps"]]
    required_steps = {(persona, *step) for persona in ("gateway", "operator") for step in expected}
    if (not expected or len(set(expected)) != len(expected)
            or set(observed) != required_steps or len(observed) != len(required_steps)):
        problems.append("journey report is incomplete or duplicated")
    problems += [f"quickstart: {check['check']} ({check['detail']})"
                 for check in report["quickstart"] if not check["ok"]]
    for step in report["steps"]:
        key = (step["family"], step["stage"])
        where = f"{step['persona']}/{step['family']}/{step['stage']}"
        required = key in MUST_PASS or key in (
            MUST_PASS_OPERATOR if step["persona"] == "operator" else MUST_PASS_GATEWAY)
        if step["status"] in ("crash", "timeout"):
            problems.append(f"{where}: {step['status']}")
        elif required and step["status"] != "ok":
            problems.append(f"{where}: must pass, got {step['status']}: {step['last_line']}")
    return problems


def render(report: dict) -> str:
    lines = [f"{report['version']} on Python {report['python']}, upstream: {report.get('upstream', 'stand-in')}",
             "", "Quickstart"]
    lines += [f"  {'ok  ' if check['ok'] else 'FAIL'}  {check['check']}" for check in report["quickstart"]]
    for persona in ("gateway", "operator"):
        lines += ["", f"Journeys as the {persona} persona"]
        for step in (s for s in report["steps"] if s["persona"] == persona):
            reason = "" if step["status"] == "ok" else f"  {step['last_line'][:110]}"
            lines.append(f"  {step['status']:<8} {step['family']:<20} {step['stage']:<18}{reason}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wheel", type=Path, help="wheel to install (default: the one wheel in dist/)")
    parser.add_argument("--pypi", action="store_true", help="install the published release instead")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--json", action="store_true", help="print the full report")
    parser.add_argument("--real-model", action="store_true",
                        help=f"serve {REAL_MODEL} from {REAL_IMAGE} instead of the stand-in "
                             "(first use takes about 1.7 GB of disk: a 1.2 GB server image and 0.5 GB of weights kept in a Docker volume)")
    parser.add_argument("--inside", metavar="INSTALL", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.inside:
        return inside(args.inside, args.real_model)

    command = ["docker", "run", "--rm", "-v", f"{Path(__file__).resolve()}:/smoke/journey_smoke.py:ro"]
    model_server = f"anvil-journey-smoke-model-{os.getpid()}" if args.real_model else None
    if model_server:
        # Sharing the model server's network namespace keeps every address 127.0.0.1
        # and publishes nothing on the host.
        subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", model_server, "-e", "LLAMA_CACHE=/cache",
             "-v", f"{REAL_CACHE_VOLUME}:/cache", REAL_IMAGE, "-hf", REAL_MODEL,
             "--alias", UPSTREAM_MODEL, "--host", "127.0.0.1", "--port", "11434"],
            check=True, stdout=subprocess.DEVNULL)
        command += ["--network", f"container:{model_server}"]
    if args.pypi:
        install = "anvil-serving"
    else:
        from wheel_smoke import select_wheel

        wheel = (args.wheel or select_wheel(Path(__file__).resolve().parents[1] / "dist")).resolve()
        command += ["-v", f"{wheel}:/smoke/{wheel.name}:ro"]
        install = f"/smoke/{wheel.name}"
    command += [args.image, "python", "/smoke/journey_smoke.py", "--inside", install]
    try:
        done = subprocess.run(command + (["--real-model"] if model_server else []),
                              capture_output=True, text=True, check=False)
    finally:
        if model_server:
            subprocess.run(["docker", "rm", "-f", model_server], capture_output=True, check=False)
    payload = [line for line in done.stdout.splitlines() if line.startswith(MARKER)]
    if not payload:
        print(f"journey smoke did not report: {(done.stderr or done.stdout)[-2000:]}", file=sys.stderr)
        return 1
    report = json.loads(payload[-1][len(MARKER):])
    problems = failures(report)
    print(json.dumps({"report": report, "failures": problems}, indent=2) if args.json else render(report))
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
