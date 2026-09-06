from dataclasses import asdict
import json
import os
from pathlib import Path
import traceback

import pytest

from anvil_serving.control_plane.authorization import (
    NODE_ADMIN_BOOTSTRAP,
    WORKLOADS_READ,
    AuthorizationError,
    check_scope,
    load_authorization_policy,
)


TOKEN_A = "a" * 16
TOKEN_B = "b" * 16
LEGACY = "l" * 16


def write_policy(tmp_path: Path, clients: list[dict], **extra: object) -> Path:
    payload: dict[str, object] = {"schema_version": 1, "clients": clients}
    payload.update(extra)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def client(client_id: str, token_name: str, scopes: list[str]) -> dict:
    return {"id": client_id, "credential_env": token_name, "scopes": scopes}


def load(tmp_path: Path, clients: list[dict], **kwargs: object):
    return load_authorization_policy(
        write_policy(tmp_path, clients), env={"TOKEN_A": TOKEN_A, "TOKEN_B": TOKEN_B}, **kwargs
    )


def test_valid_least_privilege_and_explicit_both_scopes(tmp_path: Path):
    policy = load(
        tmp_path,
        [
            client("reader", "TOKEN_A", [WORKLOADS_READ]),
            client("admin", "TOKEN_B", [WORKLOADS_READ, NODE_ADMIN_BOOTSTRAP]),
        ],
    )

    reader = check_scope(policy, TOKEN_A, WORKLOADS_READ)
    assert reader.allowed and reader.client_id == "reader" and reader.scopes == frozenset((WORKLOADS_READ,))
    assert check_scope(policy, TOKEN_A, NODE_ADMIN_BOOTSTRAP).code == "authorization_scope_denied"
    assert check_scope(policy, TOKEN_B, NODE_ADMIN_BOOTSTRAP).allowed


def test_missing_policy_unknown_scope_and_malformed_presented_token_deny(tmp_path: Path):
    assert check_scope(None, TOKEN_A, WORKLOADS_READ).code == "authorization_policy_missing"
    assert check_scope(None, TOKEN_A, "other").code == "authorization_scope_unknown"
    policy = load(tmp_path, [client("reader", "TOKEN_A", [WORKLOADS_READ])])
    assert check_scope(policy, "short", WORKLOADS_READ).code == "authorization_credential_malformed"


def test_non_string_scope_and_wrong_policy_type_are_fixed_denials():
    assert check_scope(None, TOKEN_A, []).code == "authorization_scope_unknown"
    assert check_scope(None, TOKEN_A, {}).code == "authorization_scope_unknown"
    assert check_scope("wrong", TOKEN_A, WORKLOADS_READ).code == "authorization_policy_malformed"


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version": 1, "schema_version": 1, "clients": []}',
        b'{"schema_version": true, "clients": []}',
        b'{"schema_version": 1, "clients": [], "extra": 1}',
        b'{"schema_version": 1, "clients": [{"id":"reader","credential_env":"TOKEN_A","scopes":["bad"]}]}',
        b'{"schema_version": 1, "clients": [{"id":"reader","credential_env":"TOKEN_A","scopes":["workloads:read","workloads:read"]}]}',
    ],
)
def test_malformed_closed_schema_denies(tmp_path: Path, payload: bytes):
    path = tmp_path / "policy.json"
    path.write_bytes(payload)
    with pytest.raises(AuthorizationError) as exc:
        load_authorization_policy(path, env={"TOKEN_A": TOKEN_A})
    assert exc.value.code == "authorization_policy_malformed"


def test_policy_size_client_ids_references_and_resolved_material_must_be_unique(tmp_path: Path):
    path = tmp_path / "policy.json"
    path.write_bytes(b" " * (64 * 1024 + 1))
    with pytest.raises(AuthorizationError):
        load_authorization_policy(path)

    with pytest.raises(AuthorizationError):
        load(tmp_path, [client("same", "TOKEN_A", [WORKLOADS_READ]), client("same", "TOKEN_B", [WORKLOADS_READ])])
    with pytest.raises(AuthorizationError):
        load(tmp_path, [client("one", "TOKEN_A", [WORKLOADS_READ]), client("two", "TOKEN_A", [NODE_ADMIN_BOOTSTRAP])])
    with pytest.raises(AuthorizationError):
        load_authorization_policy(
            write_policy(tmp_path, [client("one", "TOKEN_A", [WORKLOADS_READ]), client("two", "TOKEN_B", [NODE_ADMIN_BOOTSTRAP])]),
            env={"TOKEN_A": TOKEN_A, "TOKEN_B": TOKEN_A},
        )


