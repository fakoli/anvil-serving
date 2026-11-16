
from anvil_serving.benchmarking.context import (
    build_native_context_case,
)


def words(text):
    return len(text.split())


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
