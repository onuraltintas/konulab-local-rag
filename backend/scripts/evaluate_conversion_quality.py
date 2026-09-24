"""Docling dönüşüm kalitesini kullanıcı verisini indekslemeden ölçer.

Çalıştırma (proje kökünden):
    uv run --project backend python backend/scripts/evaluate_conversion_quality.py

Manifest yalnızca yerel kaynakların göreli yollarını ve insan tarafından gözden
geçirilmiş alt eşikleri içerir; büyük test dosyaları depoya kopyalanmaz. Yeni
PDF/DOCX/PPTX/XLSX örnekleri önce insan incelemesiyle manifest'e eklenmelidir.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from app.conversion import convert_to_markdown


PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_MANIFEST = Path(__file__).parents[1] / "evals" / "conversion_quality_benchmark.json"


def markdown_heading_count(markdown: str) -> int:
    return len(re.findall(r"(?m)^#{1,6}\s+\S", markdown))


def evaluate_case(case: dict[str, Any], source_root: Path) -> dict[str, Any]:
    source_path = source_root / str(case["source"])
    if not source_path.is_file():
        raise FileNotFoundError(f"Benchmark kaynağı bulunamadı: {source_path}")
    converted = convert_to_markdown(source_path)
    markdown = converted.markdown
    normalized = markdown.casefold()
    expected = dict(case.get("expectations", {}))
    metrics = {
        "markdown_chars": len(markdown),
        "headings": markdown_heading_count(markdown),
        "page_count": converted.page_count,
        "table_count": converted.table_count,
        "image_count": converted.image_count,
        "visual_assets": len(converted.assets),
        "ocr_used": converted.ocr_used,
    }
    failures: list[str] = []
    numeric_requirements = {
        "min_markdown_chars": "markdown_chars",
        "min_heading_count": "headings",
        "min_page_count": "page_count",
        "min_table_count": "table_count",
        "min_image_count": "image_count",
        "min_visual_assets": "visual_assets",
    }
    for expectation_name, metric_name in numeric_requirements.items():
        if expectation_name in expected and metrics[metric_name] < int(expected[expectation_name]):
            failures.append(f"{metric_name}={metrics[metric_name]} < {expected[expectation_name]}")
    required_any = [str(item).casefold() for item in expected.get("required_any", [])]
    if required_any and not any(term in normalized for term in required_any):
        failures.append("beklenen terimlerden hiçbiri Markdown'da yok")
    if expected.get("ocr_used") is not None and metrics["ocr_used"] is not bool(expected["ocr_used"]):
        failures.append(f"ocr_used={metrics['ocr_used']} beklenen={bool(expected['ocr_used'])}")
    return {
        "name": case["name"],
        "source": str(case["source"]),
        "converter": converted.converter,
        "converter_version": converted.converter_version,
        "metrics": metrics,
        "passed": not failures,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="KonuLab belge dönüşüm kalite değerlendirmesi")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--report", type=Path, help="İsteğe bağlı JSON rapor dosyası")
    args = parser.parse_args()
    cases = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise RuntimeError("Benchmark manifest'i boş olamaz.")
    results = [evaluate_case(case, args.source_root) for case in cases]
    report = {"case_count": len(results), "passed": all(result["passed"] for result in results), "results": results}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"Dönüşüm kalite değerlendirmesi çalıştırılamadı: {error}", file=sys.stderr)
        raise SystemExit(2) from error
