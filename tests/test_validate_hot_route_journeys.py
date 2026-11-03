import hashlib
import json
from pathlib import Path
from scripts.validate_hot_route_journeys import validate


def test_accepts_bound_twelve_receipts(tmp_path: Path):
    journeys=[]
    for number in range(12):
        name=f"journey-{number}"
        receipt=tmp_path/f"{name}.json"
        receipt.write_text(json.dumps({"schema":"hot-route-journey/v1","journey":name,"status":"passed"}))
        journeys.append({"name":name,"receipt":receipt.name,"sha256":hashlib.sha256(receipt.read_bytes()).hexdigest()})
    cleanup=tmp_path/"cleanup.json"; cleanup.write_text("{}")
    config=tmp_path/"campaign.json"
    config.write_text(json.dumps({"journeys":journeys,"cleanup":{"receipt":cleanup.name,"sha256":hashlib.sha256(cleanup.read_bytes()).hexdigest()}}))
    assert validate(config)[0]


def test_rejects_missing_receipt(tmp_path: Path):
    config=tmp_path/"campaign.json"; config.write_text(json.dumps({"journeys":[],"cleanup":{}}))
    assert not validate(config)[0]
