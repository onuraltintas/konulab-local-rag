import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import contextual_indexing as module
from app.db import Base
from app.models import DocumentSection, SourceDocument
from app.runtime_models import ModelProviderError


def fixture(database):
    document = SourceDocument(logical_key="test", source_name="test.md", source_path="/tmp/test.md",
                              media_type="text/markdown", content_hash="a" * 64, byte_size=20,
                              contextual_enrichment=True)
    database.add(document)
    database.flush()
    section = DocumentSection(document_id=document.id, heading_path="Olasılık", content="Özgün kaynak.",
                              sequence=1, line_start=1, line_end=2)
    database.add(section)
    database.flush()
    return document, section


def test_context_is_separate_persistent_cached_and_invalidated(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    calls = []

    async def complete(*args, **kwargs):
        calls.append(kwargs)
        return '{"context":"Olasılık kavramının tanımı."}'

    monkeypatch.setattr(module, "complete", complete)
    with Session(engine) as database:
        document, section = fixture(database)
        asyncio.run(module.enrich_document(database, document))
        database.commit()
        database.expire_all()
        assert section.content == "Özgün kaynak."
        assert section.search_context == "Olasılık kavramının tanımı."
        asyncio.run(module.enrich_document(database, document))
        assert len(calls) == 1
        document.content_hash = "b" * 64
        asyncio.run(module.enrich_document(database, document))
        assert len(calls) == 2
        section.content = "Değişen kaynak."
        asyncio.run(module.enrich_document(database, document))
        assert len(calls) == 3


def test_disabled_never_calls_model_and_failure_keeps_source(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    calls = []

    async def complete(*args, **kwargs):
        calls.append(True)
        raise ModelProviderError("offline")

    monkeypatch.setattr(module, "complete", complete)
    with Session(engine) as database:
        document, section = fixture(database)
        document.contextual_enrichment = False
        asyncio.run(module.enrich_document(database, document))
        assert not calls
        document.contextual_enrichment = True
        asyncio.run(module.enrich_document(database, document))
        assert section.content == "Özgün kaynak."
        assert section.search_context is None
        assert section.search_context_metadata["status"] == "failed"


def test_context_never_truncates_source_or_exceeds_token_budget():
    embedder = SimpleNamespace(tokenizer=lambda text, **kw: {"input_ids": text.split()}, passage_token_budget=lambda: 8)
    section = SimpleNamespace(search_context="Kısa bağlam", search_context_metadata={})
    document = SimpleNamespace(contextual_enrichment=True)
    assert module.contextual_passage("Asıl kaynak", section, document, embedder).endswith("Asıl kaynak")
    source = "bir iki üç dört beş altı yedi sekiz"
    assert module.contextual_passage(source, section, document, embedder) == source
    assert section.search_context_metadata["skip_reason"] == "token_budget"


def test_formula_extraction_pipeline_enabled(monkeypatch):
    import sys
    from app.conversion import _build_converter
    captured = {}
    class Converter:
        def __init__(self, **kwargs):
            captured.update(kwargs)
    monkeypatch.setitem(sys.modules, "docling.datamodel.base_models", SimpleNamespace(InputFormat=SimpleNamespace(PDF="pdf")))
    monkeypatch.setitem(sys.modules, "docling.datamodel.pipeline_options", SimpleNamespace(PdfPipelineOptions=SimpleNamespace))
    monkeypatch.setitem(sys.modules, "docling.document_converter", SimpleNamespace(DocumentConverter=Converter, PdfFormatOption=lambda **kwargs: kwargs))
    _build_converter(use_ocr=False)
    options = captured["format_options"]["pdf"]["pipeline_options"]
    assert options.do_formula_enrichment is True
    assert options.do_table_structure is True


def test_undecoded_formula_retains_original_and_flags_review(monkeypatch):
    from pathlib import Path
    from app import conversion
    item = SimpleNamespace(label="formula", text="", orig="x squared", self_ref="#/texts/0", prov=[])
    document = SimpleNamespace(pages={}, tables=[],
        export_to_markdown=lambda: "Başlangıç\n<!-- formula-not-decoded -->\nSon",
        iterate_items=lambda: iter([(item, 0)]))
    monkeypatch.setattr(conversion, "_build_converter", lambda **kwargs: SimpleNamespace(convert=lambda path: SimpleNamespace(document=document)))
    result = conversion._convert_once(Path("test.docx"), use_ocr=False)
    assert "x squared" in result.markdown
    assert result.quality_report["requires_review"] is True
    assert result.quality_report["formulas"] == [{"source_ref": "#/texts/0", "decoded": False, "latex": "", "provenance": []}]


def test_dense_and_sparse_use_context_but_sql_source_does_not(monkeypatch):
    from app import indexing
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    seen = {}
    def dense(passages):
        seen["dense"] = passages
        return [[1.0] for _ in passages]
    def sparse(passages):
        seen["sparse"] = passages
        return [SimpleNamespace(indices=[1], values=[1.0]) for _ in passages]
    embedder = SimpleNamespace(tokenizer=lambda text, **kw: {"input_ids": text.split()},
                               passage_token_budget=lambda: 100, embed_passages=dense)
    monkeypatch.setattr(indexing, "get_embedder", lambda: embedder)
    monkeypatch.setattr(indexing, "get_sparse_embedder", lambda: SimpleNamespace(embed_passages=sparse))
    monkeypatch.setattr(indexing, "get_vector_store", lambda: SimpleNamespace(upsert_sections=lambda vectors: None))
    with Session(engine) as database:
        document, section = fixture(database)
        section.search_context = "Koşullu olasılık tanımı"
        indexing.index_document(database, document)
        assert seen["dense"] == seen["sparse"]
        assert "Koşullu olasılık tanımı" in seen["dense"][0]
        assert section.content == "Özgün kaynak."
        document.contextual_enrichment = False
        indexing.index_document(database, document)
        assert "Koşullu olasılık tanımı" not in seen["dense"][0]
