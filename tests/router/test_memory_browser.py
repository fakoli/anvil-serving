"""Native browse and curation retain explicit, current bank authority."""
import json
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from anvil_serving import memory_access
from anvil_serving.router.keys import Principal
from anvil_serving.router.memory import MemoryError, MemoryRouter
from tests.router.key_fixtures import tmp_path as tmp_path
from tests.router.test_memory import ROUTE

USER = "human:" + "a" * 64
OTHER = "human:" + "b" * 64
ID = "11111111-1111-4111-8111-111111111111"
SOURCE = "22222222-2222-4222-8222-222222222222"
RECORD = {"id": ID, "text": "Synthetic fact", "type": "world", "state": "valid", "entities": ["Example"]}


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "grants.json"
    policy = {"schema": memory_access.SCHEMA, "users": {
        USER: {"default_bank": "personal", "admin": True, "banks": {"personal": ["list", "detail", "invalidate", "restore"], "old": ["recall"]}},
        OTHER: {"default_bank": "other", "admin": False, "banks": {"other": ["list", "detail"]}},
    }}
    def publish():
        path.write_text(json.dumps(policy)); path.chmod(0o600)
    publish()
    calls, replies = [], []
    def transport(url, **kwargs):
        calls.append({"url": url, **kwargs})
        response = replies.pop(0) if replies else RECORD
        if callable(response): response = response()
        return json.dumps(response).encode()
    router = MemoryRouter([replace(ROUTE, principal="connect", access_file=str(path))],
        env={"HINDSIGHT_TOKEN": "synthetic"}, transport=transport)
    def send(op, args, bank="personal", principal=USER):
        body = {"alias": ROUTE.alias, "operation": op, "arguments": args}
        if bank is not None: body["bank"] = bank
        return router.dispatch(body, principal=Principal("test", (ROUTE.alias,), ("/v1/memory",), owner=(principal, "1", "0" * 64)))["result"]
    return send, calls, replies, policy, publish


def test_native_inventory_filters_even_administrator(setup):
    send, calls, _, _, _ = setup
    value = send("banks", {"native": True, "expected_principal": USER}, bank=None)
    assert value["schema"] == "anvil-native-memory/v1"
    assert [b["bank_id"] for b in value["banks"]] == ["personal"]
    assert value["principal"] == USER and not calls
    with pytest.raises(MemoryError) as failure:
        send("banks", {"native": True, "expected_principal": OTHER}, bank=None)
    assert failure.value.status == 403


@pytest.mark.parametrize("operation,args", [("list", {}), ("detail", {"id": ID}), ("invalidate", {"id": ID, "reason": "Fixture"}), ("restore", {"id": ID})])
def test_new_operations_never_inherit_admin_or_other_bank_access(setup, operation, args):
    send, calls, _, _, _ = setup
    for bank, user in [("old", USER), ("other", USER), ("personal", OTHER)]:
        with pytest.raises(MemoryError) as failure: send(operation, args, bank, user)
        assert failure.value.status == 403
    assert not calls


def test_list_query_bounded_strict_and_projection_excludes_untrusted_fields(setup):
    send, calls, replies, _, _ = setup
    replies.append({"items": [{**RECORD, "fact_type": "world", "source_memories": [{"text": "cross-bank secret"}], "metadata": {"private": True}, "attachments": ["https://example.test/file"]}], "total": 1, "limit": 10, "offset": 20})
    result = send("list", {"limit": 10, "offset": 20, "q": "two words", "type": "world", "tags": ["fixture"], "tags_match": "all_strict"})
    assert "q=two+words" in calls[0]["url"] and "state=valid" in calls[0]["url"]
    assert "tags_match=all_strict" in calls[0]["url"] and calls[0]["method"] == "GET"
    assert not {"metadata", "source_memories", "attachments"} & result["items"][0].keys()


@pytest.mark.parametrize("args", [{"limit": True}, {"limit": 101}, {"offset": -1}, {"q": "x" * 513}, {"tags_match": "any"}, {"url": "http://example.test"}, {"state": []}])
def test_invalid_filters_fail_without_upstream(setup, args):
    send, calls, _, _, _ = setup
    with pytest.raises(MemoryError) as failure: send("list", args)
    assert failure.value.status == 422 and not calls


