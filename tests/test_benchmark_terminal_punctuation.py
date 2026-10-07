"""Semantic answer punctuation remains bounded and preserves strict history."""
import pytest

from anvil_serving.benchmarking.specs import evaluate_text_checks, _compile_safe_check_regex


@pytest.mark.parametrize("answer", ["42", "42.", "42!", "42?", " 42.\n"])
def test_optional_single_terminal_punctuation_accepts_same_answer(answer):
    assert evaluate_text_checks(answer, [{"name": "answer", "matches_regex": r"^\s*42[.!?]?\s*$"}])[0]["passed"]


@pytest.mark.parametrize("answer", ["", "43.", "42..", "42. 43", "The answer is 42.", "<think>42</think>"])
def test_optional_terminal_punctuation_does_not_weaken_answer_identity(answer):
    assert not evaluate_text_checks(answer, [{"name": "answer", "matches_regex": r"^\s*42[.!?]?\s*$"}])[0]["passed"]


@pytest.mark.parametrize("pattern", [r"^[a]?42$", r"^42[.!?]*$", r"^42[.!?]?wrong$", r"^(42[.!?]?)+$", r"^42[.!?]?[.!?]?$", r"^42[.!?]?|43$", r"^.*[.!?]?$", r"^42[.!?]?\s*\s*$"])
def test_general_and_composed_quantifiers_remain_rejected(pattern):
    with pytest.raises(ValueError):
        _compile_safe_check_regex(pattern)


def test_historical_exact_answer_retains_punctuation_failure():
    check = [{"name": "historical", "matches_regex": r"^\s*Tea\s*$"}]
    assert evaluate_text_checks("Tea", check)[0]["passed"]
    assert not evaluate_text_checks("Tea.", check)[0]["passed"]
