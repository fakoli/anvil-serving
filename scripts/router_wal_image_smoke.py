"""Disposable native-WAL conversion smoke used while building the router image."""

import json
import tempfile
from pathlib import Path

from anvil_serving.router.keys import KeyStore, _migrate_offline


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="anvil-router-wal-smoke-") as temporary_directory:
        scratch = Path(temporary_directory)
        database = scratch / "keys.sqlite3"
        KeyStore.initialize(database)

        config = scratch / "router.toml"
        config.write_text(
            '[server]\nauth_env="SYNTHETIC_MASTER"\napi_keys_path='
            + json.dumps(str(database))
            + '\nadmission_state_path=' + json.dumps(str(scratch / "admission.json"))
            + '\nrouter_owner_id="synthetic-owner"'
            + '\nrouter_owner_roster=["synthetic-owner"]'
            + '\nusage_domain_id="synthetic-domain"\n'
        )
        config.chmod(0o600)

        conversion = _migrate_offline(
            str(config),
            str(scratch / "before.sqlite3"),
            journal_mode="WAL",
            _managed_worker=True,
        )
        if conversion.get("journal_mode") != "wal" or conversion.get("offline") is not True:
            raise RuntimeError("native WAL offline conversion did not complete")

        store = KeyStore(database)
        metadata, token = store.create(
            "router image smoke", ["llm.primary"], ["/v1/chat/completions"]
        )
        if store.authenticate(token).key_id != metadata["key_id"]:
            raise RuntimeError("native WAL key-store round trip failed")
        with store._connect() as connection:
            if connection.execute("PRAGMA journal_mode").fetchone() != ("wal",):
                raise RuntimeError("key store did not remain in WAL mode")


if __name__ == "__main__":
    main()
