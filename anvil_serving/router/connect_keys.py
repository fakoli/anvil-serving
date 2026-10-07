"""Connect's narrow router-key broker; browser identity never comes from a caller.

Connect authenticates the browser and submits an actor through a separate service
credential. Owned keys require a live account-generation/authority-epoch check.
No cookie, prompt, bearer or IdP subject is retained in this database.
"""
from __future__ import annotations

import http.client
import json
import math
import ipaddress
import re
import sqlite3
import time
from urllib.parse import urlsplit

from .keys import KeyStoreError, _POST_PATHS
from ..connect.config import service_url

BROKER_PATH = "/v1/connect/keys"
HOME_PATH = "/_anvil-connect/home"
CHECK_PATH = HOME_PATH + "/router-principal"
_HUMAN = re.compile(r"human:[0-9a-f]{64}\Z")
_EPOCH = re.compile(r"[0-9a-f]{64}\Z")
_GENERATION = re.compile(r"[1-9][0-9]{0,19}\Z")


class Denied(KeyStoreError):
    pass


def home_url(value):
    value = service_url(value)
    parsed = urlsplit(value)
    host = parsed.hostname
    if (parsed.scheme != "https" or parsed.netloc != host or len(host) > 253 or "." not in host
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split("."))):
        raise ValueError("Connect Home requires canonical host-only HTTPS")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return value
    raise ValueError("Connect Home requires a declared hostname")


def identity(owner, generation, epoch):
    if (not isinstance(owner, str) or not _HUMAN.fullmatch(owner)
            or not isinstance(generation, str) or not _GENERATION.fullmatch(generation)
            or int(generation) > 2**64 - 1
            or not isinstance(epoch, str) or not _EPOCH.fullmatch(epoch)):
        raise Denied("invalid Connect identity")
    return owner, generation, epoch


def principal_checker(url, secret):
    parsed = urlsplit(home_url(url))

    def check(owner, generation, epoch):
        identity(owner, generation, epoch)
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = cls(parsed.hostname, parsed.port, timeout=2)
        try:
            connection.request("POST", CHECK_PATH, json.dumps({"principal": owner, "generation": generation, "epoch": epoch}),
                               {"Authorization": "Bearer " + secret, "Content-Type": "application/json", "Connection": "close"})
            response = connection.getresponse()
            # No redirects, retries, caches, response logging or credential forwarding.
            if response.status == 204:
                return True
            if response.status == 403:
                return False
            raise KeyStoreError("Connect account check unavailable")
        except (OSError, http.client.HTTPException):
            raise KeyStoreError("Connect account check unavailable") from None
        finally:
            connection.close()
    return check


def _create_schema(db):
    """Individual DDL for the caller's existing migration transaction."""
    db.execute("""CREATE TABLE connect_accounts (
                owner TEXT PRIMARY KEY, generation TEXT NOT NULL, epoch TEXT NOT NULL,
                revision INTEGER NOT NULL, status TEXT NOT NULL, models TEXT NOT NULL,
                paths TEXT NOT NULL, rpm INTEGER NOT NULL, expires_days INTEGER NOT NULL,
                updated_at INTEGER NOT NULL, actor TEXT NOT NULL)""")
    db.execute("""CREATE TABLE connect_key_owners (
                key_id TEXT PRIMARY KEY, owner TEXT NOT NULL, generation TEXT NOT NULL,
                epoch TEXT NOT NULL, revision INTEGER NOT NULL)""")
    db.execute("CREATE INDEX connect_owner_keys ON connect_key_owners(owner)")
    db.execute("CREATE TABLE connect_sequence (revision INTEGER NOT NULL)")
    db.execute("INSERT INTO connect_sequence VALUES (0)")


def migrate(store):
    """Atomic additive migration; never downgrade a newer accounting store."""
    with store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 1:
            _create_schema(db)
            db.execute("PRAGMA user_version=2")
            version = 2
        elif version not in (2, 3):
            raise KeyStoreError("credential store format is unsupported")
        db.execute("COMMIT")
    store.version = version


