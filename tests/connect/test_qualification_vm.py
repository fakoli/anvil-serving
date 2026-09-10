"""Closed image preparation tests; all downloads and tools are synthetic."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from anvil_serving.connect import qualification as qualification
from anvil_serving.connect import _qualification_vm_process as process
from anvil_serving.connect import qualification_vm as subject


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
    monkeypatch.setattr(subject, "_require_linux_execution", lambda: None)
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


def test_prepare_refuses_nonlinux_before_reading_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(subject, "_read_config", lambda _: pytest.fail("unexpected config read"))
    with pytest.raises(qualification.QualificationError) as failure:
        subject.prepare()
    assert failure.value.code == "runner-unavailable"


def test_prepare_verifies_publishes_receipt_and_reuses_verified_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, artifacts = _config(tmp_path)
    calls = _prepare_fakes(monkeypatch, config)
    first = subject.prepare(tmp_path / "ignored.toml")
    assert first["ok"] is True and first["reused"] is False
    assert first["image_bytes"] == len(_IMAGE) and first["virtual_size"] == 4096
    image = artifacts / ".vm-images" / subject._CACHE_NAME
    assert image.read_bytes() == _IMAGE and os.stat(image).st_mode & 0o777 == 0o400
    receipt = artifacts / "vm-image.json"
    assert os.stat(receipt).st_mode & 0o777 == 0o600
    assert json.loads(receipt.read_text())["image_sha256"] == hashlib.sha256(_IMAGE).hexdigest()
    assert calls == [subject._SUMS_URL, subject._SIGNATURE_URL, subject._IMAGE_URL]
    second = subject.prepare(tmp_path / "ignored.toml")
    assert second["reused"] is True and calls == [subject._SUMS_URL, subject._SIGNATURE_URL, subject._IMAGE_URL]


@pytest.mark.parametrize("failure", ["checksum", "signer", "unsafe-format", "unsafe-backing", "unsafe-virtual"])
def test_prepare_refuses_bad_signed_or_unsafe_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    config, artifacts = _config(tmp_path)
    if failure == "checksum":
        calls = _prepare_fakes(monkeypatch, config, image=b"different")
    elif failure == "signer":
        calls = _prepare_fakes(monkeypatch, config, signer="E" * 40)
    elif failure == "unsafe-format":
        calls = _prepare_fakes(monkeypatch, config, qemu={"format": "raw", "virtual-size": 4096})
    elif failure == "unsafe-backing":
        calls = _prepare_fakes(monkeypatch, config, qemu={"format": "qcow2", "virtual-size": 4096, "backing-filename": "other"})
    else:
        calls = _prepare_fakes(monkeypatch, config, qemu={"format": "qcow2", "virtual-size": subject._MAX_VIRTUAL_SIZE + 1})
    with pytest.raises(qualification.QualificationError):
        subject.prepare(tmp_path / "ignored.toml")
    assert not (artifacts / ".vm-images" / subject._CACHE_NAME).exists()
    assert not (artifacts / "vm-image.json").exists()
    assert calls


def test_prepare_refuses_oversized_download_without_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, artifacts = _config(tmp_path, maximum=1)
    calls = _prepare_fakes(monkeypatch, config)
    with pytest.raises(qualification.QualificationError, match="download failed"):
        subject.prepare(tmp_path / "ignored.toml")
    assert calls == [subject._SUMS_URL, subject._SIGNATURE_URL, subject._IMAGE_URL]
    assert not (artifacts / ".vm-images" / subject._CACHE_NAME).exists()


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


def test_interrupted_download_leaves_no_image_or_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, artifacts = _config(tmp_path)
    _prepare_fakes(monkeypatch, config)

    def interrupted(url: str, target: Path, *, maximum: int, timeout: float) -> tuple[str, int]:
        if url == subject._IMAGE_URL:
            target.write_bytes(_IMAGE)
            raise KeyboardInterrupt
        data = _SUMS if url == subject._SUMS_URL else _SIGNATURE
        target.write_bytes(data)
        return hashlib.sha256(data).hexdigest(), len(data)

    monkeypatch.setattr(subject, "_download", interrupted)
    with pytest.raises(KeyboardInterrupt):
        subject.prepare(tmp_path / "ignored.toml")
    assert not (artifacts / ".vm-images" / subject._CACHE_NAME).exists()
    assert not (artifacts / "vm-image.json").exists()
    assert not list((artifacts / ".vm-images").glob("*.download"))


def test_pin_reader_rejects_symlink_and_closed_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, _ = _config(tmp_path)
    pin = config.source_root / subject._PIN_PATH
    pin.unlink()
    pin.symlink_to(tmp_path / "outside")
    with pytest.raises(qualification.QualificationError, match="pins"):
        subject._pins(config)


def test_pin_reader_requires_the_closed_source_schema(tmp_path: Path) -> None:
    config, _ = _config(tmp_path)
    pin = config.source_root / subject._PIN_PATH
    raw = json.loads(pin.read_text(encoding="utf-8"))
    raw["unexpected"] = True
    pin.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(qualification.QualificationError, match="pins"):
        subject._pins(config)


@pytest.mark.parametrize("phase", ["metadata", "cached-info", "cold-info", "cached-hash"])
def test_whole_preparation_budget_includes_non_download_stages(tmp_path, monkeypatch, phase):
    from dataclasses import replace
    config, artifacts = _config(tmp_path)
    _prepare_fakes(monkeypatch, config)
    clock = [100.0]
    monkeypatch.setattr(subject.time, "monotonic", lambda: clock[0])
    if phase.startswith("cached"):
        subject.prepare(tmp_path / "settings.toml")
    monkeypatch.setattr(subject, "_read_config", lambda _: replace(config, timeout_seconds=1))
    if phase == "metadata":
        original = subject._tool_metadata
        def metadata(**kwargs):
            clock[0] += 2
            return original(**kwargs)
        monkeypatch.setattr(subject, "_tool_metadata", metadata)
    elif phase == "cached-hash":
        original = subject._sha256
        def hash_file(*args, **kwargs):
            clock[0] += 2
            return original(*args, **kwargs)
        monkeypatch.setattr(subject, "_sha256", hash_file)
    else:
        original = subject._command
        def command(argv, *, timeout):
            if "info" in argv:
                assert 0 < timeout <= 1
                clock[0] += 2
            return original(argv, timeout=timeout)
        monkeypatch.setattr(subject, "_command", command)
    with pytest.raises(qualification.QualificationError) as failure:
        subject.prepare(tmp_path / "settings.toml")
    assert failure.value.code == "runner-timeout"
    if not phase.startswith("cached"):
        assert not (artifacts / "vm-image.json").exists()


@pytest.mark.parametrize("marker", [" ", "*"])
def test_gnu_checksum_modes_are_accepted_exactly_once(tmp_path, monkeypatch, marker):
    import sys
    line = hashlib.sha256(_IMAGE).hexdigest() + " " + marker + subject._CACHE_NAME + "\n"
    monkeypatch.setattr(sys.modules[__name__], "_SUMS", line.encode("ascii"))
    config, _ = _config(tmp_path)
    _prepare_fakes(monkeypatch, config)
    assert subject.prepare(tmp_path / "settings.toml")["ok"] is True


def test_duplicate_matching_signed_checksum_entries_are_rejected(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys.modules[__name__], "_SUMS", _SUMS * 2)
    config, artifacts = _config(tmp_path)
    _prepare_fakes(monkeypatch, config)
    with pytest.raises(qualification.QualificationError, match="absent from signed checksums"):
        subject.prepare(tmp_path / "settings.toml")
    assert not (artifacts / "vm-image.json").exists()