def test_legacy_equality_after_normalization_is_denied(tmp_path: Path):
    with pytest.raises(AuthorizationError) as exc:
        load(tmp_path, [client("reader", "TOKEN_A", [WORKLOADS_READ])], legacy_token=" \r" + TOKEN_A + "\n")
    assert exc.value.code == "authorization_policy_malformed"


def test_short_legacy_material_does_not_disable_a_valid_scoped_policy(tmp_path: Path):
    policy = load(tmp_path, [client("reader", "TOKEN_A", [WORKLOADS_READ])], legacy_token="short")
    assert check_scope(policy, TOKEN_A, WORKLOADS_READ).allowed


@pytest.mark.parametrize("size, expected", [(15, False), (16, True), (4096, True), (4097, False)])
def test_utf8_token_boundaries(tmp_path: Path, size: int, expected: bool):
    token = "x" * size
    path = write_policy(tmp_path, [client("reader", "TOKEN_A", [WORKLOADS_READ])])
    if expected:
        policy = load_authorization_policy(path, env={"TOKEN_A": token})
        assert check_scope(policy, "\r" + token + "\n", WORKLOADS_READ).allowed
    else:
        with pytest.raises(AuthorizationError) as exc:
            load_authorization_policy(path, env={"TOKEN_A": token})
        assert exc.value.code == "authorization_credential_malformed"


def test_file_credentials_reject_links_urls_and_non_utf8(tmp_path: Path):
    credential = tmp_path / "credential"
    credential.write_text(TOKEN_A, encoding="utf-8")
    policy_path = write_policy(
        tmp_path,
        [{"id": "reader", "credential_file": str(credential), "scopes": [WORKLOADS_READ]}],
    )
    policy = load_authorization_policy(policy_path)
    assert check_scope(policy, TOKEN_A, WORKLOADS_READ).allowed

    link = tmp_path / "credential-link"
    try:
        link.symlink_to(credential)
    except OSError:
        pytest.skip("symlinks unavailable")
    bad_link = write_policy(tmp_path, [{"id": "reader", "credential_file": str(link), "scopes": [WORKLOADS_READ]}])
    with pytest.raises(AuthorizationError):
        load_authorization_policy(bad_link)
    bad_url = write_policy(tmp_path, [{"id": "reader", "credential_file": "https://invalid", "scopes": [WORKLOADS_READ]}])
    with pytest.raises(AuthorizationError):
        load_authorization_policy(bad_url)
    credential.write_bytes(b"\xff" * 16)
    policy_path = write_policy(
        tmp_path,
        [{"id": "reader", "credential_file": str(credential), "scopes": [WORKLOADS_READ]}],
    )
    with pytest.raises(AuthorizationError):
        load_authorization_policy(policy_path)


def test_safe_representations_and_exception_chains_do_not_expose_material(tmp_path: Path):
    secret = "super-secret-material-that-must-not-render"
    reference = "PRIVATE_REFERENCE_NAME"
    policy_path = write_policy(tmp_path, [client("reader", reference, [WORKLOADS_READ])])
    policy = load_authorization_policy(policy_path, env={reference: secret})
    assert secret not in repr(policy)
    assert reference not in repr(policy)
    with pytest.raises(AuthorizationError) as exc:
        load_authorization_policy(policy_path, env={reference: "short"})
    rendered = str(exc.value) + repr(exc.value) + "".join(traceback.format_exception(exc.type, exc.value, exc.tb))
    assert secret not in rendered
    assert reference not in rendered
    assert str(policy_path) not in rendered


def test_generic_dataclass_and_json_diagnostics_do_not_expose_credentials(tmp_path: Path):
    secret = "synthetic-material-that-must-remain-private"
    reference = "SYNTHETIC_PRIVATE_REFERENCE"
    policy = load_authorization_policy(
        write_policy(tmp_path, [client("reader", reference, [WORKLOADS_READ])]),
        env={reference: secret},
    )
    rendered = repr(policy) + repr(policy.principals[0])
    projected = json.dumps(asdict(policy), default=repr, sort_keys=True)
    principal_projection = json.dumps(asdict(policy.principals[0]), default=repr, sort_keys=True)
    assert secret not in rendered + projected + principal_projection
    assert reference not in rendered + projected + principal_projection


