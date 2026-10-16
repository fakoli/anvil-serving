import pytest

from anvil_serving.benchmarking.jobs import BenchmarkJobError
from anvil_serving.benchmarking.request_controls import resolve_job_request_controls


@pytest.mark.parametrize("force_nonempty_content", [True, False])
def test_force_nonempty_content_is_allowlisted_with_enabled_thinking(force_nonempty_content):
    assert resolve_job_request_controls({
        "thinking_mode": "enabled",
        "force_nonempty_content": force_nonempty_content,
    })["chat_template_kwargs"] == {
        "enable_thinking": True,
        "force_nonempty_content": force_nonempty_content,
    }


@pytest.mark.parametrize(
    ("controls", "code"),
    [
        ({"unknown": True}, "bad_request_controls"),
        ({"thinking_mode": "enabled", "force_nonempty_content": "true"}, "bad_force_nonempty_content"),
        ({"thinking_mode": "enabled", "reasoning_effort": "max", "force_nonempty_content": True}, "conflicting_reasoning_controls"),
    ],
)
def test_request_controls_reject_invalid_or_conflicting_extensions(controls, code):
    with pytest.raises(BenchmarkJobError) as exc:
        resolve_job_request_controls(controls)

    assert exc.value.code == code


def test_request_controls_preserve_legacy_default_shape():
    assert resolve_job_request_controls({}) == {
        "thinking_mode": "default",
        "reasoning_effort": None,
        "chat_template_kwargs": None,
    }
