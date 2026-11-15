"""Running HTTP servers do not imply resident models."""
import io
import json


def responses(mapping):
    def read(request, timeout):
        return io.BytesIO(json.dumps(mapping[request.full_url]).encode())
    return read


def test_large_inventory_does_not_report_false_absence():
    from anvil_serving.service_runtime.engine import inspect
    result = inspect({"engine": "ollama", "endpoint": "http://127.0.0.1:1234", "model": "last"},
        open_url=responses({"http://127.0.0.1:1234/api/ps": {"models": [
            {"name": str(i)} for i in range(128)] + [{"name": "last"}]}}))
    assert result["model_state"] == "loaded"
    assert len(result["loaded_models"]) <= 128


def test_authenticated_metadata_redirect_is_refused():
    import urllib.error
    import urllib.request
    from anvil_serving.service_runtime.engine import NoRedirect
    request = urllib.request.Request("http://127.0.0.1:1234/v1/models", headers={"Authorization": "Bearer example"})
    assert NoRedirect().redirect_request(request, None, 302, "redirect", {}, "https://example.org/") is None


def test_metadata_uses_durable_credential_fallback_without_disclosing_it(tmp_path, monkeypatch):
    from anvil_serving import envfile
    from anvil_serving.service_runtime.engine import inspect
    secret_file = tmp_path / ".env"
    secret_file.write_text("TEST_METADATA_TOKEN=file-fixture\n", encoding="utf-8")
    monkeypatch.setattr(envfile, "fallback_paths", lambda: [str(secret_file)])
    monkeypatch.delenv("TEST_METADATA_TOKEN", raising=False)
    binding = {"engine": "none", "endpoint": "http://127.0.0.1:8766",
               "api_key_env": "TEST_METADATA_TOKEN"}
    observed = []
    def read(request, timeout):
        observed.append(request.get_header("Authorization"))
        return io.BytesIO(b'{"ok":true}')
    result = inspect(binding, open_url=read)
    assert result["ready"] is True
    assert observed == ["Bearer file-fixture"]
    assert "file-fixture" not in str(result)
    monkeypatch.setenv("TEST_METADATA_TOKEN", "environment-fixture")
    assert inspect(binding, open_url=read)["ready"] is True
    assert observed[-1] == "Bearer environment-fixture"
    monkeypatch.delenv("TEST_METADATA_TOKEN")
    secret_file.unlink()
    assert inspect(binding, open_url=read)["error"] == "credential_unavailable"
    assert len(observed) == 2
