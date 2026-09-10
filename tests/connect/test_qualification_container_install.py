"""The build helper verifies public assets before exposing executables."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import urllib.request
import sys

import pytest

PATH = Path(__file__).parents[2] / "connect/test/container/install_tools.py"
SPEC = importlib.util.spec_from_file_location("connect_qualification_install", PATH)
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def test_redirect_refuses_tls_downgrade_before_request():
    request = urllib.request.Request("https://github.com/example/tool")
    with pytest.raises(ValueError, match="non-TLS"):
        subject.HTTPSRedirect().redirect_request(request, None, 302, "redirect", {}, "http://example.test/asset")


@pytest.mark.parametrize("symlink,bad_hash", [(False, False), (True, False), (False, True)])
@pytest.mark.skipif(sys.platform != "linux", reason="requires Linux executable file permissions")
def test_only_verified_regular_binary_is_accepted(tmp_path, monkeypatch, symlink, bad_hash):
    tools = tmp_path / "tools"
    tools.mkdir()
    monkeypatch.setattr(subject, "TOOLS", tools)
    content = b"synthetic executable"
    archive = tmp_path / "tool.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo("caddy")
        if symlink:
            member.type = tarfile.SYMTYPE
            member.linkname = "../../outside"
            output.addfile(member)
        else:
            member.size = len(content)
            output.addfile(member, io.BytesIO(content))
    digest = "0" * 64 if bad_hash else hashlib.sha256(content).hexdigest()
    if symlink or bad_hash:
        with pytest.raises(ValueError):
            subject.binary(archive, "caddy", digest)
    else:
        subject.binary(archive, "caddy", digest)
        assert (tools / "caddy").read_bytes() == content
        assert (tools / "caddy").stat().st_mode & 0o777 == 0o755
