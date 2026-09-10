"""Isolated, uncredentialed public download child; parent owns the wall deadline."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def http_error_302(self, request, fp, code, message, headers):
        raise URLError("redirect refused")

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def download(url: str, target: Path, maximum: int) -> tuple[str, int]:
    # The source URL is checked independently here so this fixed helper cannot
    # become a caller-selected URL fetcher through malformed invocation.
    base = "https://cloud-images.ubuntu.com/releases/noble/release-20260826/"
    if url not in {base + name for name in ("ubuntu-24.04-server-cloudimg-amd64.img", "SHA256SUMS", "SHA256SUMS.gpg")}:
        raise ValueError
    if not 0 < maximum <= 768 * 1024 * 1024:
        raise ValueError
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(url, headers={"User-Agent": "anvil-connect-qualification/1"})
    with opener.open(request, timeout=30) as response:
        if response.geturl() != url:
            raise ValueError
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdecimal() or int(length) > maximum):
            raise ValueError
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise ValueError
                pending = memoryview(chunk)
                while pending:
                    written = os.write(descriptor, pending)
                    if written <= 0:
                        raise OSError
                    pending = pending[written:]
                digest.update(chunk)
            os.fsync(descriptor)
            if os.fstat(descriptor).st_size != total:
                raise OSError
            return digest.hexdigest(), total
        finally:
            os.close(descriptor)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 4:
            raise ValueError
        digest, size = download(sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]))
        print(json.dumps({"sha256": digest, "bytes": size}, separators=(",", ":")))
    except Exception:
        # Parent records only a classified failure, never HTTP/server text.
        raise SystemExit(1) from None
