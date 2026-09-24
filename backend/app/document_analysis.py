"""Doküman bölümlerinden kullanıcı onayına sunulacak konu taslağını üretir."""

import json
import re
from difflib import SequenceMatcher
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from app.config import get_settings
from app.prompts import JSON_ONLY_SYSTEM_PROMPT, lesson_title_prompt, topic_analysis_prompt
from app.runtime_models import ModelProviderError, complete, current_runtime_model_config


CONTENT_TYPES = {"explanation", "example", "code", "exercise"}
EXAMPLE_MARKERS = re.compile(r"\b(örnek|uygulama|example|demo)\b", re.IGNORECASE)
EXERCISE_MARKERS = re.compile(r"\b(alıştırma|egzersiz|soru|exercise|ödev)\b", re.IGNORECASE)
DATE_OR_LESSON_TITLE = re.compile(
    r"^(?:ders|lesson)\s*\d+\s*(?:[—–-]\s*)?(?:[-\d./\s]|pazartesi|salı|çarşamba|perşembe|cuma|cumartesi|pazar)+$",
    re.IGNORECASE,
)
MAX_ANALYSIS_BATCH_CHARS = 16_000
TITLE_STOPWORDS = {"ve", "ile", "için", "ile", "bir", "temel", "temelleri"}
TURKISH_TITLE_SUFFIXES = ("ları", "leri", "lar", "ler", "sı", "si", "su", "sü")


class DocumentAnalysisUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class SectionAnalysisInput:
    id: UUID
    heading_path: str
    content: str
    content_type: str


@dataclass(frozen=True)
class TopicSuggestion:
    title: str
    description: str | None
    section_ids: list[UUID]
    section_types: dict[UUID, str]


@dataclass(frozen=True)
class AnalysisUnit:
    """Aynı ders başlığı altında kalan vektör parçalarının model için tek özeti."""

    representative: SectionAnalysisInput
    members: list[SectionAnalysisInput]


def classify_content_type(section: SectionAnalysisInput) -> str:
    text = f"{section.heading_path}\n{section.content[:600]}"
    if EXERCISE_MARKERS.search(text):
        return "exercise"
    if EXAMPLE_MARKERS.search(text):
        return "example"
    if section.content_type == "code":
        return "code"
    return "explanation"


