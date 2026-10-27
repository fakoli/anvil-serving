"""Artifact-scoped, expiring read links; never expose the reusable API key."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
import time
import urllib.parse

from .errors import MediaError

LINK_SECONDS = 3600


class ArtifactDownloadLinks:
    def __init__(self, secret: str, principal: str, origin: str, *, clock=time.time):
        if not secret or not principal or not origin:
            raise ValueError("signed downloads require authenticated media configuration")
        self._secret = secret.encode("utf-8")
        self.principal = principal
        self.origin = origin.rstrip("/")
        self.clock = clock

    def _signature(self, artifact_id: str, expires: int) -> str:
        message = json.dumps(["anvil-media-read-v1", self.principal, artifact_id, expires],
                             separators=(",", ":")).encode("utf-8")
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def issue(self, artifact: dict) -> dict:
        expires = min(int(self.clock()) + LINK_SECONDS,
                      int(dt.datetime.fromisoformat(artifact["expiresAt"].replace("Z", "+00:00")).timestamp()))
        if expires <= self.clock():
            raise MediaError("artifact_not_found", "artifact was not found", status=404)
        query = urllib.parse.urlencode({"expires": expires, "signature": self._signature(artifact["id"], expires)})
        return {"downloadUrl": self.origin + artifact["resource"] + "?" + query,
                "requiresAuthentication": False, "downloadAuthorization": "signed-url",
                "downloadExpiresAt": dt.datetime.fromtimestamp(expires, dt.timezone.utc).isoformat()}

    def permits(self, artifact_id: str, query: str) -> bool:
        if len(query) > 256:
            return False
        values = urllib.parse.parse_qs(query, keep_blank_values=True)
        if set(values) != {"expires", "signature"} or any(len(v) != 1 for v in values.values()):
            return False
        expires, signature = values["expires"][0], values["signature"][0]
        if not re.fullmatch(r"[0-9]{1,12}", expires) or not re.fullmatch(r"[a-f0-9]{64}", signature):
            return False
        stamp = int(expires)
        if str(stamp) != expires or not self.clock() < stamp <= self.clock() + LINK_SECONDS:
            return False
        return hmac.compare_digest(signature, self._signature(artifact_id, stamp))


def read_range(header: str | None, total: int) -> tuple[int, int]:
    """Resolve one HTTP byte range, including open-ended and suffix ranges."""
    if header is None:
        return 0, total - 1
    match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", header)
    if not match or not any(match.groups()) or len(header) > 64:
        raise MediaError("artifact_range_invalid", "unsupported byte range", status=416)
    left, right = match.groups()
    if not left:
        suffix = int(right)
        if suffix < 1:
            raise MediaError("artifact_range_invalid", "invalid suffix range", status=416)
        return max(0, total - suffix), total - 1
    start, end = int(left), min(int(right), total - 1) if right else total - 1
    if start >= total or end < start:
        raise MediaError("artifact_range_invalid", "unsatisfiable byte range", status=416)
    return start, end
