"""Private temporary roots for router credential tests."""
from __future__ import annotations

import sys

import pytest


@pytest.fixture
def tmp_path(tmp_path_factory):
    if sys.platform != "win32":
        yield tmp_path_factory.mktemp("keys")
        return
    from tests.bootstrap_windows_fixtures import windows_fixture_tree

    with windows_fixture_tree() as tree:
        yield tree.root
