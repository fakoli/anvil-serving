from __future__ import annotations

from anvil_serving.product_families import catalog_data, journey_data


def test_catalog_and_journey_share_one_versioned_umbrella_shape():
    catalog = catalog_data()
    journey = journey_data("media")

    assert journey["schema_version"] == catalog["schema_version"]
    assert journey["umbrella"] == catalog["umbrella"]
    assert isinstance(journey["umbrella"], dict)
