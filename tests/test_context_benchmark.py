import pytest

from anvil_serving.benchmarking.context import (
    build_native_context_case,
    external_context_case,
    score_context_response,
)
from anvil_serving.benchmarking.jobs import BenchmarkJobError


def words(text):
    return len(text.split())


@pytest.mark.parametrize("position", [0.1, 0.5, 0.9])
def test_native_case_is_tokenizer_calibrated_and_positioned(position):
    case, expected = build_native_context_case(
        "native-needle",
        requested_tokens=512,
        position=position,
        repetition=0,
        token_counter=words,
    )
    assert case["calibrated_prompt_tokens"] <= 512
    assert case["calibrated_prompt_tokens"] > 450
    assert case["position"] == position
    assert expected["expected_answer"] not in repr({key: case[key] for key in case if key != "prompt"})


def test_multiple_target_and_distractor_cases_are_explicit():
    ordered, answer = build_native_context_case(
        "native-order", requested_tokens=512, position=0.5, repetition=1, token_counter=words
    )
    assert ordered["target_count"] == 3
    assert answer["expected_answer"].count(" | ") == 2
    distractor, _ = build_native_context_case(
        "native-distractor",
        requested_tokens=512,
        position=0.5,
        repetition=1,
        token_counter=words,
    )
    assert distractor["distractor_count"] == 2


def test_identifier_case_covers_exact_agent_workload_values():
    case, answer = build_native_context_case(
        "native-identifiers",
        requested_tokens=512,
        position=0.97,
        repetition=1,
        token_counter=words,
    )

    expected = answer["expected_answer"]
    assert case["position"] == 0.97
    assert case["target_count"] == 1
    assert "NAME=" in expected
    assert "UUID=" in expected
    assert "NUMBER=" in expected
    assert "IP=198.51.100." in expected
    assert "SYMBOL=resolve_orchid_" in expected


def test_crosslink_case_spreads_relationships_across_the_prompt():
    case, answer = build_native_context_case(
        "native-crosslink",
        requested_tokens=1024,
        position=0.1,
        repetition=1,
        token_counter=words,
    )

    prompt = case["prompt"]
    owner_index = prompt.index("Project ASTER is owned by")
    endpoint_index = prompt.index("The deployment endpoint assigned to")
    symbol_index = prompt.index("exports code symbol")
    assert len({owner_index, endpoint_index, symbol_index}) == 3
    assert max(owner_index, endpoint_index, symbol_index) - min(
        owner_index, endpoint_index, symbol_index
    ) > len(prompt) // 3
    assert case["target_count"] == 3
    assert answer["expected_answer"].startswith("ASTER -> ")


def test_visible_wrong_answer_fails_deterministic_semantic_score():
    case, expected = build_native_context_case(
        "native-needle", requested_tokens=256, position=0.5, repetition=0, token_counter=words
    )
    wrong = score_context_response(case, expected, "K000000000000")
    right = score_context_response(
        case,
        expected,
        "  " + expected["expected_answer"].lower() + "  ",
        observed_prompt_tokens=251,
    )
    assert wrong["passed"] is False
    assert right["passed"] is True
    assert right["prompt_tokens"] == 251
    assert right["token_measurement"] == "usage"


def test_external_adapter_requires_immutable_revision():
    with pytest.raises(BenchmarkJobError) as exc:
        external_context_case(
            adapter="mrcr",
            adapter_revision="main",
            case_id="mrcr-1",
            prompt="prompt",
            expected_answer="answer",
            requested_tokens=1024,
            observed_tokens=1000,
            position=0.5,
        )
    assert exc.value.code == "mutable_adapter"
