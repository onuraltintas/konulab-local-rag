"""Qdrant bağlantısı ve bölüm vektörleri için tek erişim noktası."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.config import get_settings


class VectorStoreUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class SectionVector:
    section_id: UUID
    document_id: UUID
    logical_key: str
    document_version: int
    source_name: str
    heading_path: str
    content_type: str
    line_start: int
    line_end: int
    topic_ids: list[int]
    vector: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]


@dataclass(frozen=True)
class SearchHit:
    section_id: UUID
    document_id: UUID
    score: float
    payload: dict[str, Any]


class QdrantVectorStore:
    # Ders notlarında anlamsal yakınlık ana sinyaldir. BM25 yalnızca teknik
    # terim/kısaltma desteği verir; aksi hâlde kod içindeki rastgele eşleşmeler
    # öğretici açıklamaların önüne geçebilir.
    DENSE_RRF_WEIGHT = 0.72
    SPARSE_RRF_WEIGHT = 0.28
    RRF_K = 60

    def __init__(self) -> None:
        settings = get_settings()
        self.collection_name = settings.qdrant_collection
        self.client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)

    def ensure_collection(self, vector_size: int) -> None:
        try:
            if not self.client.collection_exists(self.collection_name):
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config={"dense": qmodels.VectorParams(size=vector_size, distance=qmodels.Distance.COSINE)},
                    sparse_vectors_config={"bm25": qmodels.SparseVectorParams()},
                )
                self.client.create_payload_index(
                    self.collection_name,
                    "document_id",
                    qmodels.PayloadSchemaType.KEYWORD,
                )
                self.client.create_payload_index(
                    self.collection_name,
                    "topic_ids",
                    qmodels.PayloadSchemaType.INTEGER,
                )
        except Exception as error:
            raise VectorStoreUnavailableError("Qdrant'a bağlanılamadı veya koleksiyon hazırlanamadı.") from error

    def upsert_sections(self, vectors: list[SectionVector]) -> None:
        if not vectors:
            return
        self.ensure_collection(len(vectors[0].vector))
        points = [
            qmodels.PointStruct(
                id=str(item.section_id),
                vector={
                    "dense": item.vector,
                    "bm25": qmodels.SparseVector(indices=item.sparse_indices, values=item.sparse_values),
                },
                payload={
                    "section_id": str(item.section_id),
                    "document_id": str(item.document_id),
                    "logical_key": item.logical_key,
                    "document_version": item.document_version,
                    "source_name": item.source_name,
                    "heading_path": item.heading_path,
                    "content_type": item.content_type,
                    "line_start": item.line_start,
                    "line_end": item.line_end,
                    "topic_ids": item.topic_ids,
                },
            )
            for item in vectors
        ]
        try:
            self.client.upsert(collection_name=self.collection_name, points=points, wait=True)
        except Exception as error:
            raise VectorStoreUnavailableError("Bölümler Qdrant'a yazılamadı.") from error

    def delete_document(self, document_id: UUID) -> None:
        """Dokümana ait tüm hibrit vektörleri kaldırır."""
        try:
            selector = qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[qmodels.FieldCondition(key="document_id", match=qmodels.MatchValue(value=str(document_id)))]
                )
            )
            if self.client.collection_exists(self.collection_name):
                self.client.delete(collection_name=self.collection_name, points_selector=selector, wait=True)
        except Exception as error:
            raise VectorStoreUnavailableError("Eski doküman vektörleri Qdrant'tan kaldırılamadı.") from error

    def search(
        self,
        query_vector: list[float],
        sparse_indices: list[int],
        sparse_values: list[float],
        limit: int,
        topic_id: int | None = None,
    ) -> list[SearchHit]:
        query_filter = None
        if topic_id is not None:
            query_filter = qmodels.Filter(
                must=[qmodels.FieldCondition(key="topic_ids", match=qmodels.MatchValue(value=topic_id))]
            )
        try:
            candidate_limit = limit * 4
            dense_response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                using="dense",
                query_filter=query_filter,
                limit=candidate_limit,
                with_payload=True,
            )
            sparse_response = self.client.query_points(
                collection_name=self.collection_name,
                query=qmodels.SparseVector(indices=sparse_indices, values=sparse_values),
                using="bm25",
                query_filter=query_filter,
                limit=candidate_limit,
                with_payload=True,
            )
        except Exception as error:
            raise VectorStoreUnavailableError("Qdrant üzerinde arama yapılamadı.") from error

        # Ağırlıklı reciprocal-rank fusion: dense arama öğretici bağlamı
        # korurken, BM25 tam teknik terim eşleşmelerini kontrollü biçimde öne
        # çıkarır. Ham iki skor ölçeği karşılaştırılabilir olmadığından rank
        # temelli birleşim kullanılır.
        candidates: dict[str, tuple[object, float]] = {}
        for weight, points in (
            (self.DENSE_RRF_WEIGHT, dense_response.points),
            (self.SPARSE_RRF_WEIGHT, sparse_response.points),
        ):
            for rank, point in enumerate(points, start=1):
                key = str(point.id)
                previous = candidates.get(key)
                score = (previous[1] if previous else 0.0) + weight / (self.RRF_K + rank)
                candidates[key] = (point, score)
        ranked = sorted(candidates.values(), key=lambda item: item[1], reverse=True)[:limit]
        return [
            SearchHit(
                section_id=UUID(str(point.id)),
                document_id=UUID(str(point.payload["document_id"])),
                score=score,
                payload=dict(point.payload),
            )
            for point, score in ranked
        ]


@lru_cache
def get_vector_store() -> QdrantVectorStore:
    return QdrantVectorStore()
