#!/usr/bin/env python3
"""Find semantic credential leaks without ever emitting matched values."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

MAX_FILE_BYTES = 20 * 1024 * 1024
TEXT_SUFFIXES = {
    ".conf",
    ".env",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
FIELD_PATTERN = re.compile(
    r'["\'](?P<field>deviceToken|accessToken|refreshToken|clientSecret)["\']'
    r"\s*[:=]\s*[\"'](?P<value>[^\"']+)[\"']",
    re.IGNORECASE,
)
CAPABILITY_PATTERN = re.compile(
    r"/__openclaw__/cap/(?P<value>[A-Za-z0-9_-]{12,})",
    re.IGNORECASE,
)
REMOTE_CREDENTIAL_PATTERN = re.compile(r"^https?://[^/\s:@]+:[^/\s@]+@", re.I)
IPV4_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
MAGICDNS_PATTERN = re.compile(  # semantic-scan-fixture
    r"\b[a-z0-9][a-z0-9.-]*\.ts\.net\b", re.I
)
HOME_NAME = r"(?P<name>[A-Za-z0-9._<>*…-]+)"
WINDOWS_HOME_PATTERN = re.compile(r"(?i)\b[A-Z]:\\Users\\" + HOME_NAME)
UNIX_HOME_PATTERN = re.compile(r"/(?:home|Users)/" + HOME_NAME)
SLUGGED_WINDOWS_HOME_PATTERN = re.compile(  # semantic-scan-fixture
    r"(?i)\b[A-Z](?:--|__|-)Users(?:--|__|-)(?P<name>[A-Za-z0-9.]+)(?=[_-])"
)
SAFE_MARKERS = (
    "<redacted>",
    "${",
    "example",
    "placeholder",
    "not-required",
    "dummy",
    "fake",
    "test-only",
)
SEMANTIC_FIELD_PREFIXES = ("configs/", "docs/", "examples/")
SAFE_HOME_NAMES = {
    "example",
    "me",
    "operator",
    "runner",
    "test",
    "user",
    "username",
    "ubuntu",
    "vllm",
    "node",
    "app",
    "*",
    "<operator>",
    "<user>",
    "...",
    "…",
}
GENERIC_TAILNET_NETWORK = ipaddress.ip_network("100.64.0.0/24")
TAILNET_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def run_git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
    )
    return result.stdout


def git_paths(root: Path, scope: str) -> list[tuple[str, str]]:
    groups: list[tuple[str, tuple[str, ...]]] = []
    if scope in {"current", "tracked"}:
        groups.append(("tracked", ("ls-files", "-z")))
    if scope in {"current", "untracked"}:
        groups.append(("untracked", ("ls-files", "--others", "--exclude-standard", "-z")))

    paths: list[tuple[str, str]] = []
    for source, command in groups:
        raw = run_git(root, *command)
        paths.extend(
            (source, item.decode("utf-8", errors="surrogateescape"))
            for item in raw.split(b"\0")
            if item
        )
    return paths


def safe_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in {"redacted", "none", "null"} or any(
        marker in lowered for marker in SAFE_MARKERS
    )


def scan_text(text: str, rel_path: str, source: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    normalized_path = rel_path.replace("\\", "/")
    scan_sensitive_fields = normalized_path.startswith(SEMANTIC_FIELD_PREFIXES)
    for line_number, line in enumerate(text.splitlines(), start=1):
        if scan_sensitive_fields:
            for match in FIELD_PATTERN.finditer(line):
                value = match.group("value")
                if len(value) >= 8 and not safe_placeholder(value):
                    findings.append(
                        {
                            "kind": f"literal-{match.group('field').lower()}",
                            "path": normalized_path,
                            "line": line_number,
                            "source": source,
                        }
                    )
        for match in CAPABILITY_PATTERN.finditer(line):
            if not safe_placeholder(match.group("value")):
                findings.append(
                    {
                        "kind": "openclaw-capability-url",
                        "path": normalized_path,
                        "line": line_number,
                        "source": source,
                    }
                )
        if "semantic-scan-fixture" in line:
            continue
        for match in IPV4_PATTERN.finditer(line):
            try:
                address = ipaddress.ip_address(match.group(0))
            except ValueError:
                continue
            if address in TAILNET_NETWORK and address not in GENERIC_TAILNET_NETWORK:
                findings.append(
                    {
                        "kind": "non-generic-tailnet-address",
                        "path": normalized_path,
                        "line": line_number,
                        "source": source,
                    }
                )
        for match in MAGICDNS_PATTERN.finditer(line):
            magicdns_name = match.group(0).lower()
            if magicdns_name != "example.ts.net" and not magicdns_name.endswith(
                ".example.ts.net"
            ):
                findings.append(
                    {
                        "kind": "non-generic-magicdns-name",
                        "path": normalized_path,
                        "line": line_number,
                        "source": source,
                    }
                )
        for pattern in (
            WINDOWS_HOME_PATTERN,
            UNIX_HOME_PATTERN,
            SLUGGED_WINDOWS_HOME_PATTERN,
        ):
            for match in pattern.finditer(line):
                if match.group("name").lower() not in SAFE_HOME_NAMES:
                    findings.append(
                        {
                            "kind": "operator-home-path",
                            "path": normalized_path,
                            "line": line_number,
                            "source": source,
                        }
                    )
    return findings


def scan_files(
    root: Path, entries: Iterable[tuple[str, str]]
) -> tuple[int, list[dict[str, object]]]:
    scanned = 0
    findings: list[dict[str, object]] = []
    for source, rel_path in entries:
        path = root / rel_path
        try:
            if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES and path.name != ".env.example":
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        scanned += 1
        text = data.decode("utf-8", errors="replace")
        findings.extend(scan_text(text, rel_path.replace("\\", "/"), source))
    return scanned, findings


def scan_remotes(root: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    remotes = run_git(root, "remote").decode("utf-8", errors="replace").splitlines()
    for remote in remotes:
        urls = run_git(root, "remote", "get-url", "--all", remote).decode("utf-8", errors="replace")
        if any(REMOTE_CREDENTIAL_PATTERN.search(url) for url in urls.splitlines()):
            findings.append(
                {
                    "kind": "credential-bearing-git-remote",
                    "path": "<git-remote>",
                    "line": 0,
                    "source": "repository-config",
                }
            )
    return findings


def self_test() -> int:
    secret_a = "sensitive-device-value-123"
    secret_b = "sensitive-capability-value-456"
    tailnet = ".".join(("100", "100", "20", "30"))
    magicdns = ".".join(("private-node", "private-net", "ts", "net"))
    separator = "\\"
    private_home = separator.join(("C:", "Users", "private-person", "config"))
    example_home = separator.join(("C:", "Users", "operator"))
    sample = (
        f'{{"deviceToken": "{secret_a}"}}\n'
        f'"url": "http://127.0.0.1/__openclaw__/cap/{secret_b}"\n'
        '{"accessToken": "<redacted>"}\n'
        f'"tailnet": "{tailnet}"\n'
        f'"magicdns": "{magicdns}"\n'
        f'"home": "{private_home}"\n'
        f'"example": "node-a.example.ts.net {example_home}"\n'
    )
    findings = scan_text(sample, "docs/fixture.json", "self-test")
    output = json.dumps(findings)
    passed = (
        len(findings) == 5
        and secret_a not in output
        and secret_b not in output
        and "private-person" not in output
        and "private-node" not in output
        and tailnet not in output
    )
    print(json.dumps({"ok": passed, "findings": len(findings)}, sort_keys=True))
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--scope",
        choices=("current", "tracked", "untracked"),
        default="current",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    root = args.root.resolve()
    try:
        entries = git_paths(root, args.scope)
        scanned, findings = scan_files(root, entries)
        findings.extend(scan_remotes(root))
    except (OSError, subprocess.CalledProcessError) as error:
        print(json.dumps({"ok": False, "error": type(error).__name__}, sort_keys=True))
        return 2

    result = {
        "ok": not findings,
        "scope": args.scope,
        "files_scanned": scanned,
        "finding_count": len(findings),
        "findings": findings,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
