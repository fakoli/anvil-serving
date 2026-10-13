import os
from pathlib import Path
import pytest
from anvil_serving.native_agents.store import Store, protected_directory
from anvil_serving.native_agents.owner import canonical_origin
from anvil_serving.native_agents.types import AgentsError


def test_unsafe_parent_and_symlink_ancestors_rejected(tmp_path):
    tmp_path.chmod(0o777)
    with pytest.raises(ValueError): protected_directory(tmp_path)
    tmp_path.chmod(0o700)
    link=tmp_path/"link"; link.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(ValueError): protected_directory(link)

@pytest.mark.parametrize("kind",["symlink","hardlink","world-readable","journal-symlink","wal-hardlink"])
def test_unsafe_database_or_sidecar_is_never_opened(tmp_path,monkeypatch,kind):
    monkeypatch.setattr("anvil_serving.native_agents.store.protected_directory",lambda p:None)
    path=tmp_path/"store.db"; other=tmp_path/"other"; other.write_bytes(b"untouched"); other.chmod(0o600)
    if kind=="symlink": path.symlink_to(other)
    elif kind=="hardlink": os.link(other,path)
    elif kind=="world-readable": path.write_bytes(b""); path.chmod(0o644)
    else:
        path.write_bytes(b""); path.chmod(0o600)
        if kind=="journal-symlink": path.with_name("store.db-journal").symlink_to(other)
        else: os.link(other,path.with_name("store.db-wal"))
    with pytest.raises(ValueError): Store(path)
    assert other.read_bytes()==b"untouched"

@pytest.mark.parametrize("url",["http://:secret@127.0.0.1:1234","http://user@127.0.0.1:1234","http://127.0.0.1:01234","http://127.0.0.1:1234/path","http://127.0.0.1:1234/?other=1","http://localhost:1234"])
def test_owner_origin_rejects_credentials_and_aliases(url):
    with pytest.raises(ValueError): canonical_origin(url)


def test_owner_trailing_slash_normalizes_before_uniqueness():
    assert canonical_origin("http://127.0.0.1:1234/")==canonical_origin("http://127.0.0.1:1234")


def test_receipt_quota_cannot_exhaust_another_principal(tmp_path,monkeypatch):
    monkeypatch.setattr("anvil_serving.native_agents.store.protected_directory",lambda p:None)
    store=Store(tmp_path/"store.db",maximum_receipts_per_principal=2)
    for rid in ["one","two"]: store.reserve("a",rid,"digest","w","s","prompt")
    with pytest.raises(AgentsError): store.reserve("a","three","digest","w","s","prompt")
    assert store.reserve("a","one","digest","w","s","prompt")[1] is False
    assert store.reserve("b","one","digest","w","s","prompt")[1] is True


@pytest.mark.parametrize("same_path",[True,False])
def test_distinct_owner_credentials_required(tmp_path,monkeypatch,same_path):
    import json
    from anvil_serving.native_agents.server import load_config
    monkeypatch.setattr("anvil_serving.native_agents.server.protected_read",lambda p,n:Path(p).read_bytes())
    token=tmp_path/"token"; token.write_text("unique-owner-token-which-must-not-be-shared")
    other=token if same_path else tmp_path/"other"
    other.write_text(token.read_text())
    root={"version":1,"deployment_id":"test","listen_port":9900,"store_file":str(tmp_path/"db"),"auth":{},"workspaces":[]}
    for i,p in enumerate([token,other]):
        root["workspaces"].append({"id":str(i),"principal":"human:"+"a"*64,"label":"w","isolation":"personal","owner_url":f"http://127.0.0.1:{9901+i}","owner_token_file":str(p),"directories":[{"id":"source","label":"Source","path":str(tmp_path.resolve()),"display_path":"/workspace/source"}]})
    config=tmp_path/"config"; config.write_text(json.dumps(root))
    with pytest.raises(ValueError,match="credential"):load_config(config)


def test_concurrent_session_pending_limit_always_fits_stop(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr("anvil_serving.native_agents.store.protected_directory",lambda p:None)
    store=Store(tmp_path/"store.db")
    def reserve(i):
        try: store.reserve("a",str(i),"digest","w","s","prompt"); return True
        except AgentsError as e: assert e.status==429; return False
    with ThreadPoolExecutor(max_workers=16) as executor: outcomes=list(executor.map(reserve,range(160)))
    assert sum(outcomes)==128
    pending=store.pending("a","w","s"); assert len(pending)==128
    store.cancel("a","w","s",pending)
    assert store.pending("a","w","s")==[]
    assert store.reserve("a","fresh","digest","w","s","prompt")[1]
    assert store.reserve("b","other","digest","w2","s2","prompt")[1]
