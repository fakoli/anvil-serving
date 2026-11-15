import hashlib
import json

import pytest

from scripts.validate_hot_route_journeys import (
    CLEANUP_ASSERTIONS, EXPECTED_JOURNEYS, MAX_RECEIPT_BYTES, validate,
)


def campaign_files(root):
    def receipt(name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return {"receipt": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    journeys = [dict(name=name, **receipt(f"run-{name}/receipt.json", {
        "schema": "hot-route-journey/v1", "journey": name,
        "result": "passed", "cleanup_passed": True,
    })) for name in sorted(EXPECTED_JOURNEYS)]
    cleanup = receipt("cleanup.json", {"schema": "hot-route-cleanup/v1", "result": "passed",
                                      "assertions": dict.fromkeys(CLEANUP_ASSERTIONS, True)})
    config = {"schema": "hot-routes-e2e-campaign/v1", "journeys": journeys, "cleanup": cleanup}
    path = root / "campaign.json"
    path.write_text(json.dumps(config))
    return path, config, receipt


def test_accepts_bound_twelve_receipts(tmp_path):
    path, _, _ = campaign_files(tmp_path)
    assert validate(path)[0]


def test_rejects_incomplete_or_false_pass_campaign(tmp_path):
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps({"schema": "hot-routes-e2e-campaign/v1", "journeys": [], "cleanup": {}}))
    assert not validate(path)[0]


@pytest.mark.parametrize("problem", [
    "duplicate", "unknown", "malformed", "hash", "failed", "unclean",
    "cleanup-empty", "cleanup-failed", "cleanup-incomplete", "cleanup-false",
    "oversize", "absolute", "traversal", "symlink-file", "symlink-directory",
])
def test_rejects_invalid_or_unbound_evidence(tmp_path, problem):
    path, config, receipt = campaign_files(tmp_path)
    item = config["journeys"][0]
    target = tmp_path / item["receipt"]
    if problem in {"duplicate", "unknown", "malformed", "hash"}:
        if problem == "duplicate":
            config["journeys"][1] = item
        elif problem == "unknown":
            item["name"] = "invented"
        elif problem == "malformed":
            config["journeys"][0] = None
        else:
            item["sha256"] = "0" * 64
    elif problem in {"failed", "unclean"}:
        value = json.loads(target.read_text())
        value["result" if problem == "failed" else "cleanup_passed"] = "failed" if problem == "failed" else False
        item.update(receipt(item["receipt"], value))
    elif problem.startswith("cleanup-"):
        value = json.loads((tmp_path / "cleanup.json").read_text())
        if problem == "cleanup-empty":
            value = {}
        elif problem == "cleanup-failed":
            value["result"] = "failed"
        elif problem == "cleanup-incomplete":
            value["assertions"].pop("test_router_stopped")
        else:
            value["assertions"]["test_router_stopped"] = False
        config["cleanup"] = receipt("cleanup.json", value)
    elif problem == "oversize":
        target.write_bytes(b" " * (MAX_RECEIPT_BYTES + 1))
        item["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    elif problem == "absolute":
        item["receipt"] = str(target)
    elif problem == "traversal":
        item["receipt"] = "../outside.json"
    else:
        destination = target if problem == "symlink-file" else target.parent
        retained = destination.with_name(destination.name + "-retained")
        destination.rename(retained)
        try:
            destination.symlink_to(retained, target_is_directory=retained.is_dir())
        except OSError:
            pytest.skip("symlink creation unavailable on this platform")
    path.write_text(json.dumps(config))
    assert not validate(path)[0]


def test_rejects_oversize_before_content_read(tmp_path, monkeypatch):
    from scripts import validate_hot_route_journeys as verifier

    path = tmp_path / "oversize.json"
    path.write_bytes(b" " * (MAX_RECEIPT_BYTES + 1))
    original = verifier.os.fdopen

    class NoRead:
        def __init__(self, descriptor):
            self.source = original(descriptor, "rb")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.source.close()

        def fileno(self):
            return self.source.fileno()

        def read(self, *_):
            pytest.fail("oversized content must not be read")

    monkeypatch.setattr(verifier.os, "fdopen", lambda descriptor, *_: NoRead(descriptor))
    assert not validate(path)[0]
