import json
import types

from anvil_serving import models


def _recipe():
    return {
        "model": "org/candidate",
        "download": {"revision": "0123456789abcdef0123456789abcdef01234567"},
    }


def _inspect():
    return json.dumps([{
        "Id": "e" * 64,
        "Config": {
            "Image": "example/runtime:1",
            "Labels": {
                "io.anvil-serving.managed-by": "models-recipes",
                "io.anvil-serving.recipe.model": "org/candidate",
                "io.anvil-serving.recipe.revision": "0123456789abcdef0123456789abcdef01234567",
            },
        },
        "Image": "sha256:" + "a" * 64,
        "State": {"Status": "running", "Running": True},
    }])


def test_recipe_logs_filter_case_insensitive_literals_across_streams(capsys):
    def run(argv, **_kwargs):
        if argv[:2] == ["docker", "inspect"]:
            return types.SimpleNamespace(returncode=0, stdout=_inspect(), stderr="")
        return types.SimpleNamespace(
            returncode=0,
            stdout="loading shard 1\nKV cache ready\n",
            stderr="warning only\nCUDA ERROR at decode\n",
        )

    assert models._recipe_container_logs(
        _recipe(),
        "candidate",
        contains=["kv CACHE", "error"],
        _run=run,
    ) == 0

    captured = capsys.readouterr()
    assert captured.out == "KV cache ready\n"
    assert captured.err == "CUDA ERROR at decode\n"


def test_recipe_logs_reject_multiline_filter_before_reading_logs():
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return types.SimpleNamespace(returncode=0, stdout=_inspect(), stderr="")

    try:
        models._recipe_container_logs(
            _recipe(), "candidate", contains=["ERROR\nnext"], _run=run
        )
    except models.serve_recipes.RecipeError as exc:
        assert "single-line literals" in str(exc)
    else:
        raise AssertionError("multiline log filter was accepted")
    assert calls == [["docker", "inspect", "candidate"]]


def test_recipe_logs_parser_retains_repeated_filters():
    args = models._build_recipe_parser().parse_args([
        "logs",
        "org/candidate",
        "--container",
        "candidate",
        "--contains",
        "ERROR",
        "--contains",
        "KV cache",
    ])

    assert args.contains == ["ERROR", "KV cache"]
