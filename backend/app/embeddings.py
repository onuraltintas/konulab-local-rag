"""Yerel SentenceTransformers embedding sağlayıcısı.

Model ilk indeksleme veya arama anında belleğe alınır. Böylece API'nin konu ve
doküman yönetimi ekranları, model henüz indirilmemişken de açılabilir.
"""

from dataclasses import dataclass
from functools import lru_cache

from app.config import get_settings


class EmbeddingUnavailableError(RuntimeError):
    """Yerel modelin kurulamadığını veya yüklenemediğini belirtir."""


@dataclass(frozen=True)
class SparseVectorData:
    """Qdrant'ın sparse vektör biçiminden bağımsız, kolay test edilebilir veri."""

    indices: list[int]
    values: list[float]


class TurkishE5Embedder:
    def __init__(self) -> None:
        self.settings = get_settings()
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise EmbeddingUnavailableError(
                "Embedding bağımlılığı kurulu değil. CPU geliştirme için "
                "`uv sync --extra embeddings-cpu`, GX10 GPU çalıştırması için Docker/NGC profilini kullanın."
            ) from error

        try:
            self.model = SentenceTransformer(
                self.settings.embedding_model,
                device=self.settings.embedding_device,
            )
        except Exception as error:  # Model indirme/cihaz hataları kullanıcıya API hatası olarak döner.
            raise EmbeddingUnavailableError(
                f"Embedding modeli yüklenemedi: {self.settings.embedding_model}"
            ) from error

    def embed_passages(self, passages: list[str]) -> list[list[float]]:
        return self._encode([f"passage: {passage}" for passage in passages])

    @property
    def tokenizer(self):
        return self.model.tokenizer

    def passage_token_budget(self) -> int:
        """Model penceresinden E5 ön eki, özel tokenlar ve başlık birleşimi için pay çıkarır."""
        max_sequence_length = int(self.model.max_seq_length)
        prefix_tokens = len(self.tokenizer("passage: ", add_special_tokens=False).get("input_ids", []))
        # Başlık + içerik tek çağrıda tokenize edilince sınırda ilave ayırıcı
        # tokenları oluşabilir. 14 tokenlık pay sessiz model kırpmasını engeller.
        return max(32, max_sequence_length - prefix_tokens - 16)

    def embed_query(self, query: str) -> list[float]:
        return self._encode([f"query: {query}"])[0]

    def _encode(self, inputs: list[str]) -> list[list[float]]:
        vectors = self.model.encode(
            inputs,
            batch_size=self.settings.embedding_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [vector.tolist() for vector in vectors]


class TurkishBm25Embedder:
    """Türkçe ders notlarında terim/kısaltma eşleşmesini güçlendiren yerel BM25."""

    model_name = "Qdrant/bm25"

    def __init__(self) -> None:
        try:
            from fastembed import SparseTextEmbedding

            self.model = SparseTextEmbedding(model_name=self.model_name)
        except Exception as error:
            raise EmbeddingUnavailableError("BM25 sparse embedding modeli yüklenemedi.") from error

    def embed_passages(self, passages: list[str]) -> list[SparseVectorData]:
        return self._encode(passages)

    def embed_query(self, query: str) -> SparseVectorData:
        return self._encode([query])[0]

    def _encode(self, texts: list[str]) -> list[SparseVectorData]:
        vectors = list(self.model.embed(texts))
        return [
            SparseVectorData(
                indices=[int(index) for index in vector.indices],
                values=[float(value) for value in vector.values],
            )
            for vector in vectors
        ]


@lru_cache
def get_embedder() -> TurkishE5Embedder:
    return TurkishE5Embedder()


@lru_cache
def get_sparse_embedder() -> TurkishBm25Embedder:
    return TurkishBm25Embedder()
