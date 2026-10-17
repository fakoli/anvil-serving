from anvil_serving.benchmarking.requests import (
    capacity_workload_identity,
    make_prompt,
    output_contract_observation,
)


def test_capacity_workload_identity_is_synthetic_and_deterministic():
    assert capacity_workload_identity(0) == {
        "prompt_contract_revision": "capacity-prompt-v1",
        "workload_kind": "synthetic-summary",
    }
    assert capacity_workload_identity(128) == {
        "prompt_contract_revision": "capacity-prompt-v1",
        "workload_kind": "synthetic-repeated-code",
    }


def test_prompt_declares_the_unchanged_canary_delimiter_contract():
    marker = "ANVIL_REQ_0_00000"
    for words in (0, 32):
        prompt = make_prompt("", 4096, 0, marker=marker, response_words=words)
        assert f"{marker}, followed by one space." in prompt
    good = output_contract_observation(marker + " summary", expected_marker=marker)
    colon = output_contract_observation(marker + ": summary", expected_marker=marker)
    assert good["request_canary"]["passed"]
    assert not colon["request_canary"]["passed"]
