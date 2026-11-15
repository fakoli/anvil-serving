"""Upload a local reference using the existing protected MCP connection."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import time
import urllib.parse

from ..control_plane.mcp.controller_client import resolve_controller_token, resolve_controller_token_file
from ..control_plane.mcp.errors import ToolError
from ..control_plane.mcp.security import safe_controller_url
from .assets import ASSET_PATH, MAX_ASSET_BYTES
from .watch import mcp_client

SUFFIX_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".mp4": "video/mp4", ".wav": "audio/wav"}


def upload(path, config_path, *, dry_run=False):
    path, config_path = Path(path).expanduser(), Path(config_path).expanduser()
    length = path.stat().st_size
    media_type = SUFFIX_TYPES.get(path.suffix.lower())
    if not path.is_file() or not 0 < length <= MAX_ASSET_BYTES or media_type is None:
        raise ValueError("invalid reference file")
    if config_path.stat().st_size > 65536:
        raise ValueError("invalid connection definition")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    args = config["args"]
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
        raise ValueError("invalid connection arguments")

    def option(name):
        if args.count(name) > 1:
            raise ValueError("duplicate connection option")
        return args[args.index(name) + 1] if name in args else ""

    endpoint = urllib.parse.urlsplit(safe_controller_url(option("--controller-url")))
    auth_file, auth_env = option("--auth-file"), option("--auth-env")
    if bool(auth_file) == bool(auth_env):
        raise ValueError("one protected authentication source required")
    token = (resolve_controller_token_file(auth_file) if auth_file else
             resolve_controller_token(auth_env, {**os.environ, **config.get("env", {})}))
    with mcp_client(config_path, time.monotonic() + 60) as call:
        policy = call("media_capabilities", {})["assetUpload"]
    if (policy.get("available") is not True or policy.get("path") != ASSET_PATH
            or policy.get("requiresAuthentication") is not True
            or length > policy["maxBytes"] or media_type not in policy["mediaTypes"]):
        raise ValueError("reference intake unavailable or outside policy")
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if dry_run:
        return {"dryRun": True, "byteLength": length, "mediaType": media_type, "sha256": digest}
    # Derive the destination from the trusted connection, never from a returned host or redirect.
    connection_type = http.client.HTTPSConnection if endpoint.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(endpoint.hostname, endpoint.port, timeout=120)
    try:
        with path.open("rb") as source:
            connection.request("POST", ASSET_PATH, body=source,
                               headers={"Authorization": "Bearer " + token,
                                        "Content-Length": str(length), "Content-Type": media_type,
                                        "X-Content-SHA256": digest})
        response = connection.getresponse()
        raw = response.read(65537)
        if response.status != 201 or len(raw) > 65536:
            raise ValueError("reference upload rejected")
        artifact = json.loads(raw)["artifact"]
        if (artifact.get("origin") != "input" or artifact.get("sha256") != digest
                or artifact.get("byteLength") != length or artifact.get("mediaType") != media_type):
            raise ValueError("reference receipt mismatch")
        return {"artifact": artifact}
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True)
    parser.add_argument("--client-config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = upload(args.file, args.client_config, dry_run=args.dry_run)
    except (OSError, ValueError, KeyError, IndexError, TypeError, TimeoutError, ToolError, http.client.HTTPException):
        # Configuration, URLs and decoder errors can contain private data.
        print(json.dumps({"error": "media_asset_upload_failed"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
