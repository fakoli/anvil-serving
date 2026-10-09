"""Synthetic native WebUI assertions, credential binding and privacy boundaries."""
from base64 import urlsafe_b64encode
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from email.message import Message
import hmac
import json
import math
import os
import secrets

import pytest

from anvil_serving.control_plane.authorization import AuthorizationDecision, INFERENCE_USE
from anvil_serving.router.identity import (
    CallerSnapshot, EndUser, IdentityError, WebUIBinding, WebUIIdentityError,
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


def test_native_success_repeated_assertion_and_closed_projection():
    bound = binding()
    native = headers(token())
    first = verify_webui(native, bound, NOW)
    assert verify_webui(native, bound, NOW + 1) == first
    assert verify_webui(native, bound, datetime.fromtimestamp(NOW, timezone.utc)) == first
    assert first == EndUser("webui:synthetic", "open-webui", "user:synthetic")
    source = caller()
    attributed = forwarded_caller(source, first)
    assert attributed.actor == source.actor and attributed.grant == source.grant
    assert attributed.attribution_state == "verified_forwarded"
    assert CallerSnapshot.from_json(attributed.to_json()) == attributed
    assert all(mark not in attributed.to_json() for mark in ("PRIVATE NAME", "PRIVATE EMAIL", "admin", SIGNER.decode(), "SYNTHETIC_WEBUI_SIGNER"))
    with pytest.raises(FrozenInstanceError):
        first.subject = "changed"
    with pytest.raises(AttributeError):
        bound._signer = b"changed"
    assert SIGNER.decode() not in repr(bound)


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


@pytest.mark.parametrize("value", [None, "", "unsigned user", "a.b", "a.b.c.d", ".b.c", "a..c", "a.b.",
    "a.b.c, a.b.c", "a.b.c\n", "é", "x" * 8193])
def test_missing_unsigned_and_malformed_assertions_fail_closed(value):
    assert_invalid(headers(value))


def test_duplicate_case_insensitive_header_is_rejected_even_identical():
    native = headers(token())
    native["X-OpenWebUI-USER-JWT"] = token()
    assert_invalid(native)
    assert_invalid({"X-OpenWebUI-User-Jwt": token()})  # No lossy get() fallback.


@pytest.mark.parametrize("head", [{"alg": "none"}, {"alg": "HS512"}, {"alg": "RS256"}, {"alg": True},
    {"alg": "HS256", "typ": "jwt"}, {"alg": "HS256", "kid": "caller"},
    {"alg": "HS256", "crit": []}, {"alg": "HS256", "jku": "https://invalid.example"},
    {"alg": "HS256", "jwk": {}}, {"alg": "HS256", "x5u": "invalid"}, {}, []])
def test_only_fixed_native_jose_header_is_allowed(head):
    assert_invalid(headers(token(header=head)))


def test_typ_is_optional_and_original_segments_are_authenticated():
    assert verify_webui(headers(token(header={"alg": "HS256"})), binding(), NOW).subject == "user:synthetic"
    original = token()
    parts = original.split(".")
    parts[1] = b64(json.dumps({"sub": "other", "iss": "open-webui", "iat": NOW, "exp": NOW + 300}).encode())
    assert_invalid(headers(".".join(parts)))
    assert_invalid(headers(token(signer=b"different-synthetic-signer-material")))


def test_decoded_size_bounds_are_inclusive_and_profile_claims_are_not_persisted():
    head = b'{"alg":"HS256"}'
    payload = b'{"sub":"a","iss":"open-webui","iat":1000,"exp":1300}'
    native = token(header_raw=head + b" " * (512 - len(head)),
                   payload_raw=payload + b" " * (4096 - len(payload)))
    assert verify_webui(headers(native), binding(), NOW).subject == "a"
    assert_invalid(headers(token(header_raw=head + b" " * (513 - len(head)))))
    assert_invalid(headers(token(payload_raw=payload + b" " * (4097 - len(payload)))))


@pytest.mark.parametrize("field", ["sub", "iss", "iat", "exp"])
def test_each_native_identity_claim_is_required(field):
    claims = {"sub": "a", "iss": "open-webui", "iat": NOW, "exp": NOW + 300}
    del claims[field]
    assert_invalid(headers(token(claims)))


@pytest.mark.parametrize("raw", [b'{"alg":"HS256","alg":"HS256"}', b'{"alg":"HS256","typ":"JWT","typ":"JWT"}',
    b'{"alg":"HS256","nested":{"a":1,"a":2}}', b'{"alg":"HS256","value":NaN}',
    b'{"alg":"HS256","value":1e999}', b'{"alg":"HS256"}\xff',
    '{"alg":"HS256"}'.encode("utf-16"), b"[", b"{}" * 257])
def test_header_strict_utf8_json_and_size(raw):
    assert_invalid(headers(token(header_raw=raw)))


@pytest.mark.parametrize("raw", [b'{"sub":"a","sub":"a"}', b'{"x":{"a":1,"a":2}}',
    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e999}', b'{"x":"\xff"}',
    '{"sub":"a"}'.encode("utf-16"), b"[", b"[" * 1100 + b"]" * 1100, b" " * 4097])
def test_payload_strict_utf8_json_nested_duplicates_nonfinite_recursion_size(raw):
    assert_invalid(headers(token(payload_raw=raw)))


