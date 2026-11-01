from email.message import Message

import pytest

from anvil_serving.observability.dashboard.access import Access
from anvil_serving.observability.dashboard.contracts import ObservatoryError


def headers(**values):
    result = Message()
    for key, value in values.items():
        result[key.replace("_", "-")] = value
    return result


def access(**kwargs):
    return Access([{"id": "operator-fixture", "username": "operator", "role": "operator", "resources": ["serve-fixture-a"], "actions": ["tier.quiesce"]}],
                  authenticate=lambda u, p: u == "operator" and p == "fixture-password", origin="https://console.example.test", operate=True, **kwargs)


def test_legacy_and_proxy_credentials_never_authenticate():
    auth = access()
    for name in ("Authorization", "X-Api-Key", "Tailscale-User-Login"):
        with pytest.raises(ObservatoryError):
            auth.session(headers(**{name: "fixture-legacy-token"}))
