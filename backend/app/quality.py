"""Kalite değerlendirmesinde modelden bağımsız, açıklanabilir kontroller."""

from dataclasses import dataclass
import re


_CITATION_PATTERN = re.compile(r"\[K(\d+)\]")


@dataclass(frozen=True)
class CitationInspection:
    labels: list[int]
    invalid_labels: list[int]

    @property
    def is_valid(self) -> bool:
        return bool(self.labels) and not self.invalid_labels


def inspect_citations(answer: str, source_count: int) -> CitationInspection:
    """[K1] biçimindeki tüm atıfların, sunulan kaynaklara karşılığını kontrol eder."""
    labels = sorted({int(value) for value in _CITATION_PATTERN.findall(answer)})
    invalid_labels = [label for label in labels if label < 1 or label > source_count]
    return CitationInspection(labels=labels, invalid_labels=invalid_labels)
