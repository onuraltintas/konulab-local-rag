from uuid import uuid4

from fastapi.testclient import TestClient

from app.document_analysis import TopicSuggestion
from app.db import SessionLocal
from app.main import app
from topic_fixtures import seed_topics


def test_document_analysis_requires_approval_before_indexing(tmp_path, monkeypatch) -> None:
    from app import main

    class FakeAnalyzer:
        async def suggest_topics(self, sections):
            return [
                TopicSuggestion(
                    title="Tokenizasyon",
                    description="Metni tokenlara ayırma.",
                    section_ids=[section.id for section in sections],
                    section_types={section.id: "example" for section in sections},
                )
            ]

        async def suggest_lesson_title(self, topics):
            return "Tokenizasyon"

    monkeypatch.setattr(main.settings, "storage_path", tmp_path / "sources")
    monkeypatch.setattr(main, "get_document_analyzer", lambda: FakeAnalyzer())
    monkeypatch.setattr(main, "index_document", lambda database, document: len(document.sections))
    monkeypatch.setattr(main, "remove_document_vectors", lambda document: None)
    with SessionLocal() as database:
        seed_topics(database)
    logical_key = f"analiz-onay-{uuid4().hex}"
    content = "# Tokenizasyon\n\n## Örnek\n\n" + "Tokenizasyon örneği. " * 30

    with TestClient(app) as client:
        analysis = client.post(
            "/api/documents/analyze",
            data={"logical_key": logical_key},
            files={"file": ("tokenizasyon.md", content, "text/markdown")},
        )
        assert analysis.status_code == 202
        document_id = analysis.json()["document"]["id"]
        assert analysis.json()["status"] == "processing"
        completed_analysis = client.get(f"/api/documents/{document_id}/analysis")
        assert completed_analysis.status_code == 200
        assert completed_analysis.json()["status"] == "ready"
        proposal_id = completed_analysis.json()["topics"][0]["id"]
        before_approval = client.get("/api/documents")
        approval = client.post(
            f"/api/documents/{document_id}/approve-analysis",
            json={
                "decisions": [
                    {"proposal_id": proposal_id, "title": "Tokenizasyon", "description": "Metni parçalara ayırma."}
                ],
                # Test veritabanı önceki örneklerden aynı ders adına sahip bir
                # kayıt barındırabilir; bu test konu onayı akışını ölçer.
                "allow_existing_lesson_merge": True,
            },
        )

    pending_document = next(document for document in before_approval.json() if document["id"] == document_id)
    assert pending_document["status"] == "processing"
    assert approval.status_code == 202
    completed_approval = client.get(f"/api/documents/{document_id}/analysis")
    assert completed_approval.json()["status"] == "approved"
    assert completed_approval.json()["document"]["status"] == "active"
