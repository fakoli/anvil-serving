"""Purpose metadata and declared child fixtures; no live inference."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import threading

import pytest

from anvil_serving.router.backends.relay import RelayBackendError
from anvil_serving.router.decision_log import TokenDirection, TokenUsage
from anvil_serving.router.identity import EndUser, forwarded_caller
from anvil_serving.router.purpose import PurposeError, PurposeRouter, child_request_start, purpose_usage
from anvil_serving.router.usage_store import Observation, RequestStart, RouteAssociation, Terminal, UsageError
from tests.router.test_embeddings import EMBED_PM, RERANK_PM, FakeTransport
from tests.router.test_usage_lifecycle import AT, END, rows, start_at, store
from tests.router.key_fixtures import tmp_path as tmp_path


@pytest.mark.parametrize("kind,raw,count", [
    ("embedding", {"prompt_tokens": 7, "total_tokens": 7}, 7),
    ("rerank", {"total_tokens": 11}, 11),
    ("embedding", {"prompt_tokens": 0}, 0),
    ("rerank", {"total_tokens": 0}, 0),
    ("embedding", None, None), ("rerank", {}, None),
    ("embedding", {"prompt_tokens": True}, None),
    ("rerank", {"total_tokens": -1}, None),
    ("embedding", {"prompt_tokens": "4"}, None),
    ("rerank", {"total_tokens": 4.0}, None),
    ("embedding", {"prompt_tokens": 10**15 + 1}, None),
    ("rerank", {"prompt_tokens": None, "total_tokens": 9}, None),
    ("embedding", {"prompt_tokens": -1, "total_tokens": 9}, None),
    ("embedding", "invalid usage", None),
])
def test_declared_token_purpose_distinguishes_zero_unknown_and_na(kind, raw, count):
    observed = purpose_usage(kind, raw)
    assert observed.input == TokenDirection(count, "measured" if count is not None else "unknown")
    assert observed.output == TokenDirection(applicability="not_applicable")


def test_optional_components_and_closed_metadata_do_not_include_content():
    observed = purpose_usage("embedding", {"total_tokens": 10, "prompt_tokens_details": {"cached_tokens": 4},
                                           "completion_tokens": 999, "reasoning_tokens": 888, "secret": "discard"})
    assert observed.input.count == 10 and observed.cache_read_input_tokens == 4
    assert observed.reasoning_output_tokens is None and observed.cache_creation_input_tokens is None
    assert "discard" not in json.dumps(observed.to_dict())
    assert TokenUsage.from_dict(observed.to_dict()) == observed
    assert purpose_usage("embedding", {"prompt_tokens": 10}).cache_read_input_tokens is None


@pytest.mark.parametrize("kind", ["stt", "tts", "audio", "memory"])
def test_declared_non_token_operation_does_not_guess_tokens_from_other_units(kind):
    observed = purpose_usage(kind, {"duration_ms": 500, "bytes": 1000, "max_tokens": 4096, "total_tokens": 3})
    assert observed.input == observed.output == TokenDirection(applicability="not_applicable")
    assert observed.cache_read_input_tokens is None


@pytest.mark.parametrize("kind", ["unknown", None, []])
def test_unknown_family_is_not_inferred(kind):
    with pytest.raises(ValueError, match="unsupported purpose units"):
        purpose_usage(kind)


def test_purpose_preserves_wire_response_and_normalized_observation_is_same_thread_only():
    payload = {"data": [{"embedding": [0.1, 0.2]}], "usage": {"prompt_tokens": 0}, "extra": "wire-only"}
    transport = FakeTransport(payload)
    router = PurposeRouter([EMBED_PM], transport=transport)
    assert router.get_last_normalized_usage() is None
    response = router.dispatch("embedding", {"model": EMBED_PM.model, "input": "fixture"})
    assert response == payload and router.get_last_normalized_usage().input == TokenDirection(0, "measured")
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(router.get_last_normalized_usage).result() is None
    assert transport.calls[0]["body"] == {"model": EMBED_PM.model, "input": "fixture"}


@pytest.mark.parametrize("failure", ["unknown_model", "transport", "malformed"])
def test_error_after_success_resets_observation_before_validation_or_dispatch(failure):
    calls = []
    def transport(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            return b'{"usage":{"prompt_tokens":7}}'
        if failure == "transport":
            raise RelayBackendError("synthetic failure")
        return b'invalid JSON'
    router = PurposeRouter([EMBED_PM], transport=transport)
    router.dispatch("embedding", {"model": EMBED_PM.model, "input": "fixture"})
    assert router.get_last_normalized_usage().input.count == 7
    model = "unknown_fixture" if failure == "unknown_model" else EMBED_PM.model
    with pytest.raises(PurposeError):
        router.dispatch("embedding", {"model": model, "input": "fixture"})
    assert len(calls) == (1 if failure == "unknown_model" else 2)
    assert router.get_last_normalized_usage() == purpose_usage("embedding")


def test_concurrent_dispatches_copy_distinct_normalized_observations_on_the_owning_workers():
    ready = threading.Barrier(2)
    def transport(*args, data, **kwargs):
        count = {"first_fixture": 4, "second_fixture": 9}[json.loads(data)["input"]]
        ready.wait(timeout=5)
        return json.dumps({"usage": {"prompt_tokens": count}}).encode()
    router = PurposeRouter([EMBED_PM], transport=transport)
    copied = threading.Barrier(2)
    def worker(value):
        router.dispatch("embedding", {"model": EMBED_PM.model, "input": value})
        copied.wait(timeout=5)
        return router.get_last_normalized_usage()
    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = list(pool.map(worker, ("first_fixture", "second_fixture")))
    assert (one.input.count, two.input.count) == (4, 9)
    assert router.get_last_normalized_usage() is None


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


def test_inclusive_child_conflict_and_local_purpose_rejection_make_zero_transport_calls(store):
    usage, _, run, scope = store
    parent = replace(start_at(run), kind="embedding", usage_relation="inclusive_parent", output_applicability="not_applicable")
    transport = FakeTransport({"usage": {"prompt_tokens": 99}})
    router = PurposeRouter([EMBED_PM], transport=transport)
    with pytest.raises(UsageError, match="accounting_conflict"):
        child_request_start(parent, accepted_at=AT, kind="embedding", model=EMBED_PM.model,
                            usage_relation="exclusive", applicability=("applicable", "not_applicable"))
        router.dispatch("embedding", {"model": EMBED_PM.model})
    usage.start(parent, authority_scope=scope)
    with pytest.raises(PurposeError):
        router.dispatch("embedding", {"model": "unknown_fixture"})
    usage.finalize(Terminal(parent.request_id, END, False, "rejected", "not_applicable", "rejected",
                            RouteAssociation(), router.get_last_normalized_usage()))
    assert transport.calls == []
    assert rows(usage, "usage_cumulative")[0]["attempts"] == rows(usage, "usage_cumulative")[0]["measured_input"] == 0


def test_child_descriptor_requires_explicit_valid_relation_units_and_accepted_order(store):
    _, _, run, _ = store
    parent = start_at(run)
    for changes in ({"usage_relation": "guess"}, {"accepted_at": "2026-10-07T11:59:59Z"},
                    {"applicability": ("guess", "applicable")}):
        options = dict(accepted_at=AT, kind="embedding", model=EMBED_PM.model, usage_relation="exclusive",
                       applicability=("applicable", "not_applicable"))
        options.update(changes)
        with pytest.raises(ValueError):
            child_request_start(parent, **options)
