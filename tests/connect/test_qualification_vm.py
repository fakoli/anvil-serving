"""Closed image preparation tests; all downloads and tools are synthetic."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

from anvil_serving.connect import qualification as qualification
from anvil_serving.connect import qualification_vm as subject


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="requires Linux VM image custody controls")


_IMAGE = b"synthetic-qcow2-image"
_SUMS = hashlib.sha256(_IMAGE).hexdigest().encode("ascii") + b" *ubuntu-24.04-server-cloudimg-amd64.img\n"
_SIGNATURE = b"synthetic-signature"
_KEYRING = "a" * 64


def _config(tmp_path: Path, *, image: bytes = _IMAGE, maximum: int = 1024) -> tuple[qualification.QualificationConfig, Path]:
    source = tmp_path / "source"
    pins = source / "connect/test/vm"
    pins.mkdir(parents=True)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(mode=0o700)
    raw = {
        "schema": "anvil-connect.qualification-vm-pins/v1",
        "image_url": subject._IMAGE_URL,
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "max_image_bytes": maximum,
        "sums_url": subject._SUMS_URL,
        "sums_sha256": hashlib.sha256(_SUMS).hexdigest(),
        "signature_url": subject._SIGNATURE_URL,
        "signature_sha256": hashlib.sha256(_SIGNATURE).hexdigest(),
        "signer_fingerprint": "D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81",
        "keyring_sha256": _KEYRING,
    }
    (pins / "pins.json").write_text(json.dumps(raw), encoding="utf-8")
    config = qualification.QualificationConfig(
        source_root=source,
        artifact_root=artifacts,
        playwright_root=tmp_path / "playwright",
        go_module_cache=tmp_path / "cache",
        tools={},
        timeout_seconds=60,
    )
    return config, artifacts


def _prepare_fakes(monkeypatch: pytest.MonkeyPatch, config: qualification.QualificationConfig, *, image: bytes = _IMAGE,
                   qemu: dict[str, object] | None = None, signer: str = "D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81") -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(subject, "_read_config", lambda _: config)
    monkeypatch.setattr(subject, "_tool_metadata", lambda **kwargs: {
        "gpgv": {"sha256": "b" * 64, "version": "2.4.8"},
        "qemu_img": {"sha256": "c" * 64, "version": "10.2.1"},
        "keyring": {"sha256": _KEYRING},
    })

    def fake_download(url: str, target: Path, *, maximum: int, timeout: float) -> tuple[str, int]:
        calls.append(url)
        data = {subject._SUMS_URL: _SUMS, subject._SIGNATURE_URL: _SIGNATURE, subject._IMAGE_URL: image}[url]
        if len(data) > maximum:
            raise subject._source_error("VM image download failed")
        target.write_bytes(data)
        return hashlib.sha256(data).hexdigest(), len(data)

    def fake_command(argv: list[str], *, timeout: float) -> tuple[int, bytes]:
        if argv[0] == str(subject._GPGV):
            return 0, f"[GNUPG:] VALIDSIG {signer} 0 0 0 0 0 0 0 0\n".encode("ascii")
        return 0, json.dumps(qemu or {"format": "qcow2", "virtual-size": 4096}).encode("utf-8")

    monkeypatch.setattr(subject, "_download", fake_download)
    monkeypatch.setattr(subject, "_command", fake_command)
    return calls


def test_prepare_replaces_tampered_image_but_rewrites_only_a_tampered_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, artifacts = _config(tmp_path)
    calls = _prepare_fakes(monkeypatch, config)
    subject.prepare(tmp_path / "ignored.toml")
    image = artifacts / ".vm-images" / subject._CACHE_NAME
    image.chmod(0o600)
    image.write_bytes(b"tampered")
    replaced = subject.prepare(tmp_path / "ignored.toml")
    assert replaced["reused"] is False and image.read_bytes() == _IMAGE
    assert len(calls) == 6
    receipt = artifacts / "vm-image.json"
    receipt.write_text("{}", encoding="utf-8")
    receipt.chmod(0o600)
    repaired = subject.prepare(tmp_path / "ignored.toml")
    assert repaired["reused"] is True and len(calls) == 6
    assert json.loads(receipt.read_text())["schema"] == subject._SCHEMA


def test_pin_reader_rejects_symlink_and_closed_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _ = _config(tmp_path)
    pin = config.source_root / subject._PIN_PATH
    pin.unlink()
    pin.symlink_to(tmp_path / "outside")
    with pytest.raises(qualification.QualificationError, match="pins"):
        subject._pins(config)