@pytest.mark.parametrize("index", [0, 1, 2])
def test_padded_and_noncanonical_segments_are_rejected(index):
    parts = token().split(".")
    parts[index] += "="
    assert_invalid(headers(".".join(parts)))
    # A signature's low padding bits do not encode data, but must be canonical.
    parts = token().split(".")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    parts[2] = parts[2][:-1] + alphabet[alphabet.index(parts[2][-1]) ^ 1]
    assert_invalid(headers(".".join(parts)))


@pytest.mark.parametrize("size", [0, 31, 33])
def test_signature_is_exactly_32_bytes(size):
    parts = token().split(".")
    parts[2] = b64(b"x" * size)
    assert_invalid(headers(".".join(parts)))


@pytest.mark.parametrize("field,value", [("iss", "other"), ("iss", None), ("sub", ""), ("sub", None),
    ("sub", 1), ("sub", "a" * 129), ("sub", "é" * 65), ("sub", "a\n"), ("sub", "a\x00"),
    ("sub", "a\u200b"), ("sub", "\ud800"), ("iat", True), ("iat", 1000.0), ("iat", "1000"),
    ("exp", True), ("exp", 1300.0), ("exp", "1300"), ("iat", NOW + 31),
    ("exp", NOW - 30), ("exp", NOW), ("exp", NOW + 301)])
def test_required_claim_types_bounds_and_expiry(field, value):
    claims = {"sub": "synthetic", "iss": "open-webui", "iat": NOW, "exp": NOW + 300}
    claims[field] = value
    assert_invalid(headers(token(claims)))


def test_lifetime_and_skew_boundaries():
    for iat, exp in [(NOW, NOW), (NOW + 1, NOW), (NOW - 301, NOW), (NOW - 330, NOW - 30)]:
        assert_invalid(headers(token({"sub": "a", "iss": "open-webui", "iat": iat, "exp": exp})))
    valid = token({"sub": "é" * 64, "iss": "open-webui", "iat": NOW - 329, "exp": NOW - 29})
    assert verify_webui(headers(valid), binding(), NOW).subject == "é" * 64
    assert_invalid(headers(valid), bound=binding(clock_skew_seconds=0))
    future = token({"sub": "a", "iss": "open-webui", "iat": NOW + 30, "exp": NOW + 300})
    verify_webui(headers(future), binding(), NOW)


@pytest.mark.parametrize("now", [True, "1000", None, math.nan, math.inf, -math.inf, 10**1000,
    datetime.fromtimestamp(NOW)])
def test_clock_must_be_finite_epoch_or_aware_utc(now):
    assert_invalid(headers(token()), now=now)


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


def test_runtime_signer_collision_is_rejected_across_different_references():
    profiles = (profile(), profile(credential_id="second", instance="second", signer_env="SECOND_SIGNER"))
    with pytest.raises(WebUIIdentityError):
        load_webui_bindings(profiles, env={"SYNTHETIC_WEBUI_SIGNER": SIGNER.decode(), "SECOND_SIGNER": "  " + SIGNER.decode() + "\n"})
    with pytest.raises(WebUIIdentityError):
        select_webui_binding((binding(), binding()), caller())
    with pytest.raises(WebUIIdentityError):
        select_webui_binding(tuple(WebUIBinding(p, SIGNER) for p in profiles), caller())


@pytest.mark.parametrize("value", [None, "", "short", "x" * 4097, 32, b"x" * 32])
def test_runtime_signer_is_present_bounded_and_redacted(value):
    with pytest.raises(WebUIIdentityError) as caught:
        load_webui_bindings((profile(),), env={"SYNTHETIC_WEBUI_SIGNER": value})
    assert str(caught.value) == "invalid forwarded identity"


def test_protected_signer_file_is_read_by_existing_guard(tmp_path):
    path = tmp_path / "signer"
    path.write_bytes(SIGNER + b"\n")
    path.chmod(0o600)
    configured = profile(signer_env=None, signer_file=str(path))
    bound = load_webui_bindings((configured,), env={})[0]
    verify_webui(headers(token()), bound, NOW)
    if os.name != "nt":
        path.chmod(0o644)
        with pytest.raises(WebUIIdentityError):
            load_webui_bindings((configured,), env={})
        path.chmod(0o600)
        link = tmp_path / "link"
        link.symlink_to(path)
        with pytest.raises(WebUIIdentityError):
            load_webui_bindings((profile(signer_env=None, signer_file=str(link)),), env={})


def test_end_user_closed_schema_and_no_invented_connect_mapping():
    end_user = verify_webui(headers(token()), binding(), NOW)
    assert end_user.state == "verified_unmapped" and end_user.owner is None
    with pytest.raises(IdentityError):
        EndUser.from_dict({**end_user.to_dict(), "role": "admin"})
    with pytest.raises(IdentityError):
        replace(end_user, state="verified")
    with pytest.raises(IdentityError):
        replace(end_user, owner="human:synthetic")
    with pytest.raises(IdentityError):
        forwarded_caller(legacy_caller(), end_user)
    saved = forwarded_caller(caller(), end_user).to_dict()
    with pytest.raises(IdentityError):
        CallerSnapshot.from_dict({**saved, "end_user": {**saved["end_user"], "email": "private"}})
