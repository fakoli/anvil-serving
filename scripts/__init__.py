"""Top-level namespace for anvil-serving's standalone operator/harness scripts.

Not shipped in the installed wheel (see ``pyproject.toml``'s
``[tool.setuptools.packages.find]`` -- only ``anvil_serving*`` is packaged);
this exists purely so ``scripts/voice/*.py`` can share a small helper module
via a normal Python import while being run either as
``python scripts/voice/foo.py`` (repo checkout) or imported from tests
(``tests/voice/test_harness_importable.py``, which relies on this repo root
being on ``sys.path`` -- see ``pyproject.toml``'s ``[tool.pytest.ini_options]``
``pythonpath = ["."]``).
"""
