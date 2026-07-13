"""Purpose-model routing: /v1/embeddings and /v1/rerank (gpu-reservations:T010).

ADR-0017 §7: embedding/reranker serves are ordinary compose-defined serves; the
front door grows the matching endpoints and routes them **by model name** — the
request's ``model`` field is resolved against the configured
``[[router.purpose_models]]`` entries (:class:`~anvil_serving.router.config.PurposeModel`).

This is deliberately NOT the chat pipeline:

* No intent classification, no policy, no verify/fallback ladder — a purpose
  request names exactly one serve or it fails. **An unknown model name is a
  clean HTTP 404 naming the configured models for that kind; it is never a
  fallthrough to chat routing** (the T010 acceptance criterion).
* The validated body is relayed VERBATIM to the serve's OpenAI-compatible
  endpoint (``{base_url}/embeddings`` or ``{base_url}/rerank``) over the same
  stdlib ``urllib`` transport the tier backends use, and the upstream JSON is
  returned to the caller unchanged.
* Every dispatched request is recorded in the shared
  :class:`~anvil_serving.router.decision_log.DecisionLog` (work_class =
  ``"embedding"`` / ``"rerank"``) and emits the standard content-free
  ``decision_line`` to stderr, so purpose traffic shows up in ``GET
  /v1/decisions`` and the container logs alongside chat decisions.

Secrets hygiene: a purpose model's optional ``auth_env`` names an env var whose
value is resolved ONCE at construction, sent only as the outbound bearer
header, and never logged (the transport already guarantees sanitized errors).

Stdlib-only.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .backends.cloud import CloudBackendError, Transport, _urlopen_transport
from .config import PURPOSE_EMBEDDING, PURPOSE_RERANK, PurposeModel
from .decision_log import AttemptRecord, DecisionLog, DecisionRecord, decision_line

#: Upstream path per purpose kind, appended to the model's ``base_url``
#: (vLLM serves the OpenAI Embeddings API at ``/v1/embeddings`` and the
#: Jina-style rerank API at ``/v1/rerank``; base_url carries the ``/v1``).
_KIND_PATHS: Mapping[str, str] = {
    PURPOSE_EMBEDDING: "/embeddings",
    PURPOSE_RERANK: "/rerank",
}

#: Cap an upstream purpose response at 32 MiB — matches the front door's
#: default request cap; an embeddings matrix for a large batch is well under
#: this, and the cap keeps a misbehaving serve from OOM-ing the router.
_MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class PurposeError(Exception):
    """A purpose request failed with a caller-facing HTTP error.

    Carries the HTTP ``status``, the OpenAI-envelope error ``etype``, and a
    sanitized ``message`` (never upstream internals or credentials). The front
    door renders it in the OpenAI error envelope.
    """

    def __init__(self, status: int, etype: str, message: str):
        super().__init__(message)
        self.status = status
        self.etype = etype
        self.message = message


class PurposeRouter:
    """Resolve purpose requests by model name and relay them to their serve.

    ``models`` is the validated config tuple. ``decision_log`` is normally the
    ROUTING backend's log so purpose decisions appear in the same audit trail
    (``GET /v1/decisions``); a standalone log also works. ``transport`` is the
    injectable HTTP seam (hermetic tests); the default is the same
    ``urllib``-based transport the tier backends use. ``default_timeout``
    mirrors ``[router].relay_timeout`` — purpose serves are local, so fail
    fast; a per-model ``timeout`` overrides it.

    A model whose ``auth_env`` is set but unresolved in the environment is
    SKIPPED with a stderr warning (mirrors the cloud-tier skip-not-fatal
    stance in ``serve.build_backends``): the server still starts and every
    other purpose model stays routable.
    """

    def __init__(
        self,
        models: Sequence[PurposeModel],
        *,
        env: Optional[Mapping[str, str]] = None,
        transport: Optional[Transport] = None,
        default_timeout: float = 20.0,
        decision_log: Optional[DecisionLog] = None,
    ) -> None:
        environ: Mapping[str, str] = os.environ if env is None else env
        self._transport: Transport = transport or _urlopen_transport
        self._default_timeout = default_timeout
        self._log = decision_log
        # (kind, model-name) -> (PurposeModel, resolved bearer token or None)
        self._routes: Dict[Tuple[str, str], Tuple[PurposeModel, Optional[str]]] = {}
        for pm in models:
            token: Optional[str] = None
            if pm.auth_env:
                token = (environ.get(pm.auth_env) or "").strip() or None
                if token is None:
                    print(
                        f"[anvil-serving] purpose model {pm.id!r} not bound: "
                        f"auth_env {pm.auth_env!r} is unset/empty in the "
                        f"environment",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
            self._routes[(pm.kind, pm.model)] = (pm, token)

    # ------------------------------------------------------------------ #
    # introspection
    # ------------------------------------------------------------------ #
    def model_ids(self, kind: str) -> Tuple[str, ...]:
        """The bound model names for ``kind``, sorted (for error messages)."""
        return tuple(sorted(m for (k, m) in self._routes if k == kind))

    def __len__(self) -> int:
        return len(self._routes)

    # ------------------------------------------------------------------ #
    # dispatch
    # ------------------------------------------------------------------ #
    def dispatch(self, kind: str, body: Mapping[str, Any]) -> Dict[str, Any]:
        """Route one validated ``kind`` request to its serve; return the JSON.

        Raises :class:`PurposeError` on an unknown model name (404 — the
        request never reaches any serve, and NEVER falls through to chat
        routing) or an upstream failure (502, sanitized). A dispatched attempt
        — served or errored — is recorded in the decision log.
        """
        model = str(body.get("model") or "")
        route = self._routes.get((kind, model))
        if route is None:
            known = self.model_ids(kind)
            known_label = ", ".join(repr(m) for m in known) or "(none configured)"
            # Deliberately NO chat fallthrough: an unknown purpose model is a
            # caller error, not a routing decision (T010 acceptance criterion).
            raise PurposeError(
                404,
                "model_not_found",
                f"unknown {kind} model {model!r}; this router serves {kind} "
                f"models: {known_label}. {kind} requests are routed by model "
                f"name only and never fall through to chat routing.",
            )
        pm, token = route

        url = pm.base_url.rstrip("/") + _KIND_PATHS[kind]
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(dict(body)).encode("utf-8")
        timeout = pm.timeout if pm.timeout is not None else self._default_timeout

        try:
            raw = self._transport(
                url, data=data, headers=headers, timeout=timeout,
                max_bytes=_MAX_RESPONSE_BYTES,
            )
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise CloudBackendError("upstream response is not a JSON object")
        except CloudBackendError as e:
            # Transport errors are already sanitized (no URL/credential leak).
            self._record(kind, pm.id, outcome="error",
                         reason=f"backend error: {type(e).__name__}")
            print(
                f"[anvil] 502 {kind} serve {pm.id!r} failed: {e}",
                file=sys.stderr, flush=True,
            )
            raise PurposeError(
                502, "upstream_error",
                f"{kind} serve for model {model!r} failed; see router logs",
            ) from None
        except (ValueError, UnicodeDecodeError):
            self._record(kind, pm.id, outcome="error",
                         reason="backend error: non-JSON response")
            print(
                f"[anvil] 502 {kind} serve {pm.id!r} returned a non-JSON body",
                file=sys.stderr, flush=True,
            )
            raise PurposeError(
                502, "upstream_error",
                f"{kind} serve for model {model!r} returned a malformed "
                f"response; see router logs",
            ) from None

        self._record(
            kind, pm.id, outcome="served",
            prompt_tokens=_usage_prompt_tokens(payload),
        )
        return payload

    # ------------------------------------------------------------------ #
    # decision logging (metadata only — R012)
    # ------------------------------------------------------------------ #
    def _record(
        self,
        kind: str,
        purpose_id: str,
        *,
        outcome: str,
        prompt_tokens: int = 0,
        reason: str = "-",
    ) -> None:
        served = outcome == "served"
        record = DecisionRecord(
            work_class=kind,
            requested_tiers=(purpose_id,),
            attempts=(
                AttemptRecord(
                    tier_id=purpose_id,
                    verifier_passed=served,
                    verify_reason=reason,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=0,
                    outcome=outcome,
                ),
            ),
            served_tier=purpose_id if served else None,
            total_prompt_tokens=prompt_tokens,
            total_completion_tokens=0,
            fell_back=False,
            intent=kind,
        )
        if self._log is not None:
            self._log.record(record)
        # Same content-free audit line the chat path emits (docker logs).
        print(f"[anvil] decision {decision_line(record)}",
              file=sys.stderr, flush=True)


def _usage_prompt_tokens(payload: Mapping[str, Any]) -> int:
    """Best-effort integer prompt-token count from an upstream ``usage`` block."""
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return 0
    value = usage.get("prompt_tokens", usage.get("total_tokens", 0))
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)
