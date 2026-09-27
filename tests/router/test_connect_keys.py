"""Security and HTTP contracts for Connect-owned router credentials."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import hmac
import http.client
import json
import secrets
import threading
import time

import pytest

from anvil_serving.router import connect_keys
from anvil_serving.router.config import ServerConfig
from anvil_serving.router.front_door import make_server
from anvil_serving.router.keys import KeyStore, KeyStoreError
from tests.router.helpers import StaticBackend
from tests.router.key_fixtures import tmp_path as tmp_path

CHAT = "/v1/chat/completions"
OWNER, OTHER, ADMIN = ("human:" + c*64 for c in "abc")
FENCE = "f"*64


@pytest.fixture
def portal(tmp_path):
    store = KeyStore.initialize(tmp_path / "private" / "keys.sqlite3")
    allowed = {(owner, "1", FENCE) for owner in (OWNER, OTHER, ADMIN)}
    store.owner_check = lambda *actor: actor in allowed
    return connect_keys.ConnectKeys(store, ["llm.primary", "llm.heavy"]), allowed


def call(portal, action, owner=OWNER, admin=False, **fields):
    return portal.dispatch({"principal":owner, "generation":"1", "epoch":FENCE, "administrator":admin,
                            "operation":{"action":action, **fields}})


def approve(portal, owner=OWNER, rpm=60):
    call(portal, "request", owner)
    account = call(portal, "view", owner)["account"]
    return portal.dispatch({"principal":ADMIN, "generation":"1", "epoch":FENCE, "administrator":True,
        "operation":{"action":"approve", "owner":owner, "revision":account["revision"], "status":"approved",
                     "models":["llm.primary"], "paths":[CHAT], "rpm":rpm, "expires_days":30}})


def issue(portal, owner=OWNER, **overrides):
    account = call(portal, "view", owner)["account"]
    return call(portal, "create", owner, **{"name":"Laptop", "models":["llm.primary"], "paths":[CHAT], "rpm":account["rpm"], "expires_days":30, "revision":account["revision"], **overrides})


def test_approval_ownership_grants_usage_and_revocation(portal):
    p, allowed = portal
    call(p, "request")
    with pytest.raises(KeyStoreError): issue(p)
    approve(p); approve(p, OTHER)
    result = issue(p); other = issue(p, OTHER)
    assert p.store.authenticate(result["secret"]).owner == (OWNER,"1",FENCE)
    for changes in ({"models":["llm.heavy"]}, {"paths":["/v1/embeddings"]}, {"rpm":61}, {"expires_days":31}, {"revision":True}):
        with pytest.raises(KeyStoreError): issue(p, **changes)
    with pytest.raises(connect_keys.Denied): call(p,"revoke",key_id=other["key"]["key_id"])
    p.store.record(result["key"]["key_id"], "request-test", "POST", CHAT, 429, 7)
    view = call(p,"view")
    assert [key["key_id"] for key in view["keys"]] == [result["key"]["key_id"]]
    assert view["usage"][0]["rate_limited"] == 1 and view["accounts"] is None
    assert result["secret"] not in json.dumps(view) and other["key"]["key_id"] not in json.dumps(view)
    call(p,"revoke",key_id=result["key"]["key_id"])
    assert p.store.authenticate(result["secret"]) is None
    assert p.store.authenticate(other["secret"]) is not None
    allowed.remove((OTHER,"1",FENCE))
    assert p.store.authenticate(other["secret"]) is None


def test_shared_atomic_rate_and_active_key_cap(portal):
    p, _ = portal
    approve(p,rpm=3)
    keys = [issue(p)["key"]["key_id"] for _ in range(10)]
    with pytest.raises(KeyStoreError): issue(p)
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(p.store.admit,keys))
    assert results.count(0) == 3
    assert all(value >= 1 for value in results if value)


def test_migration_rollback_and_retained_attribution(tmp_path, monkeypatch):
    from anvil_serving.router import keys
    store = KeyStore.initialize(tmp_path / "private" / "keys.sqlite3")
    old, token = store.create("old",["llm.primary"],[CHAT])
    store.owner_check = lambda *args: True
    p = connect_keys.ConnectKeys(store,["llm.primary"])
    assert store.authenticate(token).key_id == old["key_id"]
    with store._connect() as db: assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    approve(p)
    result = issue(p)
    assert KeyStore(store.path).authenticate(result["secret"]) is None  # feature disabled: no bypass
    store.record(result["key"]["key_id"],None,"POST",CHAT,200,10)
    store.revoke(result["key"]["key_id"])
    monkeypatch.setattr(keys,"_MAX_KEYS",2)
    issue(p)
    assert call(p,"view")["usage_totals"]["requests"] == 1


def test_policy_revision_and_account_fence(portal):
    p, allowed = portal
    approve(p); result = issue(p)
    approve(p)  # approval change invalidates previous keys
    assert p.store.authenticate(result["secret"]) is None
    result = issue(p)
    allowed.remove((OWNER,"1",FENCE))
    allowed.add((OWNER,"1","e"*64))  # delete/recreate or recovered authority
    assert p.store.authenticate(result["secret"]) is None
    with pytest.raises(connect_keys.Denied): call(p,"view")
    fresh = p.dispatch({"principal":OWNER,"generation":"1","epoch":"e"*64,"administrator":False,"operation":{"action":"view"}})
    assert fresh["account"] is None


def assertion(secret, *, owner=OWNER, role="member", target=connect_keys.BROKER_PATH):
    now = int(time.time())
    claims = {"v":1,"iss":"anvil-connect","kid":"router-keys","sub":owner,"sid":"1"*32,"sg":1,"pg":1,"epoch":FENCE,
              "resource":"router-keys","role":role,"host":"home.example.test","method":"POST","target_sha256":hashlib.sha256(target.encode()).hexdigest(),
              "iat":now,"exp":now+30,"session_exp":now+3600,"jti":secrets.token_hex(16)}
    raw = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    digest = hmac.new(base64.urlsafe_b64decode(secret+"="), ("acai1."+raw).encode(),hashlib.sha256).digest()
    return "acai1."+raw+"."+base64.urlsafe_b64encode(digest).decode().rstrip("=")


@contextmanager
def running(portal,monkeypatch):
    p, _ = portal
    secret, check_secret, master = (secrets.token_urlsafe(32) for _ in range(3))
    monkeypatch.setenv("CONNECT_SIGNING_TEST",secret);monkeypatch.setenv("CONNECT_CHECK_TEST",check_secret)
    monkeypatch.setattr(connect_keys,"principal_checker",lambda *args: p.store.owner_check)
    config = ServerConfig(api_keys_path=str(p.store.path),connect_keys_env="CONNECT_SIGNING_TEST",connect_check_env="CONNECT_CHECK_TEST",connect_home_url="https://home.example.test")
    server = make_server("127.0.0.1",0,StaticBackend(["ok"]),auth_token=master,model_routes=["llm.primary"],server_config=config)
    # Join handlers, including post-response audit writes, before deleting SQLite.
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try: yield server.server_address,secret,check_secret,master
    finally: server.shutdown();server.server_close();thread.join(5)


def send_http(address,path,body,headers):
    connection = http.client.HTTPConnection(*address,timeout=5)
    try:
        connection.request("POST",path,body,{"Content-Type":"application/json",**headers})
        response = connection.getresponse()
        return response.status,dict(response.getheaders()),response.read()
    finally:connection.close()


def test_broker_signatures_replay_closed_body_and_live_request(portal,monkeypatch):
    p, _ = portal
    approve(p)
    with running(portal,monkeypatch) as (address,secret,check_secret,master):
        def broker(body, signed=None):
            return send_http(address,connect_keys.BROKER_PATH,body,{"X-Anvil-Connect-Identity":signed or assertion(secret)})
        signed=assertion(secret)
        status,headers,raw=broker('{"action":"view"}',signed)
        assert status==200 and headers["Cache-Control"]=="no-store"
        assert broker('{"action":"view"}',signed)[0]==401
        assert broker('{"action":"view"}',assertion(check_secret))[0]==401
        assert broker('{"action":"view"}',assertion(secret,target="/v1/models"))[0]==401
        assert broker('{"action":"view","action":"request"}')[0]==400
        assert broker('{"action":"view","administrator":true}')[0]==403
        assert send_http(address,connect_keys.BROKER_PATH,'{}',{"Authorization":"Bearer "+master})[0]==401
        result=issue(p)
        status,_,_=send_http(address,CHAT,json.dumps({"model":"llm.primary","messages":[{"role":"user","content":"hi"}]}),{"Authorization":"Bearer "+result["secret"]})
        assert status==200


def test_slow_owner_checks_do_not_occupy_ordinary_key_slots(portal,monkeypatch):
    p,_=portal
    approve(p,rpm=60)
    owned=[issue(p)["secret"] for _ in range(4)]
    _,ordinary=p.store.create("ordinary",["llm.primary"],[CHAT])
    entered=threading.Barrier(5);release=threading.Event()
    def checker(*actor):
        entered.wait(timeout=5); release.wait(timeout=5); return True
    p.store.owner_check=checker
    with running(portal,monkeypatch) as (address,*_):
        payload=json.dumps({"model":"llm.primary","messages":[{"role":"user","content":"hi"}]})
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures=[pool.submit(send_http,address,CHAT,payload,{"Authorization":"Bearer "+token}) for token in owned]
            try:
                entered.wait(timeout=5)
                assert send_http(address,CHAT,payload,{"Authorization":"Bearer "+ordinary})[0]==200
            finally:release.set()
            assert all(f.result()[0]==200 for f in futures)


def test_inactive_account_removal_preserves_revision_fence(portal):
    p, allowed = portal
    approve(p)
    result = issue(p)
    old = call(p,"view")["account"]
    def forget(revision):
        return p.dispatch({"principal":ADMIN,"generation":"1","epoch":FENCE,"administrator":True,
                           "operation":{"action":"forget","owner":OWNER,"revision":revision}})
    with pytest.raises(connect_keys.Denied): forget(old["revision"])
    allowed.remove((OWNER,"1",FENCE))
    forget(old["revision"])
    assert p.store.authenticate(result["secret"]) is None
    allowed.add((OWNER,"1",FENCE))
    call(p,"request")
    new = call(p,"view")["account"]
    assert new["revision"] > old["revision"]
    with pytest.raises(connect_keys.Denied): forget(old["revision"])


@pytest.mark.parametrize("value",["http://127.0.0.1:9000","https://localhost","https://home.example.test:443","https://HOME.example.test","https://127.0.0.1","https://home.example.test/path"])
def test_home_url_matches_signed_host_contract(value):
    with pytest.raises(ValueError): connect_keys.home_url(value)


def test_bounded_retired_key_list_and_full_usage_totals(portal):
    p,_=portal
    approve(p)
    for i in range(105):
        result=issue(p)
        p.store.record(result["key"]["key_id"],None,"POST",CHAT,200,1)
        call(p,"revoke",key_id=result["key"]["key_id"])
    live=issue(p)
    view=call(p,"view")
    assert view["keys_truncated"] and len(view["keys"])==100
    assert view["keys"][0]["key_id"]==live["key"]["key_id"]
    assert view["usage_totals"]["requests"]==105
