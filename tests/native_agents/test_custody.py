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


@pytest.mark.parametrize("url",["http://:secret@127.0.0.1:1234","http://user@127.0.0.1:1234","http://127.0.0.1:01234","http://127.0.0.1:1234/path","http://127.0.0.1:1234/?other=1","http://localhost:1234"])
def test_owner_origin_rejects_credentials_and_aliases(url):
    with pytest.raises(ValueError): canonical_origin(url)


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
def test_only_declared_personal_principal_can_use_host_workspaces(tmp_path, monkeypatch):
    import json
    from anvil_serving.native_agents.server import load_config
    monkeypatch.setattr("anvil_serving.native_agents.server.protected_read", lambda p, n: Path(p).read_bytes())
    operator = "human:" + "a" * 64
    guest = "human:" + "b" * 64
    config = {"version": 1, "deployment_id": "test", "listen_port": 9900,
              "store_file": str(tmp_path / "db"), "auth": {}, "workspaces": []}
    for index, principal in enumerate((operator, guest)):
        token = tmp_path / f"token-{index}"
        token.write_text(f"distinct-owner-token-{index}")
        config["workspaces"].append({"id": f"workspace-{index}", "principal": principal,
            "label": "Agent", "isolation": "personal" if index == 0 else "sandboxed",
            "owner_url": f"http://127.0.0.1:{9901+index}", "owner_token_file": str(token),
            "directories": [{"id": "project", "label": "Project", "path": str(tmp_path.resolve()),
                             "display_path": "/workspace/project"}]})
    path = tmp_path / "config.json"
    def check():
        path.write_text(json.dumps(config))
        return load_config(path)
    with pytest.raises(ValueError, match="explicit personal principal"):
        check()
    config["personal_principal"] = operator
    assert len(check()[1]) == 2
    config["workspaces"][1]["isolation"] = "personal"
    with pytest.raises(ValueError, match="reserved for the personal principal"):
        check()
    for invalid in (None, "", "operator", 1):
        config["personal_principal"] = invalid
        with pytest.raises(ValueError, match="invalid personal principal"):
            check()
    del config["personal_principal"]
    config["workspaces"].pop()
    assert len(check()[1]) == 1  # Existing single-owner installations stay valid.
