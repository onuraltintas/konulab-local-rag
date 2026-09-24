from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import LearnerFeedback


def test_quality_center_reviews_feedback_before_export() -> None:
    client_id = str(uuid4())
    with TestClient(app) as client:
        created = client.post(
            "/api/feedback",
            json={"client_id": client_id, "kind": "quiz_question_issue", "payload": {"question": "Test"}},
        )
        assert created.status_code == 201
        feedback_id = created.json()["id"]

        overview = client.get("/api/quality/overview")
        listed = client.get("/api/quality/feedback?review_status=new")
        reviewed = client.patch(
            f"/api/quality/feedback/{feedback_id}",
            json={"review_status": "reviewed", "review_note": "Gerçekten hatalı."},
        )

    assert overview.status_code == 200
    assert listed.status_code == 200
    assert feedback_id in {item["id"] for item in listed.json()}
    assert reviewed.status_code == 200
    assert reviewed.json()["review_status"] == "reviewed"
    assert reviewed.json()["review_note"] == "Gerçekten hatalı."
    with SessionLocal() as database:
        saved = database.scalar(select(LearnerFeedback).where(LearnerFeedback.id == UUID(feedback_id)))
    assert saved is not None
    assert saved.review_status == "reviewed"
