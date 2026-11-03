import hashlib
import json
from pathlib import Path
from scripts.validate_hot_route_journeys import EXPECTED_JOURNEYS, validate


def test_accepts_bound_twelve_receipts(tmp_path: Path):
    journeys=[]
    for name in sorted(EXPECTED_JOURNEYS):
        receipt=tmp_path/f"{name}.json"
        receipt.write_text(json.dumps({"schema":"hot-route-journey/v1","journey":name,"result":"passed","cleanup_passed":True}))
        journeys.append({"name":name,"receipt":receipt.name,"sha256":hashlib.sha256(receipt.read_bytes()).hexdigest()})
    cleanup=tmp_path/"cleanup.json"; cleanup.write_text(json.dumps({"schema":"hot-route-cleanup/v1","result":"passed","assertions":True}))
    config=tmp_path/"campaign.json"
    config.write_text(json.dumps({"schema":"hot-routes-e2e-campaign/v1","journeys":journeys,"cleanup":{"schema":"hot-route-cleanup/v1","result":"passed","assertions":True,"receipt":cleanup.name,"sha256":hashlib.sha256(cleanup.read_bytes()).hexdigest()}}))
    assert validate(config)[0]


def test_rejects_incomplete_or_false_pass_campaign(tmp_path: Path):
    config=tmp_path/"campaign.json"; config.write_text(json.dumps({"schema":"hot-routes-e2e-campaign/v1","journeys":[],"cleanup":{}}))
    assert not validate(config)[0]
