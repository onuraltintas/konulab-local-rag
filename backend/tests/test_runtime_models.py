from fastapi.testclient import TestClient

from app.main import app
from app.models import LLMProviderType
from app.runtime_models import ProviderModel


def test_runtime_model_settings_are_discovered_validated_and_persisted(monkeypatch) -> None:
    """Ayar değişimi, model listesi denetlenmeden etkin çalışma zamanına geçmez."""
    from app import main

    async def fake_list(provider, _url):
        return [ProviderModel(id="qwen-test", label="Qwen Test")] if provider == LLMProviderType.OLLAMA else [ProviderModel(id="vllm-test", label="vLLM Test")]

    validated = []

    async def fake_validate(config):
        validated.append(config)

    monkeypatch.setattr(main, "list_provider_models", fake_list)
    monkeypatch.setattr(main, "validate_runtime_model_config", fake_validate)
    payload = {
        "text_provider": "vllm",
        "ollama_base_url": "http://127.0.0.1:11434",
        "ollama_model": "qwen-test",
        "vllm_base_url": "http://127.0.0.1:8001",
        "vllm_model": "vllm-test",
        "vision_provider": "ollama",
        "vision_model": "qwen-test",
        "context_length": 16384,
    }

    with TestClient(app) as client:
        initial = client.get("/api/settings/models")
        tested = client.post("/api/settings/models/test", json=payload)
        saved = client.put("/api/settings/models", json=payload)
        reread = client.get("/api/settings/models")

    assert initial.status_code == 200
    assert tested.status_code == 200
    assert saved.status_code == 200
    assert reread.status_code == 200
    assert len(validated) == 2
    assert saved.json()["text_provider"] == "vllm"
    assert reread.json()["vllm_model"] == "vllm-test"
    assert reread.json()["providers"][0]["models"]
