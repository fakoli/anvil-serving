"""Portable native-public OIDC contract tests; runnable with stdlib unittest."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from anvil_serving.connect import config
from anvil_serving.connect.config import ManifestError, validate_manifest
from anvil_serving.connect.render import render


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


if __name__ == "__main__":
    unittest.main()
