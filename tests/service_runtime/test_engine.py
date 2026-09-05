"""Running HTTP servers do not imply resident models."""
import io
import json


def responses(mapping):
    def read(request, timeout):
        return io.BytesIO(json.dumps(mapping[request.full_url]).encode())
    return read


def test_mlx_advertised_model_is_not_residency_proof():
    from anvil_serving.service_runtime.engine import inspect
    binding = {"engine": "mlx-lm", "endpoint": "http://127.0.0.1:30113", "model": "example/model"}
    result = inspect(binding, open_url=responses({
        "http://127.0.0.1:30113/v1/models": {"data": [{"id": "example/model"}]}}))
    assert result["ready"] is True
    assert result["model_state"] == "unknown"
    assert result["loaded_models"] is None


def test_running_ollama_without_loaded_model():
    from anvil_serving.service_runtime.engine import inspect
    result = inspect({"engine": "ollama", "endpoint": "http://127.0.0.1:11434", "model": "qwen"},
                     open_url=responses({"http://127.0.0.1:11434/api/ps": {"models": []}}))
    assert result["ready"] is False
    assert result["endpoint_reachable"] is True
    assert result["loaded_models"] == []
    assert result["model_state"] == "not_loaded"


def test_lmstudio_catalog_separates_loaded_and_cached():
    from anvil_serving.service_runtime.engine import inspect
    result = inspect({"engine": "lmstudio", "endpoint": "http://127.0.0.1:1234"},
        open_url=responses({"http://127.0.0.1:1234/api/v0/models": {"data": [
            {"id": "cached", "state": "not-loaded"}, {"id": "resident", "state": "loaded"}]}}))
    assert result["loaded_models"] == ["resident"]


def test_unknown_and_failed_probe_never_reports_empty_residency():
    from anvil_serving.service_runtime.engine import inspect
    def fail(*args, **kwargs):
        raise OSError("token=do-not-print")
    result = inspect({"engine": "mlx-lm", "endpoint": "http://127.0.0.1:30113"}, open_url=fail)
    assert result["ready"] is False
    assert result["loaded_models"] is None
    assert "do-not-print" not in str(result)


def test_missing_endpoint_keeps_readiness_unknown():
    from anvil_serving.service_runtime.engine import inspect
    assert inspect({"engine": "none"})["ready"] is None


def test_api_base_path_does_not_duplicate_v1():
    from anvil_serving.service_runtime.engine import inspect
    result = inspect({"engine": "mlx-lm", "endpoint": "http://127.0.0.1:1234/v1"},
        open_url=responses({"http://127.0.0.1:1234/v1/models": {"data": []}}))
    assert result["ready"] is True


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