def test_surrogates_and_injected_reader_or_mapping_fail_with_fixed_errors(tmp_path: Path):
    policy_path = write_policy(tmp_path, [client("reader", "TOKEN_A", [WORKLOADS_READ])])
    with pytest.raises(AuthorizationError) as surrogate:
        load_authorization_policy(policy_path, env={"TOKEN_A": "x" * 15 + "\ud800"})
    assert surrogate.value.code == "authorization_credential_malformed"

    surrogate_reference = write_policy(
        tmp_path,
        [{"id": "reader", "credential_file": str(tmp_path / "\ud800"), "scopes": [WORKLOADS_READ]}],
    )
    with pytest.raises(AuthorizationError) as bad_reference:
        load_authorization_policy(surrogate_reference)
    assert bad_reference.value.code == "authorization_policy_malformed"

    policy_path = write_policy(tmp_path, [client("reader", "TOKEN_A", [WORKLOADS_READ])])

    class BrokenMapping(dict[str, str]):
        def get(self, key: str, default: object = None) -> str:
            raise RuntimeError("synthetic mapping failure: " + key)

    with pytest.raises(AuthorizationError) as mapping_failure:
        load_authorization_policy(policy_path, env=BrokenMapping())
    assert mapping_failure.value.code == "authorization_credential_malformed"

    def broken_reader(kind: str, reference: str) -> str:
        raise RuntimeError("synthetic reader failure: " + kind + reference)

    with pytest.raises(AuthorizationError) as reader_failure:
        load_authorization_policy(policy_path, secret_reader=broken_reader)
    rendered = "".join(traceback.format_exception(reader_failure.type, reader_failure.value, reader_failure.tb))
    assert "synthetic reader failure" not in rendered
    assert "TOKEN_A" not in rendered


def test_deeply_nested_json_is_a_fixed_malformed_policy_error(tmp_path: Path):
    path = tmp_path / "policy.json"
    path.write_bytes(b'{"schema_version":1,"clients":' + b"[" * 2100 + b"]" * 2100 + b"}")
    with pytest.raises(AuthorizationError) as exc:
        load_authorization_policy(path)
    assert exc.value.code == "authorization_policy_malformed"


def test_huge_json_integer_is_a_fixed_malformed_policy_error(tmp_path: Path):
    path = tmp_path / "policy.json"
    path.write_bytes(b'{"schema_version":' + b"9" * 5000 + b',"clients":[]}')
    with pytest.raises(AuthorizationError) as exc:
        load_authorization_policy(path)
    assert exc.value.code == "authorization_policy_malformed"


@pytest.mark.parametrize("source", ["env", "reader", "file"])
def test_scoped_credential_normalized_boundaries_match_all_sources(tmp_path: Path, source: str):
    token = "x" * 4096
    item: dict[str, object] = {"id": "reader", "scopes": [WORKLOADS_READ]}
    kwargs: dict[str, object] = {}
    if source == "env":
        item["credential_env"] = "TOKEN"
        kwargs["env"] = {"TOKEN": token + "\n"}
    elif source == "reader":
        item["credential_env"] = "TOKEN"
        kwargs["secret_reader"] = lambda _kind, _reference: token + "\n"
    else:
        credential = tmp_path / "credential"
        credential.write_text(token + "\n", encoding="utf-8")
        item["credential_file"] = str(credential)
    policy = load_authorization_policy(write_policy(tmp_path, [item]), **kwargs)
    assert check_scope(policy, token, WORKLOADS_READ).allowed

    if source == "env":
        kwargs["env"] = {"TOKEN": "x" * 4097}
    elif source == "reader":
        kwargs["secret_reader"] = lambda _kind, _reference: "x" * 4097
    else:
        credential.write_text("x" * 4097, encoding="utf-8")
    with pytest.raises(AuthorizationError) as exc:
        load_authorization_policy(write_policy(tmp_path, [item]), **kwargs)
    assert exc.value.code == "authorization_credential_malformed"


