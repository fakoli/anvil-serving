"""Supported operator entry points for approved propagation."""

from .spec import _handler, _node, _option


def _leaf(name, summary, *, options=(), mutate=False, prefix=None):
    return _node(
        name, summary,
        options=tuple(options) + ((_option("--confirm", summary="Confirm this owner request."),) if mutate else ()),
        handler=_handler("anvil_serving.workflows_cli", argv_prefix=prefix or (name,),
                         forward_confirm_flag=mutate),
        mutation_class="mutate" if mutate else "read",
    )


def workflow_command():
    return _node(
        "workflows", "Operate approved propagation through the authenticated owner.",
        children=(
            _leaf("capabilities", "Require the installed owner to expose this release's complete contract."),
            _leaf("start", "Accept one approved propagation contract.", mutate=True,
                  options=(_option("--approval-ref", summary="Approved contract reference.", value_name="REF"),
                           _option("--request-id", summary="Stable request identity.", value_name="ID"))),
            _leaf("status", "Read complete owner and workflow progress.",
                  options=(_option("--intent-id", summary="Accepted intent identity.", value_name="ID"),)),
            _leaf("resume", "Resume retained work under the same approved revision.", mutate=True,
                  options=(_option("--intent-id", summary="Accepted intent identity.", value_name="ID"),
                           _option("--expected-digest", summary="Exact accepted contract digest.", value_name="SHA256"))),
            _leaf("cancel", "Request cancellation of retained work.", mutate=True,
                  options=(_option("--intent-id", summary="Accepted intent identity.", value_name="ID"),
                           _option("--expected-digest", summary="Exact accepted contract digest.", value_name="SHA256"))),
            _node("deployment", "Inspect pinned release artifacts and installed owner profile.", children=(
                _leaf("preview", "Verify local release bytes without installation.", prefix=("deployment", "preview"),
                      options=(_option("--profile", summary="Named workflow profile.", value_name="PROFILE"),)),
                _leaf("verify", "Compare pinned release and installed owner profile.", prefix=("deployment", "verify"),
                      options=(_option("--profile", summary="Named workflow profile.", value_name="PROFILE"),)),
            )),
            _node("recovery", "Inspect isolated recovery evidence through the owner.", children=(
                _leaf("verify", "Read verified isolated-restore evidence.",
                      prefix=("recovery", "verify"),
                      options=(_option("--profile", summary="Named workflow profile.", value_name="PROFILE"),)),
                _leaf("snapshot-journal", "Copy the guarded native operation journal for encrypted backup.",
                      prefix=("recovery", "snapshot-journal"), mutate=True,
                      options=(_option("--profile", summary="Named workflow profile.", value_name="PROFILE"),
                               _option("--output", summary="New protected snapshot directory.", value_name="PATH"))),
            )),
        ),
        docs_anchor="docs/cli/control-plane.md#workflows",
    )
