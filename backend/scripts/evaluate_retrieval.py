"""KonuLab dense ve hibrit retrieval kalitesini aynı ders sorularında ölçer.

Çalıştırma (GPU API konteyneri içinde):
    python scripts/evaluate_retrieval.py

Her benchmark kaydında, doğru kabul edilen kaynak parçasında geçmesi gereken en
az bir ifade bulunur. Bu, kaynak değiştiğinde UUID'lere bağımlı kalmadan ölçüm
yapılmasını sağlar.
"""

from __future__ import annotations

import json
from math import log2
from pathlib import Path
import sys
from uuid import UUID

from qdrant_client.http import models as qmodels
from sqlalchemy import select

from app.db import SessionLocal
from app.embeddings import get_embedder, get_sparse_embedder
from app.models import DocumentSection, Topic, TopicSource
from app.vector_store import get_vector_store


BENCHMARK_PATH = Path(__file__).parents[1] / "evals" / "tokenization_retrieval_benchmark.json"
K = 3


def is_relevant(content: str, expected_any: list[str]) -> bool:
    normalized = content.casefold()
    return any(expected.casefold() in normalized for expected in expected_any)


def metric_at_k(
    results: list[DocumentSection], expected_any: list[str], relevant_total: int, k: int = K
) -> tuple[float, float, float]:
    relevance = [is_relevant(section.content, expected_any) for section in results[:k]]
    recall = float(any(relevance))
    reciprocal_rank = next((1 / rank for rank, hit in enumerate(relevance, start=1) if hit), 0.0)
    dcg = sum(1 / log2(rank + 1) for rank, hit in enumerate(relevance, start=1) if hit)
    ideal_dcg = sum(1 / log2(rank + 1) for rank in range(1, min(relevant_total, k) + 1))
    ndcg = dcg / ideal_dcg if ideal_dcg else 0.0
    return recall, reciprocal_rank, ndcg


def dense_search(query: str, topic_id: int, limit: int) -> list[UUID]:
    store = get_vector_store()
    query_vector = get_embedder().embed_query(query)
    filter_ = qmodels.Filter(
        must=[qmodels.FieldCondition(key="topic_ids", match=qmodels.MatchValue(value=topic_id))]
    )
    response = store.client.query_points(
        collection_name=store.collection_name,
        query=query_vector,
        using="dense",
        query_filter=filter_,
        limit=limit,
        with_payload=False,
    )
    return [UUID(str(point.id)) for point in response.points]


def hybrid_search(query: str, topic_id: int, limit: int) -> list[UUID]:
    dense_query = get_embedder().embed_query(query)
    sparse_query = get_sparse_embedder().embed_query(query)
    return [
        result.section_id
        for result in get_vector_store().search(
            dense_query,
            sparse_query.indices,
            sparse_query.values,
            limit=limit,
            topic_id=topic_id,
        )
    ]


def evaluate() -> None:
    cases = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    database = SessionLocal()
    totals = {"dense": [0.0, 0.0, 0.0], "hybrid": [0.0, 0.0, 0.0]}
    try:
        for case in cases:
            topic = database.scalar(select(Topic).where(Topic.title == case["topic"]))
            if topic is None:
                raise RuntimeError(f"Benchmark konusu bulunamadı: {case['topic']}")
            result_ids = {
                "dense": dense_search(case["query"], topic.id, K),
                "hybrid": hybrid_search(case["query"], topic.id, K),
            }
            topic_sections = list(
                database.scalars(
                    select(DocumentSection)
                    .join(TopicSource, TopicSource.section_id == DocumentSection.id)
                    .where(TopicSource.topic_id == topic.id)
                )
            )
            relevant_total = sum(is_relevant(section.content, case["expected_any"]) for section in topic_sections)
            if relevant_total == 0:
                raise RuntimeError(f"Benchmark hedef ifadesi kaynakta bulunamadı: {case['query']}")
            scores: dict[str, tuple[float, float, float]] = {}
            for mode, ids in result_ids.items():
                sections = [database.get(DocumentSection, section_id) for section_id in ids]
                scores[mode] = metric_at_k(
                    [section for section in sections if section], case["expected_any"], relevant_total
                )
                totals[mode] = [total + score for total, score in zip(totals[mode], scores[mode], strict=True)]
            print(
                f"{case['query']}\n"
                f"  dense  recall@{K}={scores['dense'][0]:.0f} mrr={scores['dense'][1]:.3f} ndcg={scores['dense'][2]:.3f}\n"
                f"  hybrid recall@{K}={scores['hybrid'][0]:.0f} mrr={scores['hybrid'][1]:.3f} ndcg={scores['hybrid'][2]:.3f}"
            )
    finally:
        database.close()

    print("\nÖZET")
    for mode, sums in totals.items():
        recall, mrr, ndcg = (value / len(cases) for value in sums)
        print(f"{mode}: Recall@{K}={recall:.3%}, MRR={mrr:.3%}, nDCG@{K}={ndcg:.3%}")


if __name__ == "__main__":
    if "--validate" in sys.argv:
        cases = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
        database = SessionLocal()
        try:
            missing: list[str] = []
            for case in cases:
                topic = database.scalar(select(Topic).where(Topic.title == case["topic"]))
                if topic is None:
                    missing.append(f"Konu yok: {case['topic']}")
                    continue
                sections = database.scalars(
                    select(DocumentSection)
                    .join(TopicSource, TopicSource.section_id == DocumentSection.id)
                    .where(TopicSource.topic_id == topic.id)
                )
                if not any(is_relevant(section.content, case["expected_any"]) for section in sections):
                    missing.append(f"Hedef yok: {case['query']}")
            if missing:
                raise RuntimeError("\n".join(missing))
            print(f"Benchmark doğrulandı: {len(cases)} soru.")
        finally:
            database.close()
    else:
        evaluate()
