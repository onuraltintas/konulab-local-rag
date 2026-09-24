from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import DocumentSection, DocumentStatus, EmbeddingStatus, SourceDocument


def test_source_view_returns_focused_section_with_neighbouring_context() -> None:
    key = f"kaynak-goruntuleme-{uuid4().hex}"
    with SessionLocal() as database:
        document = SourceDocument(
            logical_key=key,
            source_name="kaynak.md",
            source_path=f"/tmp/{key}.md",
            media_type="text/markdown",
            content_hash=uuid4().hex + uuid4().hex,
            status=DocumentStatus.ACTIVE,
            byte_size=100,
        )
        database.add(document)
        database.flush()
        sections = [
            DocumentSection(
                document_id=document.id,
                heading_path="Konu",
                content=f"Parça {sequence}",
                sequence=sequence,
                line_start=sequence * 10,
                line_end=sequence * 10 + 4,
                embedding_status=EmbeddingStatus.READY,
            )
            for sequence in range(5)
        ]
        database.add_all(sections)
        database.commit()
        focus_id = sections[2].id

    with TestClient(app) as client:
        response = client.get(f"/api/sections/{focus_id}/source-view")

    assert response.status_code == 200
    body = response.json()
    assert body["source_name"] == "kaynak.md"
    assert len(body["sections"]) == 5
    focus = next(section for section in body["sections"] if section["is_focus"])
    assert focus["id"] == str(focus_id)
    assert focus["line_start"] == 20
