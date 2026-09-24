from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import LearnerFeedback


def test_anonymous_feedback_is_persisted() -> None:
    client_id = str(uuid4())
    with TestClient(app) as client:
        response = client.post(
            "/api/feedback",
            json={
                "client_id": client_id,
                "kind": "source_mismatch",
                "payload": {"heading": "Test kaynağı"},
            },
        )

    assert response.status_code == 201
    with SessionLocal() as database:
        saved = database.scalar(select(LearnerFeedback).where(LearnerFeedback.id == UUID(response.json()["id"])))
    assert saved is not None
    assert saved.client_id == client_id
    assert saved.kind == "source_mismatch"


def test_feedback_state_returns_only_the_current_local_profile_feedback() -> None:
    first_client = str(uuid4())
    second_client = str(uuid4())
    with TestClient(app) as client:
        first_profile = client.post("/api/profiles/bootstrap", json={"client_id": first_client, "progress": {}}).json()
        client.post("/api/profiles/bootstrap", json={"client_id": second_client, "progress": {}})
        client.post("/api/feedback", json={
            "client_id": first_client,
            "profile_id": first_profile["id"],
            "kind": "answer_helpful",
            "conversation_id": "11111111-1111-4111-8111-111111111111",
        })
        client.post("/api/feedback", json={
            "client_id": second_client,
            "kind": "answer_unhelpful",
            "conversation_id": "22222222-2222-4222-8222-222222222222",
        })
        state = client.get("/api/feedback/state", params={"client_id": first_client, "profile_id": first_profile["id"]})

    assert state.status_code == 200
    assert state.json()["keys"] == ["answer:11111111-1111-4111-8111-111111111111:helpful"]