class DocumentAnalyzer:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def suggest_topics(self, sections: list[SectionAnalysisInput]) -> list[TopicSuggestion]:
        if not sections:
            return []
        units = self._collapse_into_units(sections)
        suggestions: list[TopicSuggestion] = []
        for batch in self._batches([unit.representative for unit in units]):
            suggestions.extend(await self._suggest_batch(batch))
        exact_merged = self._merge_matching_titles(self._expand_unit_assignments(suggestions, units))
        return self._merge_near_duplicate_topics(exact_merged)

    async def suggest_lesson_title(self, topics: list[TopicSuggestion]) -> str | None:
        """Konu taslağından dosya isminden bağımsız, insan okunur ders adı üretir.

        Başlık yalnızca bir öneridir: kullanıcı onay ekranında değiştirmeden önce
        hiçbir kalıcı ders kaydına yazılmaz. Konu çıkarımı zaten başarılı olsa bile
        bu küçük ek istek hata verebilir; o durumda konu taslağı bozulmaz.
        """
        if not topics:
            return None
        prompt = lesson_title_prompt(topics)
        try:
            content = await complete(current_runtime_model_config(), [
                {"role": "system", "content": JSON_ONLY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ], temperature=0.1, max_tokens=100, json_mode=True)
            title = str(json.loads(content.strip().removeprefix("```json").removesuffix("```").strip()).get("lesson_title", "")).strip()
            if (
                len(title) < 2
                or len(title) > 255
                or DATE_OR_LESSON_TITLE.fullmatch(title)
                or title.casefold() in {"başlangıç", "genel", "ders"}
            ):
                return None
            return title
        except (ModelProviderError, ValueError, json.JSONDecodeError):
            return None

    async def _suggest_batch(self, sections: list[SectionAnalysisInput]) -> list[TopicSuggestion]:
        prompt = topic_analysis_prompt(sections)
        try:
            content = await complete(current_runtime_model_config(), [
                {"role": "system", "content": JSON_ONLY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ], temperature=0.1, max_tokens=min(self.settings.document_analysis_max_tokens, 1_200), json_mode=True)
            suggestions = self._parse(content, sections)
            if not suggestions:
                raise ValueError("Model geçerli bir konu önerisi döndürmedi.")
            return suggestions
        except (ModelProviderError, ValueError, json.JSONDecodeError) as error:
            # Tarih/ders etiketi gibi kalitesiz başlıklarla devam etmek yerine kullanıcıya
            # yeniden denemesi gereken gerçek nedeni bildir.
            reason = f"{type(error).__name__}: {str(error)[:240]}"
            raise DocumentAnalysisUnavailableError(
                f"Yerel modelden anlamlı bir konu haritası alınamadı ({reason})."
            ) from error

    @staticmethod
    def _batches(sections: list[SectionAnalysisInput]) -> list[list[SectionAnalysisInput]]:
        """İstek bağlamını model penceresinin altında tutacak, sıralı analiz partileri üretir."""
        batches: list[list[SectionAnalysisInput]] = []
        batch: list[SectionAnalysisInput] = []
        batch_chars = 0
        for section in sections:
            section_chars = len(section.heading_path) + min(len(section.content), 480) + 48
            if batch and batch_chars + section_chars > MAX_ANALYSIS_BATCH_CHARS:
                batches.append(batch)
                batch = []
                batch_chars = 0
            batch.append(section)
            batch_chars += section_chars
        if batch:
            batches.append(batch)
        return batches

    @staticmethod
    def _collapse_into_units(sections: list[SectionAnalysisInput]) -> list[AnalysisUnit]:
        """Embedding parçalarını ders/Markdown başlığı bazında birleştirir.

        LLM, aynı başlığa ait beş küçük vektör parçasını beş farklı konu sanmamalı.
        Her ünite ilk 1.200 karakterlik farklı bağlamı taşır; onay aşamasında ise
        o ünitenin bütün özgün parçaları ilgili konuya bağlanır.
        """
        grouped: dict[str, list[SectionAnalysisInput]] = {}
        for section in sections:
            grouped.setdefault(section.heading_path, []).append(section)
        units: list[AnalysisUnit] = []
        for members in grouped.values():
            excerpts: list[str] = []
            remaining = 1_200
            for member in members:
                if remaining <= 0:
                    break
                excerpt = member.content[:remaining]
                excerpts.append(excerpt)
                remaining -= len(excerpt)
            first = members[0]
            units.append(
                AnalysisUnit(
                    representative=SectionAnalysisInput(
                        id=first.id,
                        heading_path=first.heading_path,
                        content="\n\n".join(excerpts),
                        content_type=first.content_type,
                    ),
                    members=members,
                )
            )
        return units

    @staticmethod
    def _expand_unit_assignments(
        suggestions: list[TopicSuggestion], units: list[AnalysisUnit]
    ) -> list[TopicSuggestion]:
        members_by_representative = {unit.representative.id: unit.members for unit in units}
        expanded: list[TopicSuggestion] = []
        for suggestion in suggestions:
            ids: list[UUID] = []
            types: dict[UUID, str] = {}
            for representative_id in suggestion.section_ids:
                for section in members_by_representative.get(representative_id, []):
                    ids.append(section.id)
                    types[section.id] = classify_content_type(section)
            if ids:
                expanded.append(TopicSuggestion(suggestion.title, suggestion.description, ids, types))
        return expanded

    @staticmethod
    def _merge_matching_titles(suggestions: list[TopicSuggestion]) -> list[TopicSuggestion]:
        """Farklı partilerde tekrar eden aynı kavramı tek kullanıcı konusu olarak sunar."""
        merged: dict[str, TopicSuggestion] = {}
        for suggestion in suggestions:
            key = re.sub(r"\s+", " ", suggestion.title).casefold()
            current = merged.get(key)
            if current is None:
                merged[key] = TopicSuggestion(
                    title=suggestion.title,
                    description=suggestion.description,
                    section_ids=list(suggestion.section_ids),
                    section_types=dict(suggestion.section_types),
                )
                continue
            current.section_ids.extend(section_id for section_id in suggestion.section_ids if section_id not in current.section_ids)
            current.section_types.update(suggestion.section_types)
        return list(merged.values())

    @staticmethod
    def _merge_near_duplicate_topics(suggestions: list[TopicSuggestion]) -> list[TopicSuggestion]:
        """Aynı kavramın küçük yazım/çekim farklarıyla yinelenmesini güvenle birleştirir.

        Sadece yüksek dizgesel benzerliği olan VE en az iki anlamlı ortak sözcük
        taşıyan başlıklar birleşir. Böylece "Metin Normalizasyonu" ile "Karakter
        Normalizasyonu" gibi farklı öğrenme kapsamları yanlışlıkla tek konu olmaz.
        """
        groups: list[list[TopicSuggestion]] = []
        for suggestion in suggestions:
            matching_group = next(
                (group for group in groups if DocumentAnalyzer._titles_are_near_duplicates(suggestion.title, group[0].title)),
                None,
            )
            if matching_group is None:
                groups.append([suggestion])
            else:
                matching_group.append(suggestion)

        merged: list[TopicSuggestion] = []
        for group in groups:
            canonical = max(group, key=lambda item: (len(item.description or ""), len(item.title)))
            section_ids: list[UUID] = []
            section_types: dict[UUID, str] = {}
            for item in group:
                for section_id in item.section_ids:
                    if section_id not in section_types:
                        section_ids.append(section_id)
                    section_types[section_id] = item.section_types[section_id]
            merged.append(TopicSuggestion(canonical.title, canonical.description, section_ids, section_types))
        return merged

    @staticmethod
    def _titles_are_near_duplicates(first: str, second: str) -> bool:
        first_terms = DocumentAnalyzer._normalized_title_terms(first)
        second_terms = DocumentAnalyzer._normalized_title_terms(second)
        if len(first_terms.intersection(second_terms)) < 2:
            return False
        first_key = " ".join(sorted(first_terms))
        second_key = " ".join(sorted(second_terms))
        return SequenceMatcher(None, first_key, second_key).ratio() >= 0.88

    @staticmethod
    def _normalized_title_terms(title: str) -> set[str]:
        normalized = str.maketrans("çğıöşü", "cgiosu")
        terms: set[str] = set()
        for word in re.findall(r"[\wçğıöşü]+", title.casefold()):
            word = word.translate(normalized)
            for suffix in TURKISH_TITLE_SUFFIXES:
                if len(word) > len(suffix) + 2 and word.endswith(suffix):
                    word = word[: -len(suffix)]
                    break
            if word and word not in TITLE_STOPWORDS:
                terms.add(word)
        return terms

    def _parse(self, content: str, sections: list[SectionAnalysisInput]) -> list[TopicSuggestion]:
        payload = json.loads(content.strip().removeprefix("```json").removesuffix("```").strip())
        known_sections = {number: section for number, section in enumerate(sections, start=1)}
        used: set[int] = set()
        suggestions: list[TopicSuggestion] = []
        for item in payload.get("topics", []):
            title = str(item.get("title", "")).strip()[:255]
            if not title or DATE_OR_LESSON_TITLE.fullmatch(title) or title.casefold() in {"başlangıç", "genel"}:
                continue
            ids: list[UUID] = []
            types: dict[UUID, str] = {}
            for raw_number in item.get("sections", []):
                try:
                    section_number = int(raw_number)
                except (TypeError, ValueError):
                    continue
                if section_number not in known_sections or section_number in used:
                    continue
                section = known_sections[section_number]
                ids.append(section.id)
                used.add(section_number)
                types[section.id] = classify_content_type(section)
            if ids:
                suggestions.append(TopicSuggestion(title, str(item.get("description") or "")[:1_000] or None, ids, types))
        if not suggestions:
            return []
        # Modelin atladığı parçaları yeni, kalitesiz bir tarih başlığı oluşturmadan en
        # yakın kavramsal öneriye bağla. Başlık her durumda dil modelinden gelir.
        unassigned = [section for number, section in enumerate(sections, start=1) if number not in used]
        if unassigned:
            for section in unassigned:
                target = max(
                    suggestions,
                    key=lambda suggestion: self._content_overlap(section, suggestion, known_sections),
                )
                target.section_ids.append(section.id)
                target.section_types[section.id] = classify_content_type(section)
        return suggestions

    @staticmethod
    def _content_overlap(
        section: SectionAnalysisInput,
        suggestion: TopicSuggestion,
        known_sections: dict[int, SectionAnalysisInput],
    ) -> int:
        """Eksik bir parçayı en yakın model-konusu altında tutmak için basit örtüşme skoru."""
        terms = set(re.findall(r"[\wçğıöşü]+", section.content.casefold()))
        topic_text = f"{suggestion.title} {suggestion.description or ''} " + " ".join(
            known.content for known in known_sections.values() if known.id in suggestion.section_ids
        )
        return len(terms.intersection(re.findall(r"[\wçğıöşü]+", topic_text.casefold())))

@lru_cache
def get_document_analyzer() -> DocumentAnalyzer:
    return DocumentAnalyzer()
