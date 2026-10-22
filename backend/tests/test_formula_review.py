import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from app import main
from app.chunking import ParsedSection
from app.formula_validation import formula_signature
from app.models import ConversionStatus, DocumentConversion, SourceDocument
from test_reindexing import environment as environment


def test_formula_warnings_need_explicit_acknowledgment_for_exact_revision(environment, monkeypatch):
    client, factory, document_id, calls = environment
    markdown = "# Olasılık\n\n$$x=-2$$"
    digest = hashlib.sha256(markdown.encode()).hexdigest()
    with factory() as session:
        document = session.scalar(select(SourceDocument))
        path = Path(document.source_path).with_name("converted.md")
        path.write_text(markdown, encoding="utf-8")
        Path(document.source_path + ".quality.json").write_text(json.dumps({
            "formula_signature": formula_signature(markdown),
            "formulas": [{"latex": "x=-2", "validation": {"needs_review": True}}],
        }), encoding="utf-8")
        session.add(DocumentConversion(document_id=document.id, status=ConversionStatus.READY,
                                        markdown_path=str(path), markdown_hash=digest))
        session.commit()
    monkeypatch.setattr(main, "parse_document_for_embedding", lambda *args: [ParsedSection(
        heading_path=("Olasılık",), content="$$x=-2$$", content_type="explanation", line_start=1, line_end=3)])
    monkeypatch.setattr(main, "create_topic_proposals", lambda *args: None)
    detail = client.get(f"/api/documents/{document_id}/conversion").json()
    assert detail["formula_review_required"] is True
    payload = {"confirmation": "OGRENME_HARITASINA_DONUSTUR"}
    assert client.post(f"/api/documents/{document_id}/publish-conversion", json=payload).status_code == 409
    assert client.post(f"/api/documents/{document_id}/publish-conversion", json={**payload, "accept_formula_warnings": True, "expected_markdown_hash": "0"*64}).status_code == 409
    assert client.post(f"/api/documents/{document_id}/publish-conversion", json={**payload, "accept_formula_warnings": True, "expected_markdown_hash": digest}).status_code == 202
    with factory() as session:
        assert session.scalar(select(DocumentConversion)).formula_review_hash == digest


def test_invalid_formula_image_index_returns_404(environment):
    client, factory, document_id, calls = environment
    with factory() as session:
        document = session.scalar(select(SourceDocument))
        Path(document.source_path + ".quality.json").write_text('{"formulas":[]}', encoding="utf-8")
    assert client.get(f"/api/documents/{document_id}/formulas/-1/image").status_code == 404
    assert client.get(f"/api/documents/{document_id}/formulas/99/image").status_code == 404
