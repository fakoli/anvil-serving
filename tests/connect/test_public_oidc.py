"""Portable native-public OIDC contract tests; runnable with stdlib unittest."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from anvil_serving.connect import config, manage
from anvil_serving.connect.config import ManifestError, canonical_manifest, validate_manifest
from anvil_serving.connect.render import render, render_for_inspection


ROOT = Path(__file__).parents[2]


def _manifest() -> dict:
    return json.loads((ROOT / "connect/examples/deployment.json").read_text())


def _native() -> dict:
    return {
        "client_id": "native-example", "client_name": "Native Example", "public": True,
        "redirect_uris": ["app.example.connect.ios:/oauth/callback"],
    }


def _confidential() -> dict:
    return {
        "client_id": "web-integration", "client_name": "Web Integration",
        "client_secret_file": "/etc/anvil-connect/secrets/web-oidc-client-secret",
        "redirect_uris": ["https://app.example.test/oidc/callback"], "groups_scope": True,
    }


def _with_clients(*clients: dict) -> dict:
    value = _manifest()
    value["authelia"]["additional_oidc_clients"] = list(clients)
    return value


def _isolated(value: dict) -> dict:
    value = copy.deepcopy(value)
    del value["service_user"]
    value["service_identities"] = {
        "gateway": {"uid": 1201, "gid": 1201}, "edge": {"uid": 1202, "gid": 1202},
        "idp": {"uid": 1203, "gid": 1203},
        "connectors": {"dashboard": {"uid": 1204, "gid": 1204}},
        "clients": {"dashboard-api": {"uid": 1205, "gid": 1205}},
        "ingress": {"group_id": 1290, "directory": "/run/anvil-connect/ingress"},
    }
    limit = {"memory_max_bytes": 268435456, "tasks_max": 64}
    value["service_limits"] = {
        "gateway": dict(limit), "edge": dict(limit), "idp": dict(limit),
        "connectors": {"dashboard": dict(limit)}, "clients": {"dashboard-api": dict(limit)},
    }
    value["caddy"]["state_directory"] = "/var/lib/anvil-connect/caddy"
    return value


class PublicOIDCContractTests(unittest.TestCase):
    def test_secretless_native_client_normalizes_and_round_trips(self) -> None:
        native = _native()
        native["redirect_uris"] = [
            "app.example.connect.ios:/oauth/second", "app.example.connect.ios:/oauth/callback",
        ]
        normalized = validate_manifest(_with_clients(native))
        self.assertEqual(normalized["authelia"]["additional_oidc_clients"], [{
            "client_id": "native-example", "client_name": "Native Example", "public": True,
            "redirect_uris": [
                "app.example.connect.ios:/oauth/callback", "app.example.connect.ios:/oauth/second",
            ], "groups_scope": False,
        }])
        self.assertEqual(validate_manifest(normalized), normalized)

    def test_public_client_renders_fixed_code_pkce_and_secretless_auth(self) -> None:
        text = render(_isolated(_with_clients(_native())))["files"]["authelia/configuration.yml"]
        block = text.split("      - client_id: 'native-example'\n", 1)[1]
        self.assertEqual(block, """        client_name: 'Native Example'
        consent_mode: explicit
        public: true
        authorization_policy: two_factor
        require_pkce: true
        pkce_challenge_method: S256
        response_types:
          - code
        grant_types:
          - authorization_code
        scopes:
          - openid
          - email
          - profile
        id_token_signed_response_alg: RS256
        token_endpoint_auth_method: none
        redirect_uris:
          - 'app.example.connect.ios:/oauth/callback'