@pytest.mark.parametrize("source", ["env", "reader", "file"])
def test_credential_raw_source_bound_is_enforced_before_normalization(tmp_path: Path, source: str):
    within_limit = " " * (64 * 1024 - 16) + TOKEN_A
    item: dict[str, object] = {"id": "reader", "scopes": [WORKLOADS_READ]}
    kwargs: dict[str, object] = {}
    if source == "env":
        item["credential_env"] = "TOKEN"
        kwargs["env"] = {"TOKEN": within_limit}
    elif source == "reader":
        item["credential_env"] = "TOKEN"
        kwargs["secret_reader"] = lambda _kind, _reference: within_limit
    else:
        credential = tmp_path / "credential"
        credential.write_text(within_limit, encoding="utf-8")
        item["credential_file"] = str(credential)
    assert check_scope(load_authorization_policy(write_policy(tmp_path, [item]), **kwargs), TOKEN_A, WORKLOADS_READ).allowed

    overflow = within_limit + " "
    if source == "env":
        kwargs["env"] = {"TOKEN": overflow}
    elif source == "reader":
        kwargs["secret_reader"] = lambda _kind, _reference: overflow
    else:
        credential.write_text(overflow, encoding="utf-8")
    with pytest.raises(AuthorizationError) as exc:
        load_authorization_policy(write_policy(tmp_path, [item]), **kwargs)
    assert exc.value.code == "authorization_credential_malformed"


def test_unsafe_local_references_are_rejected_before_secret_reader(tmp_path: Path):
    calls: list[tuple[str, str]] = []

    def reader(kind: str, reference: str) -> str:
        calls.append((kind, reference))
        return TOKEN_A

    for reference in ("https://invalid", "//network/share", r"\\server\share", r"\\?\C:\device"):
        policy_path = write_policy(
            tmp_path,
            [{"id": "reader", "credential_file": reference, "scopes": [WORKLOADS_READ]}],
        )
        with pytest.raises(AuthorizationError) as exc:
            load_authorization_policy(policy_path, secret_reader=reader)
        assert exc.value.code == "authorization_policy_malformed"
    assert not calls
    with pytest.raises(AuthorizationError) as policy_path_error:
        load_authorization_policy("//network/share/policy.json", secret_reader=reader)
    assert policy_path_error.value.code == "authorization_policy_malformed"
    assert not calls


def test_policy_and_credential_symlink_components_are_rejected(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    policy = target / "policy.json"
    policy.write_text(json.dumps({"schema_version": 1, "clients": []}), encoding="utf-8")
    credential = target / "credential"
    credential.write_text(TOKEN_A, encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(AuthorizationError):
        load_authorization_policy(link / "policy.json")
    credential_policy = write_policy(
        tmp_path,
        [{"id": "reader", "credential_file": str(link / "credential"), "scopes": [WORKLOADS_READ]}],
    )
    with pytest.raises(AuthorizationError):
        load_authorization_policy(credential_policy)


@pytest.mark.skipif(os.name != "nt", reason="Windows handle binding is Windows-specific")
def test_windows_reads_bind_the_expected_handle_identity(monkeypatch, tmp_path: Path):
    import anvil_serving.control_plane.authorization as authorization

    path = tmp_path / "policy.json"
    path.write_text("{}", encoding="utf-8")
    expected = (1, 2, 3, 4, 5)
    seen: dict[str, object] = {}
    monkeypatch.setattr(authorization, "_windows_file_identity", lambda value: expected)

    def read(path_value: Path, **kwargs: object) -> bytes:
        seen["path"] = path_value
        seen.update(kwargs)
        return b"{}"

    monkeypatch.setattr(authorization, "_read_bounded", read)
    assert authorization._read_bounded_regular_file(path, 64, "authorization_policy_malformed") == b"{}"
    assert seen["expected_windows_identity"] == expected


def test_scope_check_compares_every_principal_without_token_equality(monkeypatch, tmp_path: Path):
    policy = load(
        tmp_path,
        [client("reader", "TOKEN_A", [WORKLOADS_READ]), client("admin", "TOKEN_B", [NODE_ADMIN_BOOTSTRAP])],
    )
    import anvil_serving.control_plane.authorization as authorization

    calls: list[tuple[bytes, bytes]] = []
    original = authorization.hmac.compare_digest

    def checked(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr(authorization.hmac, "compare_digest", checked)
    decision = check_scope(policy, TOKEN_A, WORKLOADS_READ)
    assert decision.allowed
    assert len(calls) == 2


def test_malformed_token_and_wrong_scope_do_not_grant(tmp_path: Path):
    policy = load(tmp_path, [client("reader", "TOKEN_A", [WORKLOADS_READ])])
    assert check_scope(policy, "bad\nvalue" + "x" * 16, WORKLOADS_READ).code == "authorization_credential_malformed"
    assert check_scope(policy, TOKEN_B, WORKLOADS_READ).code == "authorization_scope_denied"
