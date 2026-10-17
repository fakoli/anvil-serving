import json

import pytest

from anvil_serving import benchmark as bm
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


def test_capacity_unique_prompt_namespace_uses_seed_but_shared_prompts_do_not(
    monkeypatch, tmp_path
):
    def prompts_for(seed, mode):
        prompts = []
        artifact = tmp_path / f"{mode}-{seed}.json"

        def stream_request(_base, _model, prompt, _key, _max_tokens, **_kwargs):
            prompts.append(prompt)
            return {
                "ttft": 0.01,
                "e2e": 0.02,
                "out_toks": 1,
                "usage": {"prompt_tokens": 100, "completion_tokens": 1},
            }

        monkeypatch.setattr(bm, "stream_chat", stream_request)
        assert bm.main([
            "capacity",
            "--base-url", "http://127.0.0.1:39015/v1",
            "--model", "candidate",
            "--requests", "1",
            "--concurrency", "1",
            "--ctx-tokens", "1024",
            "--max-model-len", "8192",
            "--prompt-cache-mode", mode,
            "--seed", str(seed),
            "--output", str(artifact),
        ], prog="benchmark") == 0
        return prompts, json.loads(artifact.read_text(encoding="utf-8"))

    first, first_artifact = prompts_for(7, "unique")
    repeated, _ = prompts_for(7, "unique")
    changed, _ = prompts_for(8, "unique")
    shared_first, shared_artifact = prompts_for(7, "shared")
    shared_changed, _ = prompts_for(8, "shared")

    assert first == repeated
    assert first != changed
    assert first[0].startswith("# unique benchmark request seed=7 index=00000\n")
    assert first_artifact["unique_prefix_namespace"] == "capacity-v1:seed-request-index"
    assert shared_first == shared_changed
    assert shared_artifact["unique_prefix_namespace"] is None


def test_unique_prefix_never_truncates_large_seed_headers():
    first_seed = "seed=" + "7" * 200
    second_seed = "seed=" + "8" * 200
    first = make_prompt("", 128, 0, unique_prefix=first_seed)
    second = make_prompt("", 128, 0, unique_prefix=second_seed)

    assert first != second
    assert first.startswith(f"# unique benchmark request {first_seed}\n")
    for seed in (first_seed, second_seed):
        with pytest.raises(ValueError, match="unique benchmark prefix"):
            make_prompt("", 32, 0, unique_prefix=seed)

    shared = make_prompt("x" * 200, 32, 0)
    assert shared.endswith("# request 0: summarize the above in one line.")
