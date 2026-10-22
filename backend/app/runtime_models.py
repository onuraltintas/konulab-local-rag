"""Çalışma anında değişebilen, sağlayıcıdan bağımsız LLM yapılandırması."""

from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.models import LLMProviderType, RuntimeModelSettings


class ModelProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderModel:
    id: str
    label: str
    size_bytes: int | None = None
    parameter_size: str | None = None
    quantization: str | None = None


@dataclass(frozen=True)
class RuntimeModelConfig:
    text_provider: LLMProviderType
    ollama_base_url: str
    ollama_model: str
    vllm_base_url: str
    vllm_model: str | None
    vision_provider: LLMProviderType
    vision_model: str
    context_length: int

    @property
    def text_model(self) -> str:
        return self.ollama_model if self.text_provider == LLMProviderType.OLLAMA else (self.vllm_model or "")

    @property
    def text_base_url(self) -> str:
        return self.ollama_base_url if self.text_provider == LLMProviderType.OLLAMA else self.vllm_base_url


_lock = threading.RLock()
_current = RuntimeModelConfig(
    text_provider=LLMProviderType.OLLAMA,
    ollama_base_url=get_settings().ollama_base_url,
    ollama_model=get_settings().ollama_model,
    vllm_base_url="http://127.0.0.1:8001",
    vllm_model=None,
    vision_provider=LLMProviderType.OLLAMA,
    vision_model=get_settings().vision_model,
    context_length=get_settings().ollama_context_length,
)


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ModelProviderError("Sunucu adresi http:// veya https:// ile geçerli bir adres olmalıdır.")
    return value


def config_from_record(record: RuntimeModelSettings) -> RuntimeModelConfig:
    return RuntimeModelConfig(
        text_provider=record.text_provider,
        ollama_base_url=normalize_base_url(record.ollama_base_url),
        ollama_model=record.ollama_model.strip(),
        vllm_base_url=normalize_base_url(record.vllm_base_url),
        vllm_model=record.vllm_model.strip() if record.vllm_model else None,
        vision_provider=record.vision_provider,
        vision_model=record.vision_model.strip(),
        context_length=record.context_length,
    )


def current_runtime_model_config() -> RuntimeModelConfig:
    with _lock:
        return _current


def set_runtime_model_config(config: RuntimeModelConfig) -> None:
    global _current
    with _lock:
        _current = config


def initialize_runtime_model_config(database) -> RuntimeModelSettings:
    """DB kaydını ortam değişkeni varsayılanlarıyla bir kez oluşturur."""
    record = database.get(RuntimeModelSettings, 1)
    if record is None:
        defaults = get_settings()
        record = RuntimeModelSettings(
            id=1,
            ollama_base_url=defaults.ollama_base_url,
            ollama_model=defaults.ollama_model,
            vision_model=defaults.vision_model,
            context_length=defaults.ollama_context_length,
        )
        database.add(record)
        database.commit()
        database.refresh(record)
    set_runtime_model_config(config_from_record(record))
    return record


async def list_provider_models(provider: LLMProviderType, base_url: str) -> list[ProviderModel]:
    base_url = normalize_base_url(base_url)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=15, write=10, pool=10)) as client:
            if provider == LLMProviderType.OLLAMA:
                response = await client.get(f"{base_url}/api/tags")
                response.raise_for_status()
                models = response.json().get("models", [])
                return [
                    ProviderModel(
                        id=str(item.get("name", "")).strip(),
                        label=str(item.get("name", "")).strip(),
                        size_bytes=int(item["size"]) if isinstance(item.get("size"), int) else None,
                        parameter_size=str(item.get("details", {}).get("parameter_size")) if item.get("details", {}).get("parameter_size") else None,
                        quantization=str(item.get("details", {}).get("quantization_level")) if item.get("details", {}).get("quantization_level") else None,
                    )
                    for item in models
                    if str(item.get("name", "")).strip()
                ]
            response = await client.get(f"{base_url}/v1/models")
            response.raise_for_status()
            models = response.json().get("data", [])
            return [ProviderModel(id=str(item.get("id", "")).strip(), label=str(item.get("id", "")).strip()) for item in models if str(item.get("id", "")).strip()]
    except (httpx.HTTPError, ValueError) as error:
        service = "Ollama" if provider == LLMProviderType.OLLAMA else "vLLM"
        raise ModelProviderError(f"{service} sunucusuna ulaşılamadı veya model listesi okunamadı.") from error


