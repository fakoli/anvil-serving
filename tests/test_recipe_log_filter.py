import json


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
