from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Topic


def test_topic_prerequisites_are_saved_and_cycles_are_rejected() -> None:
    with SessionLocal() as database:
        first = Topic(slug=f"on-kosul-a-{uuid4().hex}", title="Temel", order_index=999)
        second = Topic(slug=f"on-kosul-b-{uuid4().hex}", title="İleri", order_index=999)
        database.add_all([first, second])
        database.commit()
        first_id, second_id = first.id, second.id

    with TestClient(app) as client:
        saved = client.put(
            f"/api/topics/{second_id}/prerequisites", json={"prerequisite_topic_ids": [first_id]}
        )
        listed = client.get(f"/api/topics/{second_id}/prerequisites")
        cycle = client.put(
            f"/api/topics/{first_id}/prerequisites", json={"prerequisite_topic_ids": [second_id]}
        )

    assert saved.status_code == 200
    assert [topic["id"] for topic in listed.json()] == [first_id]
    assert cycle.status_code == 422
