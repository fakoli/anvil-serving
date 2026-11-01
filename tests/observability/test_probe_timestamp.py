from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.probes import normalize_collection_time


def test_collection_time_normalizes_offset_and_defaults_to_current_utc():
    value = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2)))
    assert normalize_collection_time(value) == datetime(2025, 12, 31, 22, tzinfo=timezone.utc)
    before = datetime.now(timezone.utc)
    assert before <= normalize_collection_time(None) <= datetime.now(timezone.utc)


@pytest.mark.parametrize("value,error", [("2026-01-01", TypeError), (datetime(2026, 1, 1), ValueError)])
def test_collection_time_rejects_invalid_inputs(value, error):
    with pytest.raises(error, match="collected_at"):
        normalize_collection_time(value)
