"""Configuration inputs must not block or follow operator-path indirection."""
from pathlib import Path
import os
import sys

import pytest

from anvil_serving.connect.qualification import QualificationError, _read_config


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="requires Linux no-follow configuration reads")


@pytest.mark.parametrize("kind", ["symlink", "fifo", "oversized"])
def test_unsafe_config_refuses_without_reading(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "qualification.toml"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_text("schema='irrelevant'")
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(b"#" * (64 * 1024 + 1))
    with pytest.raises(QualificationError) as caught:
        _read_config(path)
    assert caught.value.code == "config-invalid"