async def complete(
    config: RuntimeModelConfig,
    messages: list[dict[str, object]],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    json_mode: bool = False,
    think: bool = False,
    image: bytes | None = None,
    read_timeout: float = 300,
) -> str:
    provider = config.text_provider if model is None else config.vision_provider
    selected_model = model or config.text_model
    base_url = config.text_base_url if model is None else (config.ollama_base_url if provider == LLMProviderType.OLLAMA else config.vllm_base_url)
    if not selected_model:
        raise ModelProviderError("Seçili sağlayıcı için model belirtilmemiş.")
    prepared = [dict(message) for message in messages]
    if image is not None:
        if provider == LLMProviderType.OLLAMA:
            prepared[-1]["images"] = [base64.b64encode(image).decode("ascii")]
        else:
            content = str(prepared[-1].get("content", ""))
            prepared[-1]["content"] = [
                {"type": "text", "text": content},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode("ascii")}},
            ]
    if provider == LLMProviderType.OLLAMA:
        payload: dict[str, object] = {
            "model": selected_model, "stream": False, "think": think, "messages": prepared,
            "options": {"num_ctx": config.context_length, "temperature": temperature}, "keep_alive": "30m",
        }
        if max_tokens is not None:
            payload["options"] = {"num_ctx": config.context_length, "temperature": temperature, "num_predict": max_tokens}
        if json_mode:
            payload["format"] = "json"
        endpoint = f"{base_url}/api/chat"
    else:
        payload = {"model": selected_model, "messages": prepared, "temperature": temperature, "stream": False}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        endpoint = f"{base_url}/v1/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=read_timeout, write=30, pool=30)) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
        body = response.json()
        content = body.get("message", {}).get("content", "") if provider == LLMProviderType.OLLAMA else ((body.get("choices") or [{}])[0].get("message", {}).get("content", ""))
    except (httpx.HTTPError, ValueError, IndexError, KeyError) as error:
        raise ModelProviderError("Model yanıt vermiyor, seçili model yüklenemedi veya yanıt biçimi geçersiz.") from error
    if not isinstance(content, str) or not content.strip():
        raise ModelProviderError("Model boş bir yanıt döndürdü.")
    return content.strip()


async def validate_runtime_model_config(config: RuntimeModelConfig) -> None:
    models = await list_provider_models(config.text_provider, config.text_base_url)
    if config.text_model not in {item.id for item in models}:
        raise ModelProviderError("Seçili metin modeli sağlayıcının etkin model listesinde bulunamadı.")
    vision_base = config.ollama_base_url if config.vision_provider == LLMProviderType.OLLAMA else config.vllm_base_url
    vision_models = await list_provider_models(config.vision_provider, vision_base)
    if config.vision_model not in {item.id for item in vision_models}:
        raise ModelProviderError("Seçili görsel modeli sağlayıcının etkin model listesinde bulunamadı.")
    await complete(config, [{"role": "user", "content": "Yalnızca TAMAM yaz."}], temperature=0, max_tokens=16)
    # Model listesinde yer almak multimodal desteği kanıtlamaz. Çok küçük sabit
    # PNG, isteğin kabul edildiğini doğrular; kullanıcı verisi gönderilmez.
    probe_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Jj0YAAAAASUVORK5CYII="
    )
    try:
        vision_result = await complete(
            config,
            [{"role": "user", "content": "Görsel isteğini aldığını {\"visible\": true} biçiminde JSON ile doğrula."}],
            model=config.vision_model,
            temperature=0,
            max_tokens=32,
            json_mode=True,
            image=probe_png,
        )
        if not isinstance(json.loads(vision_result.removeprefix("```json").removesuffix("```").strip()), dict):
            raise ValueError("JSON nesnesi bekleniyordu")
    except (ModelProviderError, ValueError, json.JSONDecodeError) as error:
        raise ModelProviderError("Seçili görsel model test görselini işleyemedi; vision destekli bir model seçin.") from error
