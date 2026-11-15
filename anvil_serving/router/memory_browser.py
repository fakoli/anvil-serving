"""Bounded native memory vocabulary; no upstream objects cross this projection."""
from __future__ import annotations

import re
from urllib.parse import urlencode
from uuid import UUID

OPERATIONS = {"list", "detail", "invalidate", "restore"}
_TYPES = {"world", "experience", "observation"}
_STATES = {"valid", "invalidated"}
_TAG = re.compile(r"[A-Za-z0-9._:/=-]{1,128}$")


def _text(value, limit, *, optional=False):
    if optional and value is None:
        return None
    if type(value) is not str or len(value) > limit:
        raise ValueError("invalid memory field")
    return value


def record_id(value):
    if type(value) is not str or len(value) != 36 or str(UUID(value)) != value.lower():
        raise ValueError("invalid memory identifier")
    return value.lower()


def request(operation, arguments):
    """Return a validated bank-relative path, method, and bounded body."""
    if type(arguments) is not dict:
        raise ValueError("invalid memory arguments")
    if operation == "list":
        if set(arguments) - {"limit", "offset", "type", "state", "q", "tags", "tags_match"}:
            raise ValueError("unsupported memory filter")
        limit, offset = arguments.get("limit", 50), arguments.get("offset", 0)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 1000000:
            raise ValueError("invalid memory pagination")
        query = {"limit": limit, "offset": offset, "state": arguments.get("state", "valid")}
        if query["state"] not in _STATES:
            raise ValueError("invalid memory state")
        if "type" in arguments:
            if arguments["type"] not in _TYPES:
                raise ValueError("invalid memory type")
            query["type"] = arguments["type"]
        if "q" in arguments:
            query["q"] = _text(arguments["q"], 512)
        if "tags" in arguments:
            tags = arguments["tags"]
            if type(tags) is not list or len(tags) > 16 or any(type(t) is not str or not _TAG.fullmatch(t) for t in tags):
                raise ValueError("invalid memory tags")
            query["tags"] = tags
        if "tags_match" in arguments:
            if arguments["tags_match"] not in {"any_strict", "all_strict", "exact"}:
                raise ValueError("native tag matching must be strict")
            query["tags_match"] = arguments["tags_match"]
        elif query.get("tags"):
            query["tags_match"] = "all_strict"
        return "/memories/list?" + urlencode(query, doseq=True), "GET", None
    allowed = {"id", "reason"} if operation == "invalidate" else {"id"}
    if set(arguments) - allowed or "id" not in arguments:
        raise ValueError("invalid memory detail arguments")
    path = "/memories/" + record_id(arguments["id"])
    if operation == "detail":
        return path, "GET", None
    if operation not in {"invalidate", "restore"}:
        raise ValueError("unsupported memory operation")
    body = {"state": "invalidated" if operation == "invalidate" else "valid"}
    if operation == "invalidate":
        reason = _text(arguments.get("reason"), 1024)
        if not reason.strip():
            raise ValueError("invalidation requires a reason")
        body["reason"] = reason
    return path, "PATCH", body


def project_record(raw, *, expected_id=None):
    if type(raw) is not dict:
        raise ValueError("invalid memory record")
    identity = record_id(raw.get("id"))
    if expected_id is not None and identity != record_id(expected_id):
        raise ValueError("memory identity changed")
    kind, state = raw.get("type", raw.get("fact_type")), raw.get("state", "valid")
    if kind not in _TYPES or state not in _STATES:
        raise ValueError("invalid memory record kind")
    result = {"id": identity, "type": kind, "state": state,
              "text": _text(raw.get("text"), 32768), "context": _text(raw.get("context", ""), 8192)}
    for key in ("date", "mentioned_at", "occurred_start", "occurred_end", "updated_at", "invalidated_at"):
        result[key] = _text(raw.get(key), 64, optional=True)
    # Only opaque identifier-shaped provenance is exposed. Upstream accepts
    # arbitrary document IDs, including capability URLs and absolute host paths.
    for key in ("document_id", "chunk_id"):
        value = raw.get(key)
        result[key] = value if type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}", value) else None
    result["invalidation_reason"] = _text(raw.get("invalidation_reason"), 1024, optional=True)
    tags = raw.get("tags", [])
    if type(tags) is not list or len(tags) > 64:
        raise ValueError("invalid memory tags")
    result["tags"] = [_text(tag, 128) for tag in tags]
    sources = raw.get("source_memory_ids", [])
    if type(sources) is not list:
        raise ValueError("invalid memory sources")
    result["source_ids"] = list(dict.fromkeys(record_id(s) for s in sources[:64]))
    result["sources_truncated"] = len(sources) > 64
    # Observation entity inheritance in upstream 0.10.1 is not bank scoped.
    # Never expose inherited entities, inline source bodies, or history.
    entities = [] if kind == "observation" else raw.get("entities", [])
    if type(entities) is str:
        entities = [part.strip() for part in entities.split(",") if part.strip()]
    if type(entities) is not list or len(entities) > 128:
        raise ValueError("invalid memory entities")
    result["entities"] = [_text(entity, 256) for entity in entities]
    return result


def project_list(raw, arguments):
    if type(raw) is not dict or type(raw.get("items")) is not list:
        raise ValueError("invalid memory page")
    limit, offset = arguments.get("limit", 50), arguments.get("offset", 0)
    if (len(raw["items"]) > limit or type(raw.get("total")) is not int or raw["total"] < 0
            or raw.get("limit") != limit or raw.get("offset") != offset):
        raise ValueError("invalid memory pagination")
    items = [project_record(item) for item in raw["items"]]
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("duplicate memory records")
    return {"items": items, "total": raw["total"], "limit": limit, "offset": offset}
