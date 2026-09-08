#!/usr/bin/env python3
"""Bounded read-only acceptance checks; no lifecycle or configuration writes."""

import argparse
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parsed = urllib.parse.urlsplit(args.url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"):
        parser.error("use HTTPS or an explicit loopback fixture")
    if parsed.query or parsed.fragment or parsed.username or not parsed.path.endswith("/"):
        parser.error("use a credential-free base URL ending in /")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    jar = http.cookiejar.CookieJar()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPCookieProcessor(jar))
    checks = []

    def request(path, *, body=None, headers=None, method=None):
        req = urllib.request.Request(args.url + path, data=json.dumps(body).encode() if body is not None else None,
            headers={"Origin": origin, **({"Content-Type": "application/json"} if body is not None else {}), **(headers or {})}, method=method)
        started = time.monotonic()
        try:
            response = opener.open(req, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("response bound exceeded")
            checks.append({"route": path, "status": response.status, "milliseconds": round((time.monotonic() - started) * 1000)})
            return response.status, response.headers, raw

    prefix = "api/observatory/v1/"
    receipt = {"schema": "anvil-observatory/validation/v1", "observed_at_epoch_seconds": time.time(), "checks": checks, "passed": False}
    csrf = None
    try:
        status, _, _ = request(prefix + "fleet")
        assert status == 401, "anonymous fleet read was not refused"
        password = args.password_file.read_text().strip()
        assert 0 < len(password) <= 4096, "password file is empty or unbounded"
        status, headers, raw = request(prefix + "session", body={"username": args.username, "password": password})
        del password
        assert status == 200, "configured login failed"
        session = json.loads(raw)["data"]
        csrf = session["csrf_token"]
        cookie = headers.get("Set-Cookie", "").lower()
        assert all(flag in cookie for flag in ("secure", "httponly", "samesite=strict")), "session cookie flags missing"
        receipt["build"] = session.get("build")
        receipt["operate"] = session.get("operate")
        for asset in ("", "observatory.js", "observatory.css", "views/workload_view.js"):
            status, headers, _ = request(asset)
            assert status == 200, "packaged asset unavailable"
            assert "script-src 'self'" in headers.get("Content-Security-Policy", ""), "same-origin script CSP missing"
        for route in ("fleet", "controls", "operations", "evidence", "settings"):
            status, _, raw = request(prefix + route)
            assert status == 200, "authenticated read failed"
            data = json.loads(raw)["data"]
            if route == "fleet":
                receipt["coverage"] = data.get("coverage")
                receipt["counts"] = {kind: len(data.get(kind, [])) for kind in ("hosts", "serves")}
                receipt["counts"]["gpus"] = sum(len(host.get("gpus", [])) for host in data.get("hosts", []))
            elif route in ("operations", "evidence"):
                receipt[route + "_count"] = len(data.get("items", []))
        status, _, _ = request(prefix + "operations", body={"preview_id": "invalid", "intent_key": "invalid"})
        assert status == 403, "missing-CSRF operation was not refused"
        status, _, _ = request(prefix + "metrics?chart=arbitrary-query")
        assert status in (400, 403), "unknown metric query was not refused"
        receipt["passed"] = True
    except (AssertionError, KeyError, ValueError) as error:
        # Messages above are fixed local text, never upstream response bodies.
        receipt["error"] = str(error) if isinstance(error, AssertionError) else "invalid bounded response"
    except Exception:
        receipt["error"] = "source unavailable during validation"
    finally:
        if csrf:
            try:
                request(prefix + "session", method="DELETE", headers={"X-CSRF-Token": csrf})
            except Exception:
                receipt["logout"] = "unavailable"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(receipt, stream, indent=2)
            stream.write("\n")
    print(json.dumps({"passed": receipt["passed"], "checks": len(checks), "output": str(args.output)}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
