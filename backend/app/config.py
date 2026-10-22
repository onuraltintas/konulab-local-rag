from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Konu Tabanlı Öğrenme API"
    database_url: str = "sqlite:///./data/learning-platform.db"
    storage_path: Path = Path("./data/sources")
    allowed_origins: str = "http://localhost:4200,http://127.0.0.1:4200"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    # Önceki dense koleksiyonuna dokunmadan temiz hibrit indeks oluşturulur.
    qdrant_collection: str = "lesson_sections_turkish_e5_large_hybrid_v1"
    embedding_model: str = "ytu-ce-cosmos/turkish-e5-large"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 16
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.8:27b"
    # Şüpheli tablolar otomatik, diğer görseller kullanıcı isteğiyle incelenir.
    # Metin/RAG ve sohbet görevleri varsayılan Qwen modelinde kalır.
    # Yerelde kurulu Qwen sürümü Ollama'nın vision yeteneğini bildirir;
    # gemma4:31b metin odaklı kurulumlarda bu yeteneği bildirmeyebilir.
    vision_model: str = "qwen3.8:27b"
    ollama_context_length: int = 32_768
    ollama_think: bool = False
    document_analysis_max_tokens: int = 3_500

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
