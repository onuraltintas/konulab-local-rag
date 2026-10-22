"""SQL kaynak kayıtları ile Qdrant indeksini birlikte günceller."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.chunking import ParsedSection, split_sections_by_tokens
from app.embeddings import get_embedder, get_sparse_embedder
from app.models import DocumentSection, EmbeddingStatus, SectionTopicProposal, SourceDocument, TopicSource
from app.vector_store import SectionVector, get_vector_store
from app.contextual_indexing import contextual_passage


def table_search_text(content: str) -> str:
    """Markdown tablosunu embedding için başlık-değer biçimine çevirir.

    Özgün tablo SQL'de ve kaynak görüntüleyicide korunur; bu yalnızca yoğun/sparse
    aramada `sütun adı + değer` sorgularının daha güvenilir eşleşmesini sağlar.
    """
    lines = content.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        if not lines[index].lstrip().startswith("|"):
            output.append(lines[index])
            index += 1
            continue
        rows: list[list[str]] = []
        while index < len(lines) and lines[index].lstrip().startswith("|"):
            cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
                rows.append(cells)
            index += 1
        if len(rows) < 2:
            output.extend(" | ".join(row) for row in rows)
            continue
        headers = rows[0]
        output.append("Tablo:")
        for row in rows[1:]:
            output.append("; ".join(f"{headers[position] if position < len(headers) else f'Sütun {position + 1}'}: {value}" for position, value in enumerate(row) if value))
    return "\n".join(output)


def index_document(database: Session, document: SourceDocument) -> int:
    """Dokümanın tüm bölümlerini embed edip Qdrant'a yazar; commit çağırmaz."""
    sections = list(
        database.scalars(
            select(DocumentSection)
            .where(DocumentSection.document_id == document.id)
            .order_by(DocumentSection.sequence)
            .options(selectinload(DocumentSection.topics))
        )
    )
    if not sections:
        return 0

    passages = [
        f"{section.heading_path}\n\n{table_search_text(section.content) if section.content_type == 'table' else section.content}"
        for section in sections
    ]
    embedder = get_embedder()
    passage_budget = embedder.passage_token_budget()
    token_lengths = [len(embedder.tokenizer(passage, add_special_tokens=False).get("input_ids", [])) for passage in passages]
    if any(length > passage_budget for length in token_lengths):
        raise ValueError("Kaynak parçası embedding token bütçesini aşıyor; yeniden parçalanmalı.")
    passages = [contextual_passage(passage, section, document, embedder)
                for passage, section in zip(passages, sections, strict=True)]
    embeddings = embedder.embed_passages(passages)
    sparse_embeddings = get_sparse_embedder().embed_passages(passages)
    vectors = [
        SectionVector(
            section_id=section.id,
            document_id=document.id,
            logical_key=document.logical_key,
            document_version=document.version,
            source_name=document.lesson_title or document.source_name,
            heading_path=section.heading_path,
            content_type=section.content_type,
            line_start=section.line_start,
            line_end=section.line_end,
            topic_ids=[topic_source.topic_id for topic_source in section.topics],
            vector=embedding,
            sparse_indices=sparse_embedding.indices,
            sparse_values=sparse_embedding.values,
        )
        for section, embedding, sparse_embedding in zip(sections, embeddings, sparse_embeddings, strict=True)
    ]
    get_vector_store().upsert_sections(vectors)
    for section in sections:
        section.embedding_status = EmbeddingStatus.READY
    database.flush()
    return len(sections)


def remove_document_vectors(document: SourceDocument) -> None:
    get_vector_store().delete_document(document.id)


def rechunk_document_sections(database: Session, document: SourceDocument) -> tuple[bool, int]:
    """Eski karakter-parçalarını token güvenli parçalara dönüştürür.

    Yeni parçalar eski bölümün onaylı konu/proposal eşleşmelerini devralır. Böylece
    kaynak metni veya kullanıcının konu onayı kaybolmadan mevcut Qdrant kaydı
    yeniden üretilebilir.
    """
    sections = list(
        database.scalars(
            select(DocumentSection)
            .where(DocumentSection.document_id == document.id)
            .order_by(DocumentSection.sequence)
            .options(selectinload(DocumentSection.topics), selectinload(DocumentSection.proposed_topics))
        )
    )
    if not sections:
        return False, 0
    embedder = get_embedder()
    plans: list[tuple[DocumentSection, list[ParsedSection], list[TopicSource], list[SectionTopicProposal]]] = []
    changed = False
    for section in sections:
        split_parts = split_sections_by_tokens(
            [
                ParsedSection(
                    heading_path=tuple(section.heading_path.split(" > ")),
                    content=section.content,
                    content_type=section.content_type,
                    line_start=section.line_start,
                    line_end=section.line_end,
                )
            ],
            embedder.tokenizer,
            embedder.passage_token_budget(),
        )
        changed = changed or len(split_parts) > 1
        plans.append((section, split_parts, list(section.topics), list(section.proposed_topics)))
    if not changed:
        return False, len(sections)

    replacements: list[tuple[DocumentSection, list[TopicSource], list[SectionTopicProposal]]] = []
    sequence = 1
    for original, parts, topic_links, proposal_links in plans:
        for part in parts:
            replacement = DocumentSection(
                document_id=document.id,
                parent_section_id=original.parent_section_id,
                heading_path=" > ".join(part.heading_path),
                content=part.content,
                content_type=part.content_type,
                sequence=sequence,
                line_start=part.line_start,
                line_end=part.line_end,
                embedding_status=EmbeddingStatus.PENDING,
            )
            database.add(replacement)
            replacements.append((replacement, topic_links, proposal_links))
            sequence += 1
    database.flush()
    for replacement, topic_links, proposal_links in replacements:
        for link in topic_links:
            database.add(
                TopicSource(
                    topic_id=link.topic_id,
                    section_id=replacement.id,
                    relevance=link.relevance,
                    approved=link.approved,
                )
            )
        for link in proposal_links:
            database.add(
                SectionTopicProposal(
                    section_id=replacement.id,
                    topic_proposal_id=link.topic_proposal_id,
                    content_type=link.content_type,
                    confidence=link.confidence,
                )
            )
    for original, _, _, _ in plans:
        database.delete(original)
    database.flush()
    return True, len(replacements)
