#!/usr/bin/env python3
"""Find semantic credential leaks without ever emitting matched values."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Iterable

MAX_FILE_BYTES = 20 * 1024 * 1024
TEXT_SUFFIXES = {
    ".conf",
    ".csv",
    ".env",
    ".ini",
    ".json",
    ".log",
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
TAILSCALE_IPV6_PATTERN = re.compile(
    r"(?<![0-9a-f:])fd7a:115c:a1e0(?::[0-9a-f]{0,4}){2,5}(?![0-9a-f:])",
    re.I,
)
MAGICDNS_PATTERN = re.compile(  # semantic-scan-fixture
    r"\b[a-z0-9][a-z0-9.-]*\.ts\.net\b", re.I
)
GPU_UUID_PATTERN = re.compile(
    r"\bGPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.I
)
GPU_UUID_FRAGMENT_PATTERN = re.compile(
    r"\b(?:GPU-[0-9a-f]{8,}(?:(?:\.\.\.|…)[0-9a-f]{4,})?"
    r"(?![0-9a-f-])|REDACTED_GPU_UUID_[A-Z0-9_]+"
    r"(?:\.\.\.|…)[0-9a-f]{4,})\b",
    re.I,
)
# Exact hashes of the repository's reviewed public GPU fixtures. Hashes keep
# scanner output and source free of machine identifiers while making the
# allowlist value-specific rather than path- or extension-wide.
SAFE_GPU_UUID_SHA256 = frozenset(
    {
        "49761fc64d674ca2f94a153b699d021d9ffcde6870c7ecb3424e45936441a932",
        "6138908adc3d1c83d596dfc3b3d24537a00efd387ee88ae33c941a8d4f1f5507",
        "8dad45bd25e1846f56f8f6473970217e36d22a2032dc3b56c198d8f81f061f66",
        "99ef8e0256e255e6e80db1cfbdcfc739e57363fcf4599a6ad23f8c800a415908",
        "9a4427ae5ea45e4352ca5c4c273369220ab6cf99f9817bd2472bb2ee9996fb17",
        "a7ee4f3047b1d37690886431dc33fa69f71700930fd0f092ece5228f0986c2ec",
        "adf698ab2a25eb407f2355d48f879efb85bb8b08d3985a611f0f3bcfb6f4353f",
        "bde119761f38e8018304358ffc87969683b7be21e3f96e87088e2d71a226d96c",
        "bee1927a059b295ec61b3da9d642edce9c3fd58df4bcae79f3dbc02b33cd0eb7",
        "d521e3f3f4dde3261532534b277822e3c876ff5e6f848df5c6d268f77ee0bd5a",
        "dbc83fd80c3e79056e4cfe8256df0098ae31f931c4a9220bad66fb40b39007b5",
        "f60e70572b328307382c3ad18c66fde4d09b99a0496c0361edee7beae019ebef",
    }
)
LOCAL_HOST_PATTERN = re.compile(
    r"\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.local\b(?!\.)", re.I
)
REFERENCE_HOST_EXPRESSION = r"(?:fakoli-(?:dark|mini|mid-mod)(?:-2)?|ai-mbp25)"
NETWORK_CONTEXT_PATTERN = re.compile(
    r"(?:https?://|wss?://|\bssh\b|--gateway-host|\bhostname\b|"
    r"expected_endpoint_host|default_[a-z0-9_]*hosts)",
    re.I,
)
REFERENCE_NETWORK_IDENTITY_PATTERN = re.compile(
    rf"(?:(?:https?|wss?|ssh)://|\bssh\s+|--gateway-host\s+)<*"
    rf"{REFERENCE_HOST_EXPRESSION}>*(?!\.example\b)(?=[:/\s\"'`>,;)]|$)"
    rf"|\b{REFERENCE_HOST_EXPRESSION}(?=:[~/])"
    rf"|\b{REFERENCE_HOST_EXPRESSION}[^\n]{{0,48}}\bpasswordless\s+ssh\b",
    re.I,
)
HOME_NAME = r"(?P<name>[A-Za-z0-9._<>*…-]+)"
WINDOWS_PATH_SEPARATOR = r"(?:\\\\|\\|/)"
WINDOWS_HOME_PATTERN = re.compile(
    r"(?i)\b[A-Z]:" + WINDOWS_PATH_SEPARATOR + r"Users" + WINDOWS_PATH_SEPARATOR + HOME_NAME
)
UNIX_HOME_PATTERN = re.compile(r"(?<![A-Za-z]:)/(?:home|Users)/" + HOME_NAME)
SLUGGED_WINDOWS_HOME_PATTERN = re.compile(  # semantic-scan-fixture
    r"(?i)\b[A-Z](?:--|__|-)Users(?:--|__|-)(?P<name>[A-Za-z0-9.]+)(?=[_-])"
)
OPERATOR_USER_FIELD_PATTERN = re.compile(
    r'''(?i)["'](?:user|username)["']\s*[:=]\s*["'](?P<name>[^"']+)["']'''
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
SAFE_OPERATOR_USER_NAMES = {
    "<operator>",
    "<operator-user>",
    "<user>",
    "app",
    "node",
    "nobody",
    "operator",
    "root",
    "runner",
    "test",
    "ubuntu",
    "user",
    "username",
    "vllm",
}
GENERIC_TAILNET_NETWORK = ipaddress.ip_network("100.64.0.0/24")
TAILNET_NETWORK = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_IPV6_NETWORK = ipaddress.ip_network("fd7a:115c:a1e0::/48")


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


def safe_gpu_uuid_fragment(value: str) -> bool:
    """Allow only an obviously synthetic repeated-hex GPU prefix fixture."""
    upper = value.upper()
    if not upper.startswith("GPU-"):
        return False
    compact = upper.removeprefix("GPU-").replace("...", "").replace("…", "")
    return bool(compact) and len(set(compact)) == 1


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
        if normalized_path.startswith("docs/findings/"):
            for match in OPERATOR_USER_FIELD_PATTERN.finditer(line):
                name = match.group("name").strip().lower()
                if name not in SAFE_OPERATOR_USER_NAMES and not safe_placeholder(name):
                    findings.append(
                        {
                            "kind": "operator-user-identity",
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
        for match in GPU_UUID_PATTERN.finditer(line):
            digest = hashlib.sha256(match.group(0).lower().encode("ascii")).hexdigest()
            if digest not in SAFE_GPU_UUID_SHA256:
                findings.append(
                    {
                        "kind": "operator-gpu-uuid",
                        "path": normalized_path,
                        "line": line_number,
                        "source": source,
                    }
                )
        for match in GPU_UUID_FRAGMENT_PATTERN.finditer(line):
            if not safe_gpu_uuid_fragment(match.group(0)):
                findings.append(
                    {
                        "kind": "operator-gpu-uuid-fragment",
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
        if NETWORK_CONTEXT_PATTERN.search(line):
            for _match in LOCAL_HOST_PATTERN.finditer(line):
                findings.append(
                    {
                        "kind": "operator-local-hostname",
                        "path": normalized_path,
                        "line": line_number,
                        "source": source,
                    }
                )
        for match in TAILSCALE_IPV6_PATTERN.finditer(line):
            try:
                address = ipaddress.ip_address(match.group(0))
            except ValueError:
                continue
            if address in TAILSCALE_IPV6_NETWORK and address != TAILSCALE_IPV6_NETWORK.network_address:
                findings.append(
                    {
                        "kind": "non-generic-tailnet-ipv6-address",
                        "path": normalized_path,
                        "line": line_number,
                        "source": source,
                    }
                )
        if REFERENCE_NETWORK_IDENTITY_PATTERN.search(line):
            findings.append(
                {
                    "kind": "reference-host-network-identity",
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
    root: Path,
    entries: Iterable[tuple[str, str]],
    *,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> tuple[int, list[dict[str, object]]]:
    scanned = 0
    findings: list[dict[str, object]] = []
    for source, rel_path in entries:
        path = root / rel_path
        normalized_path = rel_path.replace("\\", "/")
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != ".env.example":
            continue
        try:
            if path.is_symlink():
                findings.append(
                    {
                        "kind": "tracked-text-symlink-unscanned",
                        "path": normalized_path,
                        "line": 0,
                        "source": source,
                    }
                )
                continue
            if not path.is_file():
                findings.append(
                    {
                        "kind": "tracked-text-file-not-regular",
                        "path": normalized_path,
                        "line": 0,
                        "source": source,
                    }
                )
                continue
            if path.stat().st_size > max_file_bytes:
                findings.append(
                    {
                        "kind": "tracked-text-file-too-large",
                        "path": normalized_path,
                        "line": 0,
                        "source": source,
                    }
                )
                continue
            data = path.read_bytes()
        except OSError:
            findings.append(
                {
                    "kind": "tracked-text-file-unreadable",
                    "path": normalized_path,
                    "line": 0,
                    "source": source,
                }
            )
            continue
        if b"\0" in data:
            findings.append(
                {
                    "kind": "tracked-text-file-binary",
                    "path": normalized_path,
                    "line": 0,
                    "source": source,
                }
            )
            continue
        scanned += 1
        text = data.decode("utf-8", errors="replace")
        findings.extend(scan_text(text, normalized_path, source))
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
    json_escaped_home = private_home.replace(separator, separator * 2)
    slash_home = private_home.replace(separator, "/")
    example_home = separator.join(("C:", "Users", "operator"))
    private_gpu = "GPU-" + "-".join(
        ("9f8e7d6c", "5b4a", "4938", "8271", "605f4e3d2c1b")
    )
    safe_gpu = "GPU-" + "-".join(
        ("11111111", "1111", "1111", "1111", "111111111111")
    )
    private_gpu_fragment = "GPU-" + "9f8e7d6c" + "..." + "3d2c1b"
    private_gpu_prefix = "GPU-" + "9f8e7d6c"
    redacted_gpu_suffix = "REDACTED_GPU_UUID_COMPUTE_A..." + "3d2c1b"
    private_tailnet_v6 = ":".join(("fd7a", "115c", "a1e0", "", "8701", "2247"))
    safe_tailnet_v6_prefix = ":".join(("fd7a", "115c", "a1e0", "", ""))
    private_local_host = "private-node.local"
    reference_host = "-".join(("fakoli", "mini"))
    private_operator_user = "-".join(("private", "operator"))
    sample = (
        f'{{"deviceToken": "{secret_a}"}}\n'
        f'"url": "http://127.0.0.1/__openclaw__/cap/{secret_b}"\n'
        '{"accessToken": "<redacted>"}\n'
        f'"tailnet": "{tailnet}"\n'
        f'"magicdns": "{magicdns}"\n'
        f'"home": "{private_home}"\n'
        f'"json_home": "{json_escaped_home}"\n'
        f'"slash_home": "{slash_home}"\n'
        f'"example": "node-a.example.ts.net {example_home}"\n'
        f'"gpu_uuid": "{private_gpu}"\n'
        f'"safe_gpu_uuid": "{safe_gpu}"\n'
        f'"gpu_fragment": "{private_gpu_fragment}"\n'
        f'"gpu_prefix": "{private_gpu_prefix}"\n'
        f'"redacted_gpu_suffix": "{redacted_gpu_suffix}"\n'
        f'"tailnet_v6": "{private_tailnet_v6}"\n'
        f'"safe_tailnet_v6_prefix": "{safe_tailnet_v6_prefix}"\n'
        f'"hostname": "{private_local_host}"\n'
        f'"probe": "ssh {reference_host}"\n'
        f'"user": "{private_operator_user}"\n'
        '"username": "<operator-user>"\n'
    )
    findings = scan_text(sample, "docs/findings/fixture.json", "self-test")
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        (root / "large.md").write_text("x" * 17, encoding="utf-8")
        (root / "binary.json").write_bytes(b"{}\0")
        _, file_findings = scan_files(
            root,
            (
                ("self-test", "large.md"),
                ("self-test", "binary.json"),
                ("self-test", "missing.toml"),
            ),
            max_file_bytes=16,
        )
    file_kinds = {finding["kind"] for finding in file_findings}
    output = json.dumps(findings)
    expected_semantic_kinds = Counter(
        {
            "literal-devicetoken": 1,
            "non-generic-magicdns-name": 1,
            "non-generic-tailnet-address": 1,
            "non-generic-tailnet-ipv6-address": 1,
            "openclaw-capability-url": 1,
            "operator-gpu-uuid": 1,
            "operator-gpu-uuid-fragment": 3,
            "operator-home-path": 3,
            "operator-local-hostname": 1,
            "operator-user-identity": 1,
            "reference-host-network-identity": 1,
        }
    )
    passed = (
        Counter(finding["kind"] for finding in findings) == expected_semantic_kinds
        and file_kinds
        == {
            "tracked-text-file-binary",
            "tracked-text-file-not-regular",
            "tracked-text-file-too-large",
        }
        and secret_a not in output
        and secret_b not in output
        and "private-person" not in output
        and "private-node" not in output
        and private_operator_user not in output
        and tailnet not in output
        and private_tailnet_v6 not in output
        and private_gpu not in output
        and private_gpu_fragment not in output
        and private_gpu_prefix not in output
        and redacted_gpu_suffix not in output
        and reference_host not in output
    )
    print(
        json.dumps(
            {
                "ok": passed,
                "semantic_findings": sum(expected_semantic_kinds.values()),
                "fail_closed_findings": len(file_findings),
            },
            sort_keys=True,
        )
    )
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