def test_cross_bank_source_text_and_inherited_entities_never_escape(setup):
    send, calls, replies, _, _ = setup
    replies.append({**RECORD, "type": "observation", "source_memory_ids": [SOURCE], "source_memories": [{"id": SOURCE, "text": "ungranted"}], "entities": ["ungranted inherited entity"], "history": [{"text": "ungranted"}]})
    result = send("detail", {"id": ID})
    assert result["source_ids"] == [SOURCE] and result["entities"] == []
    assert "ungranted" not in json.dumps(result) and len(calls) == 1


def test_curation_is_state_only_with_fresh_grants_and_raw_fact_preflight(setup):
    send, calls, replies, policy, publish = setup
    replies.extend([RECORD, {**RECORD, "state": "invalidated"}])
    assert send("invalidate", {"id": ID, "reason": "Synthetic correction"})["state"] == "invalidated"
    assert calls[0]["data"] is None and calls[1]["method"] == "PATCH"
    assert json.loads(calls[1]["data"]) == {"state": "invalidated", "reason": "Synthetic correction"}
    def revoked():
        policy["users"][USER]["banks"]["personal"].remove("restore"); publish()
        return {**RECORD, "state": "invalidated"}
    replies.append(revoked)
    with pytest.raises(MemoryError) as failure: send("restore", {"id": ID})
    assert failure.value.status == 403 and len(calls) == 3


def test_observation_curation_is_rejected_without_patch(setup):
    send, calls, replies, _, _ = setup
    replies.append({**RECORD, "type": "observation"})
    with pytest.raises(MemoryError) as failure: send("invalidate", {"id": ID, "reason": "Synthetic"})
    assert failure.value.status == 422 and len(calls) == 1


def test_stale_grants_revoke_reads(setup):
    send, calls, _, policy, publish = setup
    send("detail", {"id": ID})
    del policy["users"][USER]; publish()
    with pytest.raises(MemoryError) as failure: send("detail", {"id": ID})
    assert failure.value.status == 403 and len(calls) == 1


def test_native_missing_record_preserves_hindsight_404(tmp_path):
    path = tmp_path / "missing-grants.json"
    path.write_text(json.dumps({"schema": memory_access.SCHEMA, "users": {
        USER: {"default_bank": "personal", "admin": False,
               "banks": {"personal": ["list", "detail"]}},
    }}))
    path.chmod(0o600)
    requests = []

    class Missing(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(self.path)
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Missing)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    route = replace(ROUTE, principal="connect", access_file=str(path),
                    base_url=f"http://127.0.0.1:{upstream.server_port}")
    memory = MemoryRouter([route], env={"HINDSIGHT_TOKEN": "synthetic"})
    principal = Principal("test", (ROUTE.alias,), ("/v1/memory",), owner=(USER, "1", "0" * 64))
    try:
        with pytest.raises(MemoryError) as failure:
            memory.dispatch({"alias": ROUTE.alias, "operation": "detail",
                             "arguments": {"id": ID}, "bank": "personal"}, principal=principal)
        assert failure.value.status == 404 and failure.value.code == "memory_not_found"
        assert requests == [f"/v1/default/banks/personal/memories/{ID}"]
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(5)


@pytest.mark.parametrize("args", [{"id": "../secret"}, {"id": ID, "text": "overwrite"}, {"id": ID, "reason": ""}])
def test_curation_rejects_path_and_text_override(setup, args):
    send, calls, _, _, _ = setup
    with pytest.raises(MemoryError) as failure: send("invalidate", args)
    assert failure.value.status == 422 and not calls


@pytest.mark.parametrize("value", ["https://example.test/document?token=secret", "/home/operator/private", "file:///private/report", "../private", "C:\\Users\\operator\\private"])
def test_provenance_references_omit_urls_and_host_paths(setup, value):
    send, _, replies, _, _ = setup
    replies.append({**RECORD, "document_id": value, "chunk_id": value})
    result = send("detail", {"id": ID})
    assert result["document_id"] is None and result["chunk_id"] is None
    assert value not in json.dumps(result)
