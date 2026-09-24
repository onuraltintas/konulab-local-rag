"""KonuLab'ın kaynaklı yanıt kalitesini tekrarlanabilir bir soru setinde ölçer.

Çalıştırma (GPU API konteyneri içinde):
    python scripts/evaluate_answer_quality.py
    python scripts/evaluate_answer_quality.py --limit 1
    python scripts/evaluate_answer_quality.py --skip-judge

Rapor üç ayrı sinyal üretir:
1. retrieval_hit: beklenen ders ifadesi en az bir seçilen kaynakta mı?
2. citation_integrity: yanıttaki [Kx] atıfları sunulan kaynaklara karşılık geliyor mu?
3. judge: yerel modelin, sadece seçilen kaynaklara dayanarak verdiği açıklamalı kalite puanı.

`judge` puanı insan denetiminin yerine geçmez; regresyonları ve insan incelemesi
gerektiren cevapları yakalamak için kullanılır.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from statistics import mean

import httpx

from app.llm import OllamaUnavailableError, get_ollama_client
from app.quality import inspect_citations
from app.schemas import SearchResult


BENCHMARK_PATH = Path(__file__).parents[1] / "evals" / "answer_quality_benchmark.json"
SOURCE_LIMIT = 6


def expected_in_sources(sources: list[SearchResult], expected_any: list[str]) -> bool:
    return any(
        expected.casefold() in source.content.casefold()
        for source in sources
        for expected in expected_any
    )


async def retrieve_sources(client: httpx.AsyncClient, api_url: str, query: str, topic_id: int) -> list[SearchResult]:
    """Üretimdeki /api/search yolunu kullanır; ikinci bir embedding modeli yüklemez."""
    response = await client.post(
        f"{api_url}/api/search",
        json={"query": query, "topic_id": topic_id, "limit": SOURCE_LIMIT},
    )
    response.raise_for_status()
    return [SearchResult.model_validate(item) for item in response.json()]


async def evaluate_case(
    client: httpx.AsyncClient, case: dict[str, object], api_url: str, topic_ids: dict[str, int], skip_judge: bool
) -> dict[str, object]:
    topic_id = topic_ids.get(str(case["topic"]))
    if topic_id is None:
        raise RuntimeError(f"Benchmark konusu bulunamadı: {case['topic']}")

    sources = await retrieve_sources(client, api_url, str(case["query"]), topic_id)
    if not sources:
        raise RuntimeError(f"Kaynak bulunamadı: {case['id']}")

    answer = await get_ollama_client().answer(str(case["query"]), sources)
    citations = inspect_citations(answer, len(sources))
    result: dict[str, object] = {
        "id": case["id"],
        "topic": case["topic"],
        "query": case["query"],
        "retrieval_hit": expected_in_sources(sources, list(case["expected_any"])),
        "citation_integrity": citations.is_valid,
        "citation_labels": citations.labels,
        "invalid_citation_labels": citations.invalid_labels,
        "answer": answer,
        "sources": [
            {
                "section_id": str(source.section_id),
                "heading": source.heading_path,
                "source_name": source.source_name,
                "line_start": source.line_start,
                "line_end": source.line_end,
            }
            for source in sources
        ],
    }
    if not skip_judge:
        result["judge"] = await get_ollama_client().evaluate_answer(str(case["query"]), sources, answer)
    return result


async def calibrate_judge(
    client: httpx.AsyncClient, api_url: str, topic_ids: dict[str, int], case: dict[str, object]
) -> dict[str, object]:
    """Kasıtlı yanlış yanıtla değerlendiricinin ayırt etme gücünü sınar."""
    topic_id = topic_ids.get(str(case["topic"]))
    if topic_id is None:
        raise RuntimeError(f"Kalibrasyon konusu bulunamadı: {case['topic']}")
    sources = await retrieve_sources(client, api_url, str(case["query"]), topic_id)
    deliberately_wrong = (
        "Düzenli ifadeler yalnızca görsel dosyaları düzenlemek için kullanılır; metin arama veya "
        "ayrıştırma ile ilişkileri yoktur. Bu nedenle metin ön işleme sürecinde kullanılmazlar [K1]."
    )
    judgment = await get_ollama_client().evaluate_answer(str(case["query"]), sources, deliberately_wrong)
    detected = judgment["verdict"] != "pass" or int(judgment["groundedness"]) <= 1
    return {"detected": detected, "judge": judgment}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Koşulacak ilk benchmark soru sayısı")
    parser.add_argument("--skip-judge", action="store_true", help="Yalnızca retrieval ve atıf kontrollerini çalıştır")
    parser.add_argument("--judge-calibration-only", action="store_true", help="Yalnızca kasıtlı yanlış yanıt denetimini çalıştır")
    parser.add_argument("--output", type=Path, default=None, help="JSON raporunun yazılacağı yol")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000", help="Çalışan KonuLab API adresi")
    args = parser.parse_args()

    cases = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    if args.limit is not None:
        cases = cases[: max(args.limit, 0)]
    if not cases:
        raise RuntimeError("Çalıştırılacak benchmark sorusu yok.")

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=300, write=30, pool=30)) as client:
        topics_response = await client.get(f"{args.api_url}/api/topics")
        topics_response.raise_for_status()
        topic_ids = {str(topic["title"]): int(topic["id"]) for topic in topics_response.json()}
        if args.judge_calibration_only:
            print("Kasıtlı yanlış yanıtla değerlendirici kalibrasyonu yapılıyor…", flush=True)
            calibration = await calibrate_judge(client, args.api_url, topic_ids, cases[0])
            print(json.dumps(calibration, ensure_ascii=False, indent=2))
            if not calibration["detected"]:
                raise RuntimeError("Yerel değerlendirici kasıtlı yanlış yanıtı yeterince ayırt edemedi.")
            return

        results: list[dict[str, object]] = []
        for index, case in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case['id']} değerlendiriliyor…", flush=True)
            results.append(await evaluate_case(client, case, args.api_url, topic_ids, args.skip_judge))

    report: dict[str, object] = {
        "case_count": len(results),
        "retrieval_hit_rate": mean(bool(result["retrieval_hit"]) for result in results),
        "citation_integrity_rate": mean(bool(result["citation_integrity"]) for result in results),
        "results": results,
    }
    judgments = [result["judge"] for result in results if "judge" in result]
    if judgments:
        report["judge_averages"] = {
            metric: mean(int(judgment[metric]) for judgment in judgments)
            for metric in ("groundedness", "coverage", "teaching", "citation_quality")
        }
        report["review_cases"] = [
            result["id"] for result in results if result.get("judge", {}).get("verdict") != "pass"
        ]

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print("\nÖZET")
    print(f"Retrieval hit: {report['retrieval_hit_rate']:.1%}")
    print(f"Geçerli atıf: {report['citation_integrity_rate']:.1%}")
    if judgments:
        averages = report["judge_averages"]
        print("Yerel değerlendirici ortalaması (0–2): " + ", ".join(f"{key}={value:.2f}" for key, value in averages.items()))
        review_cases = ", ".join(report["review_cases"]) or "yok"
        print("İnsan incelemesi gerekenler: " + review_cases)
        print("Not: Yerel değerlendirici puanları negatif kontrol başarılıysa yorumlanmalıdır.")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Rapor yazıldı: {args.output}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except OllamaUnavailableError as error:
        raise SystemExit(f"Kalite değerlendirmesi tamamlanamadı: {error}") from error
