"""Synthetic native WebUI assertions, credential binding and privacy boundaries."""
from base64 import urlsafe_b64encode
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import hmac
import json
import secrets

import pytest

from anvil_serving.control_plane.authorization import AuthorizationDecision, INFERENCE_USE
from anvil_serving.router.identity import (
    EndUser, IdentityError, WebUIBinding, WebUIIdentityError,
    WebUIProfile, configured_scope_caller, forwarded_caller, legacy_caller,
    load_webui_bindings, select_webui_binding, verify_webui,
)
from tests.router.key_fixtures import tmp_path as tmp_path
from tests.router.test_usage_identity import ordinary

SIGNER = secrets.token_urlsafe(32).encode("ascii")
NOW = 1000


def b64(raw):
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def token(claims=None, header=None, *, payload_raw=None, header_raw=None, signer=SIGNER):
    if claims is None:
        claims = {"sub": "user:synthetic", "iss": "open-webui", "iat": NOW, "exp": NOW + 300,
                  "name": "PRIVATE NAME", "email": "PRIVATE EMAIL", "role": "admin"}
    if header is None:
        header = {"alg": "HS256", "typ": "JWT"}
    parts = [b64(header_raw if header_raw is not None else json.dumps(header).encode()),
             b64(payload_raw if payload_raw is not None else json.dumps(claims).encode())]
    return ".".join([*parts, b64(hmac.digest(signer, ".".join(parts).encode(), "sha256"))])


def headers(value=None):
    result = Message()
    if value is not None:
        result["x-openwebui-user-jwt"] = value
    return result


def profile(**changes):
    return WebUIProfile(**{"credential_id": "webui_service", "credential_kind": "configured_scope",
                          "instance": "webui:synthetic", "signer_env": "SYNTHETIC_WEBUI_SIGNER", **changes})


def binding(**changes):
    return load_webui_bindings((profile(**changes),), env={"SYNTHETIC_WEBUI_SIGNER": SIGNER.decode()})[0]


def caller(client="webui_service"):
    return configured_scope_caller(AuthorizationDecision(True, "authorized", client, frozenset({INFERENCE_USE})))


def assert_invalid(value, *, bound=None, now=NOW):
    with pytest.raises(WebUIIdentityError) as caught:
        verify_webui(value, binding() if bound is None else bound, now)
    assert str(caught.value) == "invalid forwarded identity"
    assert caught.value.code == "identity_invalid" and caught.value.status == 401


def test_concurrent_native_assertions_and_two_users_share_only_service_authority():
    bound, source = binding(), caller()
    native = headers(token())
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: verify_webui(native, bound, NOW), range(4)))
    assert results == [results[0]] * 4
    other = verify_webui(headers(token({"sub": "second-user", "iss": "open-webui", "iat": NOW, "exp": NOW + 300})), bound, NOW)
    first_caller, second_caller = forwarded_caller(source, results[0]), forwarded_caller(source, other)
    assert first_caller.end_user != second_caller.end_user
    assert first_caller.actor == second_caller.actor and first_caller.grant == second_caller.grant


def test_typ_is_optional_and_original_segments_are_authenticated():
    assert verify_webui(headers(token(header={"alg": "HS256"})), binding(), NOW).subject == "user:synthetic"
    original = token()
    parts = original.split(".")
    parts[1] = b64(json.dumps({"sub": "other", "iss": "open-webui", "iat": NOW, "exp": NOW + 300}).encode())
    assert_invalid(headers(".".join(parts)))
    assert_invalid(headers(token(signer=b"different-synthetic-signer-material")))


@pytest.mark.parametrize("size", [0, 31, 33])
def test_signature_is_exactly_32_bytes(size):
    parts = token().split(".")
    parts[2] = b64(b"x" * size)
    assert_invalid(headers(".".join(parts)))


def test_binding_uses_actual_credential_and_service_actor(tmp_path):
    bound = binding()
    assert select_webui_binding((bound,), caller()) is bound
    assert select_webui_binding((bound,), caller("unrelated")) is None
    assert select_webui_binding((bound,), legacy_caller()) is None
    wrong_kind = WebUIBinding(profile(credential_kind="device_key"), SIGNER)
    with pytest.raises(WebUIIdentityError):
        select_webui_binding((wrong_kind,), caller())
    store, key_id, secret = ordinary(tmp_path)
    ordinary_profile = profile(credential_id=key_id, credential_kind="device_key")
    device_bound = WebUIBinding(ordinary_profile, SIGNER)
    for kind in (None, "human", "service"):
        if kind is not None:
            store.bind_owner(key_id, kind, kind + ":synthetic", 0 if kind == "human" else 1)
        authenticated = store.authenticate(secret, snapshot=True).caller_snapshot
        if kind == "service":
            assert select_webui_binding((device_bound,), authenticated) is device_bound
            admitted = store.admit(store.authenticate(secret, snapshot=True), "/v1/chat/completions", "llm.primary").caller_snapshot
            assert forwarded_caller(admitted, verify_webui(headers(token()), device_bound, NOW)).grant == admitted.grant
        else:
            with pytest.raises(WebUIIdentityError):
                select_webui_binding((device_bound,), authenticated)
            with pytest.raises(IdentityError):
                forwarded_caller(authenticated, EndUser("instance", "open-webui", "subject"))


@pytest.mark.parametrize("value", [None, "", "short", "x" * 4097, 32, b"x" * 32])
def test_runtime_signer_is_present_bounded_and_redacted(value):
    with pytest.raises(WebUIIdentityError) as caught:
        load_webui_bindings((profile(),), env={"SYNTHETIC_WEBUI_SIGNER": value})
    assert str(caught.value) == "invalid forwarded identity"
