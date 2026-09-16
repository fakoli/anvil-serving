"""A missing verifier must be recoverable without weakening asset identity."""

import subprocess

import pytest

from anvil_serving.media.bundle import inventory, stage
from anvil_serving.media.errors import MediaError
from tests.media.test_bundle import _lock


def _runner(calls, *, pull_exit=0):
    cached = False

    def run(argv, **_kwargs):
        nonlocal cached
        calls.append(argv)
        if argv[:3] == ["docker", "volume", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["docker", "pull"]:
            cached = pull_exit == 0
            return subprocess.CompletedProcess(argv, pull_exit, "", "")
        assert argv[:2] == ["docker", "run"] and "--pull=never" in argv
        if not cached:
            return subprocess.CompletedProcess(argv, 125, "", "docker: No such image: example/stager")
        # Existing mismatched asset: even after recovering the helper, staging
        # must refuse before preparing layout or downloading replacement bytes.
        return subprocess.CompletedProcess(argv, 0, "122\n" + "d" * 64 + "\n", "")

    return run


def test_missing_helper_inventory_remains_read_only(tmp_path):
    calls = []
    with pytest.raises(MediaError) as raised:
        inventory("image.test", "v1", lock_path=_lock(tmp_path),
                  models_volume="media-models", runner=_runner(calls))
    assert raised.value.code == "media_bundle_helper_missing"
    assert not any(argv[:2] == ["docker", "pull"] for argv in calls)


def test_missing_helper_preview_discloses_pull_and_unknown_assets(tmp_path):
    calls = []
    result = stage("image.test", "v1", lock_path=_lock(tmp_path),
                   models_volume="media-models", user_volume="media-user",
                   dry_run=True, runner=_runner(calls))
    assert result["helperPullRequired"] and result["inventoryDeferred"]
    assert "@sha256:" in result["helperImage"]
    assert result["missingBytes"] is None and result["missingTargets"] is None
    assert not result["applied"] and not result["ready"]
    assert len(calls) == 2


def test_confirmed_helper_recovery_still_preserves_mismatched_assets(tmp_path):
    calls = []
    with pytest.raises(MediaError) as raised:
        stage("image.test", "v1", lock_path=_lock(tmp_path),
              models_volume="media-models", user_volume="media-user",
              dry_run=False, runner=_runner(calls))
    assert raised.value.code == "media_bundle_conflict"
    assert calls[2] == ["docker", "pull", "example/stager@sha256:" + "f" * 64]
    assert len(calls) == 5


def test_helper_pull_failure_never_touches_volume_contents(tmp_path):
    calls = []
    with pytest.raises(MediaError) as raised:
        stage("image.test", "v1", lock_path=_lock(tmp_path),
              models_volume="media-models", user_volume="media-user",
              dry_run=False, runner=_runner(calls, pull_exit=1))
    assert raised.value.code == "media_bundle_stage_failed"
    assert len(calls) == 3
