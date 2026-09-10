"""Build-only installer for public, digest-verified qualification tools."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request
from urllib.parse import urlsplit

BUILD = Path("/build")
TOOLS = Path("/opt/connect-tools")
MAX_ARCHIVE = 256 * 1024 * 1024


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if urlsplit(new_url).scheme != "https":
            raise ValueError("non-TLS tool redirect")
        return super().redirect_request(request, response, code, message, headers, new_url)


def fetch(url: str, algorithm: str, expected: str, destination: Path) -> None:
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname not in {"go.dev", "github.com"}:
        raise ValueError("unrecognized qualification tool source")
    digest = hashlib.new(algorithm)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), HTTPSRedirect())
    size = 0
    with opener.open(url, timeout=60) as response, destination.open("xb") as output:
        if urlsplit(response.url).scheme != "https":
            raise ValueError("non-TLS tool redirect")
        while chunk := response.read(64 * 1024):
            size += len(chunk)
            if size > MAX_ARCHIVE:
                raise ValueError("qualification archive exceeds bound")
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != expected:
        raise ValueError("qualification archive checksum mismatch")


def binary(archive: Path, name: str, expected: str) -> None:
    with tarfile.open(archive) as source:
        member = source.getmember(name)
        if not member.isfile() or member.size > MAX_ARCHIVE:
            raise ValueError("qualification binary is not a bounded regular file")
        with source.extractfile(member) as stream, (TOOLS / name).open("xb") as output:
            shutil.copyfileobj(stream, output)
    if hashlib.sha256((TOOLS / name).read_bytes()).hexdigest() != expected:
        raise ValueError("qualification binary checksum mismatch")
    (TOOLS / name).chmod(0o755)


def main() -> None:
    pins = json.loads((BUILD / "pins.json").read_text())
    edge = json.loads((BUILD / "edge-tools.json").read_text())
    transport = json.loads((BUILD / "transport.lock.json").read_text())
    TOOLS.mkdir(mode=0o755)
    with tempfile.TemporaryDirectory(prefix="connect-build-") as temporary:
        root = Path(temporary)
        archive = root / "go.tar.gz"
        fetch(pins["go"]["url"], "sha256", pins["go"]["sha256"], archive)
        with tarfile.open(archive) as source:
            source.extractall("/opt", filter="data")
        for component in edge["components"]:
            archive = root / (component["name"] + ".tar.gz")
            fetch(component["url"], component["checksum"]["algorithm"], component["checksum"]["value"], archive)
            binary(archive, component["name"], component["binary_sha256"])
        tunnel = transport["artifacts"]["linux/amd64"]
        archive = root / "wstunnel.tar.gz"
        fetch(tunnel["url"], "sha256", tunnel["archive_sha256"], archive)
        binary(archive, "wstunnel", tunnel["binary_sha256"])


if __name__ == "__main__":
    main()
