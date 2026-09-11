from anvil_serving.benchmarking.requests import make_prompt, output_contract_observation


def test_prompt_declares_the_unchanged_canary_delimiter_contract():
    marker = "ANVIL_REQ_0_00000"
    for words in (0, 32):
        prompt = make_prompt("", 4096, 0, marker=marker, response_words=words)
        assert f"{marker}, followed by one space." in prompt
    good = output_contract_observation(marker + " summary", expected_marker=marker)
    colon = output_contract_observation(marker + ": summary", expected_marker=marker)
    assert good["request_canary"]["passed"]
    assert not colon["request_canary"]["passed"]