def _next_revision(db):
    # The caller holds BEGIN IMMEDIATE: deleted rows cannot cause a CAS ABA.
    current = db.execute("SELECT revision FROM connect_sequence").fetchall()
    if len(current) != 1 or type(current[0][0]) is not int or not 0 <= current[0][0] < 2**53-1:
        raise KeyStoreError("Connect policy sequence unavailable")
    revision = current[0][0]+1
    db.execute("UPDATE connect_sequence SET revision=?", (revision,))
    return revision


def _account(db, owner):
    row = db.execute("SELECT owner, generation, epoch, revision, status, models, paths, rpm, expires_days, updated_at, actor FROM connect_accounts WHERE owner=?", (owner,)).fetchone()
    if row is None:
        return None
    account = dict(zip(("owner", "generation", "epoch", "revision", "status", "models", "paths", "rpm", "expires_days", "updated_at", "actor"), row))
    identity(account["owner"], account["generation"], account["epoch"])
    if account["status"] not in {"pending", "approved", "denied"} or type(account["revision"]) is not int or not 1 <= account["revision"] < 2**53:
        raise KeyStoreError("invalid Connect access policy")
    try:
        account["models"], account["paths"] = json.loads(account["models"]), json.loads(account["paths"])
        if account["status"] == "approved":
            from .keys import KeyStore
            KeyStore._grants(account["models"], account["paths"])
            if type(account["rpm"]) is not int or not 1 <= account["rpm"] <= 100000 or type(account["expires_days"]) is not int or not 1 <= account["expires_days"] <= 90:
                raise ValueError()
    except (ValueError, TypeError):
        raise KeyStoreError("invalid Connect access policy") from None
    return account


def _approved(db, binding):
    owner, generation, epoch, revision = binding
    identity(owner, generation, epoch)
    if type(revision) is not int or not 1 <= revision < 2**53:
        raise Denied("invalid Connect access revision")
    account = _account(db, owner)
    if account is None or account["status"] != "approved" or (account["generation"], account["epoch"], account["revision"]) != (generation, epoch, revision):
        raise Denied("router access is not approved")
    return account


def authorize_creation(db, binding, models, paths, rpm, days):
    account = _approved(db, binding)
    if (not set(models).issubset(account["models"]) or not set(paths).issubset(account["paths"])
            or rpm > account["rpm"] or days is None or days > account["expires_days"]):
        raise Denied("key exceeds approved access")
    count = db.execute("SELECT COUNT(*) FROM keys JOIN connect_key_owners USING(key_id) WHERE owner=? AND revoked_at IS NULL AND expires_at>?", (binding[0], int(time.time()))).fetchone()[0]
    if count >= 10:
        raise Denied("account key limit reached")


def owned_account(db, key_id):
    """Read the complete binding and approved policy in the held transaction."""
    row = db.execute("SELECT owner,generation,epoch,revision FROM connect_key_owners WHERE key_id=?", (key_id,)).fetchone()
    return (None, None) if row is None else (row, _approved(db, row))


def owned_binding(store, key_id):
    try:
        with store._connect() as db:
            row, _account = owned_account(db, key_id)
            if row is None:
                return None
        return row[:3]
    except sqlite3.Error:
        raise KeyStoreError("Connect key ownership unavailable") from None


def admit_owner(db, key_id, now):
    row, account = owned_account(db, key_id)
    if row is None:
        return 0
    rpm = account["rpm"]
    bucket_id = "connect:" + row[0]
    bucket = db.execute("SELECT tokens,updated_at FROM buckets WHERE key_id=?", (bucket_id,)).fetchone()
    tokens, updated = (float(rpm), now) if bucket is None else bucket
    if (type(rpm) is not int or not 1 <= rpm <= 100000 or type(tokens) not in (int, float)
            or type(updated) not in (int, float) or not math.isfinite(tokens) or not math.isfinite(updated)
            or not 0 <= tokens <= rpm):
        raise KeyStoreError("invalid Connect rate state")
    observed = max(now, updated)
    tokens = min(float(rpm), tokens + (observed - updated) * rpm / 60)
    retry = 0 if tokens >= 1 else max(1, math.ceil(observed - now + (1-tokens)*60/rpm))
    db.execute("INSERT INTO buckets VALUES (?,?,?) ON CONFLICT(key_id) DO UPDATE SET tokens=excluded.tokens,updated_at=excluded.updated_at", (bucket_id, tokens - 1 if not retry else tokens, observed))
    return retry


