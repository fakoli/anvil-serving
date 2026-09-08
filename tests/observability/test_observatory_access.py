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


def test_login_cookie_csrf_origin_and_resource_boundaries():
    auth = access(base_path="/observatory/")
    session = auth.login("operator", "fixture-password", client="fixture")
    cookie = auth.cookie(session)
    assert all(x in cookie for x in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/observatory/"))
    assert "__Host-" not in cookie
    good = headers(Cookie=cookie.split(";", 1)[0], Origin=auth.origin, X_CSRF_Token=session.csrf)
    assert auth.mutation(good) == session
    auth.permit(session, "serve-fixture-a", "tier.quiesce")
    for resource, action in [("serve-fixture-b", "tier.quiesce"), ("serve-fixture-a", "serve.stop")]:
        with pytest.raises(ObservatoryError):
            auth.permit(session, resource, action)
    for name in ("Origin", "X-CSRF-Token", "Cookie"):
        bad = headers(Cookie=cookie.split(";", 1)[0], Origin=auth.origin, X_CSRF_Token=session.csrf)
        del bad[name]
        with pytest.raises(ObservatoryError):
            auth.mutation(bad)
    good.replace_header("Origin", "https://evil.example.test")
    with pytest.raises(ObservatoryError):
        auth.mutation(good)


def test_legacy_and_proxy_credentials_never_authenticate():
    auth = access()
    for name in ("Authorization", "X-Api-Key", "Tailscale-User-Login"):
        with pytest.raises(ObservatoryError):
            auth.session(headers(**{name: "fixture-legacy-token"}))


def test_expiry_rotation_and_logout():
    clock = [1000]
    auth = access(clock=lambda: clock[0], lifetime=60)
    first = auth.login("operator", "fixture-password", client="fixture")
    second = auth.login("operator", "fixture-password", client="fixture", previous=first)
    assert second.key != first.key and second.csrf != first.csrf
    assert auth.session(headers(Cookie=auth.cookie(first).split(";")[0]), required=False) is None
    clock[0] += 60
    assert auth.session(headers(Cookie=auth.cookie(second).split(";")[0]), required=False) is None
    third = auth.login("operator", "fixture-password", client="fixture")
    auth.logout(third)
    assert auth.session(headers(Cookie=auth.cookie(third).split(";")[0]), required=False) is None


def test_duplicate_security_headers_and_cookie_refused():
    auth = access()
    s = auth.login("operator", "fixture-password", client="fixture")
    raw = auth.cookie(s).split(";")[0]
    for cookie in (raw + "; " + raw, "garbage"):
        with pytest.raises(ObservatoryError):
            auth.session(headers(Cookie=cookie))
    bad = headers(Cookie=raw, Origin=auth.origin, X_CSRF_Token=s.csrf)
    bad["Origin"] = auth.origin
    with pytest.raises(ObservatoryError):
        auth.mutation(bad)


def test_failed_login_is_bounded():
    auth = access()
    for _ in range(10):
        with pytest.raises(ObservatoryError) as err:
            auth.login("operator", "wrong", client="fixture")
        assert err.value.status == 401
    with pytest.raises(ObservatoryError) as err:
        auth.login("operator", "fixture-password", client="fixture")
    assert err.value.status == 429
