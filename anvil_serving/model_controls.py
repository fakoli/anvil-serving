"""Shared model-family reasoning-control validation for eval commands."""


def validate_reasoning_control(model, *, thinking_mode, no_thinking,
                               reasoning_effort):
    """Reject controls known to be ignored or unsupported by a model family.

    Unknown families remain operator-controlled. Only published, high-confidence
    incompatibilities fail closed here so a new model is not guessed into the
    wrong protocol.
    """
    normalized = (model or "").casefold()
    explicit_template_control = bool(
        no_thinking or thinking_mode in {"enabled", "disabled"}
    )
    if "gpt-oss" in normalized:
        if explicit_template_control:
            raise ValueError(
                "GPT-OSS does not use Qwen chat_template_kwargs thinking control; "
                "use --reasoning-effort low|medium|high, or --thinking-mode "
                "unsupported when the endpoint cannot expose that control"
            )
        if reasoning_effort not in {None, "low", "medium", "high"}:
            raise ValueError(
                "GPT-OSS supports --reasoning-effort low, medium, or high"
            )
    if "qwen" in normalized and reasoning_effort is not None:
        raise ValueError(
            "Qwen uses chat-template thinking control; use --thinking-mode "
            "enabled|disabled instead of --reasoning-effort"
        )