class ConnectKeys:
    def __init__(self, store, models):
        self.store, self.models = store, sorted(set(models))
        if len(self.models) > 256:
            raise KeyStoreError("Connect model catalog exceeds 256 entries")
        migrate(store)

    def dispatch(self, value):
        """Called only after the front door authenticates the dedicated broker."""
        if not isinstance(value, dict) or set(value) != {"principal", "generation", "epoch", "administrator", "operation"}:
            raise Denied("invalid Connect request")
        actor = identity(value["principal"], value["generation"], value["epoch"])
        admin, operation = value["administrator"], value["operation"]
        if type(admin) is not bool or not isinstance(operation, dict) or not isinstance(operation.get("action"), str):
            raise Denied("invalid Connect operation")
        if self.store.owner_check is None or not self.store.owner_check(*actor):
            raise Denied("Connect account is unavailable")
        action = operation["action"]
        if action == "view" and set(operation) == {"action"}:
            return self.view(actor, admin)
        if action == "request" and set(operation) == {"action"}:
            with self.store._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                old = _account(db, actor[0])
                if old is None and db.execute("SELECT COUNT(*) FROM connect_accounts").fetchone()[0] >= 256:
                    raise Denied("access request capacity reached")
                if old is None or (old["generation"], old["epoch"]) != actor[1:]:
                    rev = _next_revision(db)
                    db.execute("INSERT INTO connect_accounts VALUES (?,?,?,?,?,'[]','[]',0,0,?,?) ON CONFLICT(owner) DO UPDATE SET generation=excluded.generation,epoch=excluded.epoch,revision=excluded.revision,status='pending',models='[]',paths='[]',rpm=0,expires_days=0,updated_at=excluded.updated_at,actor=excluded.actor", (*actor, rev, "pending", int(time.time()), actor[0]))
                # Same-generation denied requests remain denied until the operator changes them.
                db.execute("COMMIT")
            return {"ok": True}
        if action == "create" and set(operation) == {"action", "name", "models", "paths", "rpm", "expires_days", "revision"}:
            if type(operation["revision"]) is not int:
                raise Denied("invalid access revision")
            metadata, secret = self.store.create(operation["name"], operation["models"], operation["paths"], operation["rpm"], operation["expires_days"], owner=(*actor, operation["revision"]))
            return {"key": metadata, "secret": secret}
        if action == "revoke" and set(operation) == {"action", "key_id"} and isinstance(operation["key_id"], str):
            with self.store._connect() as db:
                result = db.execute("UPDATE keys SET revoked_at=COALESCE(revoked_at,?) WHERE key_id=? AND key_id IN (SELECT key_id FROM connect_key_owners WHERE owner=?)", (int(time.time()), operation["key_id"], actor[0]))
            if result.rowcount != 1:
                raise Denied("key unavailable")
            return {"ok": True}
        if action == "forget" and admin and set(operation) == {"action", "owner", "revision"}:
            return self.forget(operation)
        fields = {"action", "owner", "revision", "status", "models", "paths", "rpm", "expires_days"}
        if action == "approve" and admin and set(operation) == fields:
            return self.approve(actor, operation)
        raise Denied("operation is not allowed")

    def approve(self, actor, operation):
        owner, revision, status = operation["owner"], operation["revision"], operation["status"]
        if not isinstance(owner, str) or not _HUMAN.fullmatch(owner) or type(revision) is not int or status not in {"approved", "denied"}:
            raise Denied("invalid access decision")
        models, paths, rpm, days = [], [], 0, 0
        if status == "approved":
            models, paths = self.store._grants(operation["models"], operation["paths"])
            rpm, days = operation["rpm"], operation["expires_days"]
            if len(json.dumps([models, paths])) > 8192 or not set(models).issubset(self.models) or type(rpm) is not int or not 1 <= rpm <= 100000 or type(days) is not int or not 1 <= days <= 90:
                raise Denied("invalid access limits")
        with self.store._connect() as db:
            account = _account(db, owner)
        if account is None or not self.store.owner_check(owner, account["generation"], account["epoch"]):
            raise Denied("account changed; request access again")
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("UPDATE connect_accounts SET revision=?,status=?,models=?,paths=?,rpm=?,expires_days=?,updated_at=?,actor=? WHERE owner=? AND revision=?", (_next_revision(db), status, json.dumps(models), json.dumps(paths), rpm, days, int(time.time()), actor[0], owner, revision))
            if result.rowcount != 1:
                raise Denied("access changed; refresh before saving")
            db.execute("UPDATE keys SET revoked_at=COALESCE(revoked_at,?) WHERE key_id IN (SELECT key_id FROM connect_key_owners WHERE owner=?)", (int(time.time()), owner))
            db.execute("DELETE FROM buckets WHERE key_id=?", ("connect:"+owner,))
            db.execute("COMMIT")
        return {"ok": True}

    def forget(self, operation):
        owner, revision = operation["owner"], operation["revision"]
        if not isinstance(owner, str) or not _HUMAN.fullmatch(owner) or type(revision) is not int:
            raise Denied("invalid account record")
        with self.store._connect() as db:
            account = _account(db, owner)
        if account is None or (account["status"] != "denied" and self.store.owner_check(owner, account["generation"], account["epoch"])):
            raise Denied("remove access before removing this record")
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("DELETE FROM connect_accounts WHERE owner=? AND revision=?", (owner, revision)).rowcount != 1:
                raise Denied("account changed; refresh before removing")
            db.execute("UPDATE keys SET revoked_at=COALESCE(revoked_at,?) WHERE key_id IN (SELECT key_id FROM connect_key_owners WHERE owner=?)", (int(time.time()), owner))
            db.execute("DELETE FROM buckets WHERE key_id=?", ("connect:"+owner,))
            db.execute("COMMIT")
        return {"ok": True}

    def view(self, actor, admin):
        with self.store._connect() as db:
            account = _account(db, actor[0])
            if account and (account["generation"], account["epoch"]) != actor[1:]:
                account = None
            ids = {row[0] for row in db.execute("SELECT key_id FROM connect_key_owners WHERE owner=? AND epoch=?", (actor[0],actor[2]))}
            # These are observed HTTP results in the bounded metadata log, not billing or stream completion.
            rows = db.execute("SELECT a.key_id,COUNT(*),SUM(status>=400),SUM(status=429),MAX(recorded_at),AVG(elapsed_ms) FROM audit a JOIN connect_key_owners o USING(key_id) WHERE o.owner=? AND o.epoch=? GROUP BY a.key_id", (actor[0],actor[2])).fetchall()
            accounts = [_account(db, row[0]) for row in db.execute("SELECT owner FROM connect_accounts ORDER BY updated_at,owner")] if admin else None
        usage = [dict(zip(("key_id", "requests", "errors", "rate_limited", "last_used", "average_ms"), row)) for row in rows]
        totals = {field: sum(row[field] for row in usage) for field in ("requests", "errors", "rate_limited")}
        owned = [key for key in self.store.list_keys() if key["key_id"] in ids]
        now = int(time.time())
        # ponytail: show all active keys then recent retired keys, capped at 100;
        # add cursor pagination if operators need older individual metadata.
        owned.sort(key=lambda key: (key["revoked_at"] is None and (key["expires_at"] is None or key["expires_at"] > now), key["created_at"], key["key_id"]), reverse=True)
        shown = owned[:100]
        shown_ids = {key["key_id"] for key in shown}
        return {"account": account, "keys": shown, "keys_truncated": len(owned) > len(shown),
                "usage": [row for row in usage if row["key_id"] in shown_ids], "usage_totals": totals,
                "usage_window": "Retained metadata log (up to 10,000 requests across the router); HTTP status does not prove stream completion.",
                "accounts": accounts, "models": self.models if admin else [], "paths": sorted(_POST_PATHS) if admin else []}
