"""Wire validation for the purpose-model surfaces (gpu-reservations:T010).

Two non-chat, non-streaming endpoints ride the front door (ADR-0017 §7):

* ``POST /v1/embeddings`` — the OpenAI Embeddings shape
  (``{"model": ..., "input": ...}``);
* ``POST /v1/rerank`` — the Jina/Cohere-style rerank shape vLLM serves
  (``{"model": ..., "query": ..., "documents": [...]}``).

Neither is a full :class:`~anvil_serving.router.dialects.Dialect`: there is no
SSE stream, no ``InternalRequest``, and no chat routing — the request ``model``
field IS the routing key, resolved against the configured
``[[router.purpose_models]]`` by :class:`~anvil_serving.router.purpose.PurposeRouter`.
This module only validates the caller-facing wire shape and rejects malformed
bodies with typed :class:`~anvil_serving.router.internal.DialectError`\\ s
(rendered by the front door in the OpenAI error envelope, the native envelope
for both surfaces). The validated body is relayed verbatim, so optional
upstream knobs (``encoding_format``, ``dimensions``, ``top_n``, ...) pass
through untouched.

Stdlib-only, like every dialect module.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..internal import DialectError

#: Front-door route -> purpose kind (single source for front_door + tests).
EMBEDDINGS_PATH = "/v1/embeddings"
RERANK_PATH = "/v1/rerank"


def _require_model(body: Mapping[str, Any], surface: str) -> str:
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise DialectError(
            400,
            "invalid_request_error",
            f"{surface}: 'model' is required and must be a non-empty string "
            f"(the embedding/rerank serve's served-model-name)",
        )
    return model


def parse_embeddings_request(body: Mapping[str, Any]) -> str:
    """Validate an OpenAI-style embeddings body; return the ``model`` key.

    ``input`` may be a string, a list of strings, or a list of token arrays
    (the OpenAI contract). Only presence/shape is checked here — content
    limits are the serve's job. Raises :class:`DialectError` (HTTP 400) on a
    malformed body; never mutates ``body``.
    """
    model = _require_model(body, "embeddings")
    inp = body.get("input")
    if isinstance(inp, str):
        if not inp:
            raise DialectError(
                400, "invalid_request_error",
                "embeddings: 'input' must not be empty",
            )
    elif isinstance(inp, list):
        if not inp:
            raise DialectError(
                400, "invalid_request_error",
                "embeddings: 'input' must not be an empty list",
            )
    else:
        raise DialectError(
            400, "invalid_request_error",
            "embeddings: 'input' is required and must be a string or a "
            "non-empty list",
        )
    return model


def parse_rerank_request(body: Mapping[str, Any]) -> str:
    """Validate a rerank body (Jina/Cohere shape, as served by vLLM).

    Requires ``model``, a non-empty string ``query``, and a non-empty list
    ``documents``. Raises :class:`DialectError` (HTTP 400) on a malformed
    body; never mutates ``body``.
    """
    model = _require_model(body, "rerank")
    query = body.get("query")
    if not isinstance(query, str) or not query:
        raise DialectError(
            400, "invalid_request_error",
            "rerank: 'query' is required and must be a non-empty string",
        )
    documents = body.get("documents")
    if not isinstance(documents, list) or not documents:
        raise DialectError(
            400, "invalid_request_error",
            "rerank: 'documents' is required and must be a non-empty list",
        )
    return model
