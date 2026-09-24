from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import LearnerFeedback, LearnerProfile, LearnerTopicProgress, Topic


def test_profile_bootstrap_migrates_progress_and_persists_updates() -> None:
    client_id = str(uuid4())
    with SessionLocal() as database:
        topic = Topic(slug=f"profil-test-{uuid4().hex}", title="Profil test konusu", order_index=999)
        database.add(topic)
        database.commit()
        topic_id = topic.id

    initial_progress = {
        "completed_step_ids": [str(uuid4())],
        "review_plans": [{"stepId": str(uuid4()), "heading": "Tekrar", "stage": 0, "dueAt": 1_800_000_000_000}],
        "last_step_id": None,
        "last_visited_at": 1_700_000_000_000,
        "quiz_attempts": 1,
        "last_quiz_score": 2,
    }
    with TestClient(app) as client:
        created = client.post(
            "/api/profiles/bootstrap",
            json={"client_id": client_id, "local_progress": {str(topic_id): initial_progress}},
        )
        assert created.status_code == 200
        profile = created.json()
        assert profile["progress"][str(topic_id)] == {**initial_progress, "last_quiz_at": 0}

        # Aynı istemcinin sonraki açılışında sunucudaki kayıt kaynak kaynaktır.
        second = client.post("/api/profiles/bootstrap", json={"client_id": client_id, "local_progress": {}})
        assert second.status_code == 200
        assert second.json()["id"] == profile["id"]

        updated_progress = {**initial_progress, "quiz_attempts": 2, "last_quiz_score": 3}
        saved = client.put(
            f"/api/profiles/{profile['id']}/progress/{topic_id}",
            json={"client_id": client_id, **updated_progress},
        )
        renamed = client.patch(
            f"/api/profiles/{profile['id']}",
            json={"client_id": client_id, "display_name": "Onur"},
        )
        feedback = client.post(
            "/api/feedback",
            json={"client_id": client_id, "profile_id": profile["id"], "kind": "answer_helpful"},
        )

    assert saved.status_code == 200
    assert saved.json()["quiz_attempts"] == 2
    assert renamed.status_code == 200
    assert renamed.json()["display_name"] == "Onur"
    assert feedback.status_code == 201
    with SessionLocal() as database:
        persisted_profile = database.scalar(select(LearnerProfile).where(LearnerProfile.client_id == client_id))
        persisted_progress = database.scalar(
            select(LearnerTopicProgress).where(
                LearnerTopicProgress.profile_id == persisted_profile.id,
                LearnerTopicProgress.topic_id == topic_id,
            )
        )
        saved_feedback = database.scalar(
            select(LearnerFeedback).where(LearnerFeedback.profile_id == persisted_profile.id)
        )
    assert persisted_progress is not None
    assert persisted_progress.quiz_attempts == 2
    assert saved_feedback is not None
