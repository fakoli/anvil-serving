"""Purpose metadata and declared child fixtures; no live inference."""
from dataclasses import replace
import json

import pytest

from anvil_serving.router.identity import EndUser, forwarded_caller
from anvil_serving.router.purpose import PurposeRouter, child_request_start
from anvil_serving.router.usage_store import Observation, RequestStart, RouteAssociation, Terminal
from tests.router.test_embeddings import EMBED_PM, RERANK_PM
from tests.router.test_usage_lifecycle import AT, END, rows, start_at, store as store
from tests.router.key_fixtures import tmp_path as tmp_path


@pytest.mark.parametrize("relation,expected", [("exclusive", 17), ("inclusive_parent", 17), ("unobserved", 10)])
def test_actual_declared_child_fixture_preserves_caller_grant_and_counts_consumption_once(store, relation, expected):
    usage, _, run, scope = store
    key = usage.key_store
    metadata, secret = key.create("synthetic purpose", [EMBED_PM.model, RERANK_PM.model], ["/v1/embeddings", "/v1/rerank"])
    key.bind_owner(metadata["key_id"], "service", "purpose_fixture", expected_revision=0)
    admitted = key.admit(key.authenticate(secret, snapshot=True), "/v1/embeddings", EMBED_PM.model).caller_snapshot
    caller = forwarded_caller(admitted, EndUser("webui_fixture", "open-webui", "subject_fixture"))
    parent = replace(start_at(run), caller=caller, kind="embedding", model=EMBED_PM.model,
                     usage_relation=relation, output_applicability="not_applicable")
    usage.start(parent, authority_scope=scope)
    children = [child_request_start(parent, accepted_at=AT, kind=pm.kind, model=pm.model,
                                   usage_relation="inclusive_parent" if relation == "inclusive_parent" else "exclusive",
                                   applicability=("applicable", "not_applicable")) for pm in (EMBED_PM, RERANK_PM)]
    for child in children:
        assert child.caller is caller and child.run_id == run and child.parent_request_id == parent.request_id
        usage.start(child, authority_scope=scope)
    assert len({v.request_id for v in (parent, *children)}) == len({v.attempt_id for v in (parent, *children)}) == 3
    key.bind_owner(metadata["key_id"], "service", "later_fixture", expected_revision=1)
    key.revoke(metadata["key_id"])
    observed_counts = iter((7 if relation == "exclusive" else 17, 4, 6))
    calls = []
    def transport(*args, **kwargs):
        calls.append(True)
        return json.dumps({"usage": {"total_tokens": next(observed_counts)}}).encode()
    router = PurposeRouter([EMBED_PM, RERANK_PM], transport=transport)
    for start in (parent, *children):
        route = RouteAssociation(backend_id="purpose_fixture")
        usage.note_dispatch(start.request_id, route)
        router.dispatch(start.kind, {"model": start.model, "input": "fixture", "query": "fixture", "documents": ["fixture"]})
        observed = router.get_last_normalized_usage()
        usage.note_observation(start.request_id, Observation(1, END, observed))
        final = Terminal(start.request_id, END, True, "success", "success", "success", route, observed,
                         coverage=("usage_relation_unknown",) if start.usage_relation == "unobserved" else ())
        assert usage.finalize(final) == "committed" and usage.finalize(final) == "same"
    assert len(calls) == 3
    for table in ("usage_daily", "usage_cumulative"):
        groups = rows(usage, table)
        assert sum(r["requests"] for r in groups) == 1 and sum(r["attempts"] for r in groups) == 3
        assert sum(r["measured_input"] for r in groups) == expected and sum(r["measured_output"] for r in groups) == 0
        assert all(r["actor_id"] == "purpose_fixture" and r["binding_revision"] == 1 and
                   r["end_user_subject"] == "subject_fixture" and r["grant_reference"] == metadata["key_id"] and
                   r["grant_policy_digest"] == caller.grant.policy_digest for r in groups)
    saved = [RequestStart.from_json(r["start_payload"]) for r in rows(usage, "usage_starts")]
    assert all(v.caller == caller for v in saved)
