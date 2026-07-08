import json
from pathlib import Path


def test_fast_tier_promotion_profile_denies_high_risk_fast_pins():
    profile = json.loads(
        Path(
            "docs/findings/fast-tier-bakeoff-evidence/fast-tier-promotion-profile.json"
        ).read_text(encoding="utf-8")
    )
    decisions = {
        (entry["tier_id"], entry["work_class"]): entry["decision"]
        for entry in profile["entries"]
    }

    assert decisions[("fast-local", "planning")] == "deny"
    assert decisions[("fast-local", "review")] == "deny"
    assert decisions[("fast-local", "chat")] == "allow"

