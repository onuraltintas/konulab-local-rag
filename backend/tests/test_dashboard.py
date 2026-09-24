from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import DocumentSection, DocumentStatus, EmbeddingStatus, SourceDocument, Topic, TopicSource
from app.schemas import TopicStudyStepResponse


def test_dashboard_summarizes_due_reviews_and_topic_progress(monkeypatch) -> None:
    from app import main

    client_id = str(uuid4())
    step_id = uuid4()
    with SessionLocal() as database:
        topic = Topic(slug=f"panel-{uuid4().hex}", title="Panel konusu", order_index=999)
        document = SourceDocument(
            logical_key=f"panel-{uuid4().hex}", source_name="panel.md", source_path="/tmp/panel.md",
            media_type="text/markdown", content_hash=uuid4().hex + uuid4().hex,
            status=DocumentStatus.ACTIVE, byte_size=10,
        )
        database.add_all([topic, document])
        database.flush()
        section = DocumentSection(
            document_id=document.id, heading_path="Panel", content="Kaynak", sequence=0,
            line_start=1, line_end=2, embedding_status=EmbeddingStatus.READY,
        )
        database.add(section)
        database.flush()
        database.add(TopicSource(topic_id=topic.id, section_id=section.id, approved=True))
        database.commit()
        topic_id = topic.id

    monkeypatch.setattr(main, "build_topic_study_steps", lambda _topic_id, _database: [
        TopicStudyStepResponse(
            id=step_id, section_ids=[step_id], source_name="panel.md", heading="Panel adımı",
            content="Kaynak", content_type="explanation", line_start=1, line_end=2,
        )
    ])
    with TestClient(app) as client:
        profile = client.post("/api/profiles/bootstrap", json={"client_id": client_id, "local_progress": {}}).json()
        saved = client.put(
            f"/api/profiles/{profile['id']}/progress/{topic_id}",
            json={
                "client_id": client_id,
                "completed_step_ids": [],
                "review_plans": [{"stepId": str(step_id), "heading": "Panel adımı", "dueAt": 1, "stage": 0}],
                "last_step_id": str(step_id), "last_visited_at": 10,
                "quiz_attempts": 1, "last_quiz_score": 2, "last_quiz_at": 10,
            },
        )
        response = client.get(f"/api/profiles/{profile['id']}/dashboard", params={"client_id": client_id})

    assert saved.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["in_progress_topics"] >= 1
    assert any(review["step_id"] == str(step_id) for review in body["today_reviews"])
    assert body["recent_quizzes"][0]["last_quiz_score"] == 2