""")

    def test_public_groups_scope_is_explicit_opt_in(self) -> None:
        native = _native()
        native["groups_scope"] = True
        text = render(_isolated(_with_clients(native)))["files"]["authelia/configuration.yml"]
        block = text.split("      - client_id: 'native-example'\n", 1)[1]
        self.assertIn("          - profile\n          - groups\n", block)
        self.assertNotIn("sector_identifier_uri", text)

    def test_public_rejects_every_secret_file_declaration(self) -> None:
        for secret in ("/protected/native-secret", "", None):
            with self.subTest(secret=secret):
                native = _native()
                native["client_secret_file"] = secret
                with self.assertRaisesRegex(ManifestError, "must omit client_secret_file"):
                    validate_manifest(_with_clients(native))

    def test_public_flag_requires_an_actual_boolean(self) -> None:
        for flag in ("true", "false", 1, 0, None, [], {}):
            with self.subTest(flag=flag):
                native = _native()
                native["public"] = flag
                with self.assertRaisesRegex(ManifestError, "public.*must be a boolean"):
                    validate_manifest(_with_clients(native))

    def test_public_rejects_ambiguous_or_non_native_callbacks(self) -> None:
        callbacks = (
            "native:/oauth/callback", "APP.example.connect.ios:/oauth/callback",
            "1app.example.connect.ios:/oauth/callback", "app_.example.connect.ios:/oauth/callback",
            "app.example.connect.ios.://oauth/callback", "app.example.connect.ios:oauth/callback",
            "app.example.connect.ios://oauth/callback", "app.example.connect.ios:///oauth/callback",
            "app.example.connect.ios:/", "app.example.connect.ios:/oauth/callback/",
            "app.example.connect.ios:/oauth/./callback", "app.example.connect.ios:/oauth/../callback",
            "app.example.connect.ios:/oauth//callback", "app.example.connect.ios:/oauth/*",
            "app.example.connect.ios:/oauth/%63allback", "app.example.connect.ios:/oauth\\callback",
            "app.example.connect.ios:/oauth/callback?next=/", "app.example.connect.ios:/oauth/callback?",
            "app.example.connect.ios:/oauth/callback#fragment", "app.example.connect.ios:/oauth/callback#",
            "app.example.connect.ios:/oauth/ callback", "app.example.connect.ios:/oauth/\x7fcallback",
            "app.example.connect.ios:/oauth/café", "app.example.connect.ios://user@host/callback",
            "app.example.connect.ios:/oauth/[callback]", "app.example.connect.ios:/oauth/{callback}",
            'app.example.connect.ios:/oauth/"callback"', "app.example.connect.ios:/oauth/<callback>",
            "app.example.connect.ios:/oauth/callback|next", "app.example.connect.ios:/oauth/`callback`",
            "app.example.connect.ios@other:/oauth/callback", "app.example.connect.ios:443/oauth/callback",
            "http://app.example.test/oauth/callback", "https://app.example.test/oauth/callback",
            "http://127.0.0.1:8765/oauth/callback", "file:/oauth/callback", "javascript:/oauth/callback",
        )
        for callback in callbacks:
            with self.subTest(callback=callback):
                native = _native()
                native["redirect_uris"] = [callback]
                with self.assertRaisesRegex(ManifestError, "canonical native redirect URI"):
                    validate_manifest(_with_clients(native))

    def test_duplicate_client_ids_include_public_confidential_and_connect_clients(self) -> None:
        native, confidential = _native(), _confidential()
        confidential["client_id"] = native["client_id"]
        for clients in ((native, copy.deepcopy(native)), (native, confidential)):
            with self.subTest(clients=clients):
                with self.assertRaisesRegex(ManifestError, "unique including the Connect client"):
                    validate_manifest(_with_clients(*clients))
        native["client_id"] = _manifest()["gateway"]["oidc"]["client_id"]
        with self.assertRaisesRegex(ManifestError, "unique including the Connect client"):
            validate_manifest(_with_clients(native))

    def test_duplicate_native_callbacks_are_rejected(self) -> None:
        native = _native()
        native["redirect_uris"] *= 2
        with self.assertRaisesRegex(ManifestError, "duplicate redirect URIs"):
            validate_manifest(_with_clients(native))

    def test_client_security_profile_cannot_be_overridden(self) -> None:
        for key, supplied in (
            ("require_pkce", False), ("pkce_challenge_method", "plain"),
            ("grant_types", ["implicit"]), ("response_types", ["token"]),
            ("token_endpoint_auth_method", "client_secret_post"), ("authorization_policy", "one_factor"),
            ("consent_mode", "implicit"), ("scopes", ["offline_access"]),
        ):
            with self.subTest(key=key):
                native = _native()
                native[key] = supplied
                with self.assertRaises(ManifestError):
                    validate_manifest(_with_clients(native))

    def test_confidential_bytes_are_preserved_when_public_is_omitted(self) -> None:
        value = _with_clients(_confidential())
        self.assertEqual(hashlib.sha256(canonical_manifest(value)).hexdigest(),
                         "b87e29a5a8c82fddfba8eeddb3da37241b7c591967c51446c8ea5cb54ea95e3e")
        text = render_for_inspection(value)["files"]["authelia/configuration.yml"]
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(),
                         "e9a4900ce5b5f168c60b0be30edff91cb78411ae479a7b2fcd782d46a97a99ce")

    def test_explicit_false_keeps_the_confidential_profile(self) -> None:
        baseline = _with_clients(_confidential())
        explicit = copy.deepcopy(baseline)
        explicit["authelia"]["additional_oidc_clients"][0]["public"] = False
        self.assertEqual(canonical_manifest(explicit), canonical_manifest(baseline))
        self.assertEqual(render_for_inspection(explicit), render_for_inspection(baseline))

    def test_confidential_clients_still_require_secrets_and_https_callbacks(self) -> None:
        for flag in (None, False):
            with self.subTest(flag=flag):
                confidential = _confidential()
                if flag is not None:
                    confidential["public"] = flag
                del confidential["client_secret_file"]
                with self.assertRaisesRegex(ManifestError, "missing keys: client_secret_file"):
                    validate_manifest(_with_clients(confidential))
                confidential = _confidential()
                confidential["redirect_uris"] = _native()["redirect_uris"]
                with self.assertRaisesRegex(ManifestError, "canonical HTTPS redirect URI"):
                    validate_manifest(_with_clients(confidential))

    def test_mixed_clients_preserve_protected_secret_boundaries(self) -> None:
        value = _with_clients(_native(), _confidential())
        normalized = validate_manifest(value)
        references = config._authelia_secret_files(normalized["authelia"])
        self.assertIn("/etc/anvil-connect/secrets/web-oidc-client-secret", references)
        self.assertEqual(len(references), 8)
        for secret in (value["authelia"]["client_secret_file"],
                       value["authelia"]["state_directory"] + "/web-secret",
                       value["config_root"] + "/web-secret"):
            with self.subTest(secret=secret):
                invalid = copy.deepcopy(value)
                invalid["authelia"]["additional_oidc_clients"][1]["client_secret_file"] = secret
                with self.assertRaises(ManifestError):
                    validate_manifest(invalid)

    def test_managed_preflight_checks_confidential_files_with_public_clients_present(self) -> None:
        value = _isolated(_with_clients(_native(), _confidential()))
        expected = {
            Path(value["caddy"]["tls"]["certificate_file"]): (1202, 1202, True),
            Path(value["caddy"]["tls"]["key_file"]): (1202, 1202, False),
            **{Path(value["authelia"][name]): (1203, 1203, False) for name in (
                "client_secret_file", "session_secret_file", "storage_encryption_key_file",
                "identity_validation_secret_file", "oidc_hmac_secret_file", "oidc_rsa_private_key_file",
            )},
            Path("/etc/anvil-connect/secrets/web-oidc-client-secret"): (1203, 1203, False),
        }

        def checked_file(path: Path, uid: int, gid: int, *, public: bool = False) -> None:
            self.assertEqual(expected.pop(path), (uid, gid, public))

        # Only host filesystem access is replaced; role selection and the
        # declaration's full protected-reference preflight execute normally.
        with patch.object(manage, "_safe_consumed_file", checked_file), \
                patch.object(manage, "_safe_authelia_users_file", return_value=None):
            manage._validate_gateway_files(value)
        self.assertEqual(expected, {})


if __name__ == "__main__":
    unittest.main()
