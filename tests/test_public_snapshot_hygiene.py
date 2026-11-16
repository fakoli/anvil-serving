"""Tracked files, including the inside of .gz files, must not name lab identifiers.

The scanner lives in the secret-hygiene skill and also runs in the
`public snapshot hygiene` CI job; this test runs the same code so a regression
fails locally. Patterns covered: known lab hostnames, real tailnet names and
addresses outside 100.64.0.0/24, GPU UUIDs and UUID prefixes that are not
placeholders, and personal home directories.
"""
import gzip
import io
import tarfile
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents/skills/anvil-serving-secret-hygiene/scripts/semantic_secret_scan.py"
SPEC = importlib.util.spec_from_file_location("semantic_scan_hygiene", SCRIPT)
scanner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = scanner  # lets the scanner fork workers
SPEC.loader.exec_module(scanner)

# Built from parts so this file does not itself contain a lab name.
LAB_HOSTS = ["-".join(p) for p in (("fakoli", "dark"), ("fakoli", "mini"), ("fakoli", "mid", "mod"))]
LAB_HOSTS += ["ai-mbp" + "25", "-".join(("fakoli", "mini", "2"))]


def kinds(text, path="docs/x.md"):
    return {f["kind"] for f in scanner.scan_text(text, path, "test")}


def test_lab_hostnames_are_flagged_case_insensitively():
    for host in LAB_HOSTS:
        assert "known-lab-hostname" in kinds(f"worker {host.upper()} ran", "docs/a.md")


def test_owner_name_and_dashboard_naming_are_not_flagged():
    text = "ghcr.io/fakoli/anvil-serving and the Fakoli · Fleet dashboard"
    assert "known-lab-hostname" not in kinds(text)


def test_placeholders_pass():
    text = "100.64.0.7 GPU-00000000-0000-0000-0000-000000000000 /Users/operator node.example.ts.net"
    assert not scanner.scan_text(text, "docs/x.md", "test")


def test_gzip_contents_are_scanned(tmp_path):
    prefix = "GPU-" + "a1b2" + "c3d4"  # built at runtime so the file itself stays clean
    (tmp_path / "log.txt.gz").write_bytes(gzip.compress(f"CUDA_VISIBLE_DEVICES={prefix}-\u2026\n".encode()))
    (tmp_path / "host.log.gz").write_bytes(gzip.compress(f"on {LAB_HOSTS[0]}\n".encode()))
    scanned, findings = scanner.scan_files(
        tmp_path, [("test", "log.txt.gz"), ("test", "host.log.gz")]
    )
    assert scanned == 2
    assert {f["kind"] for f in findings} == {"operator-gpu-uuid-fragment", "known-lab-hostname"}


def test_tracked_snapshot_is_clean():
    entries = scanner.git_paths(ROOT, "tracked")
    scanned, findings = scanner.scan_files(ROOT, entries)
    assert scanned > 0
    assert not findings, [(f["kind"], f["path"], f["line"]) for f in findings[:20]]


def test_multi_member_gzip_and_opaque_gzip(tmp_path):
    members = gzip.compress(b"clean\n") + gzip.compress(f"on {LAB_HOSTS[0]}\n".encode())
    (tmp_path / "multi.log.gz").write_bytes(members)
    (tmp_path / "blob.gz").write_bytes(gzip.compress(b"x"))
    _, findings = scanner.scan_files(tmp_path, [("test", "multi.log.gz"), ("test", "blob.gz")])
    assert sorted(f["kind"] for f in findings) == ["known-lab-hostname", "tracked-gzip-unscanned"]


def test_truncated_uuid_and_url_or_path_hostnames_are_flagged():
    prefix = "GPU-" + "a1b2" + "c3d4"
    assert "operator-gpu-uuid-fragment" in kinds(f"{prefix}-0000-0000")
    assert "operator-gpu-uuid-fragment" in kinds(f"{prefix}-0000-0000-0000")
    for text in (f"grpc://{LAB_HOSTS[0]}:50051", f"/mnt/{LAB_HOSTS[1]}/data"):
        assert "known-lab-hostname" in kinds(text)


def test_tar_gz_members_are_scanned(tmp_path):
    payload = f"on {LAB_HOSTS[0]}\n".encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo("inner.log")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    (tmp_path / "cache.tar.gz").write_bytes(gzip.compress(buffer.getvalue()))
    _, findings = scanner.scan_files(tmp_path, [("test", "cache.tar.gz")])
    assert [f["kind"] for f in findings] == ["known-lab-hostname"]
