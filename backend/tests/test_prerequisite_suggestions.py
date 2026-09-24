from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Topic


def test_prerequisite_suggestions_are_returned_without_persisting(monkeypatch) -> None:
    from app import main

    with SessionLocal() as database:
        prerequisite = Topic(slug=f"temel-{uuid4().hex}", title="Temel kavram", order_index=999)
        target = Topic(slug=f"ileri-{uuid4().hex}", title="İleri kavram", order_index=999)
        database.add_all([prerequisite, target])
        database.commit()
        prerequisite_id, target_id = prerequisite.id, target.id

    class FakeOllama:
        async def suggest_prerequisites(self, target, candidates):
            assert target["id"] == target_id
            assert any(candidate["id"] == prerequisite_id for candidate in candidates)
            return [{"topic_id": prerequisite_id, "reason": "Temel kavram gerekir.", "confidence": 0.91}]

    monkeypatch.setattr(main, "get_ollama_client", lambda: FakeOllama())
    with TestClient(app) as client:
        # Yerel SQLite test çalıştırmalarında daha eski örnek veriler kalmış
        # olabilir. Sözleşme, önerinin hiçbir yeni ilişki yazmamasıdır.
        before = client.get(f"/api/topics/{target_id}/prerequisites")
        response = client.post(f"/api/topics/{target_id}/prerequisite-suggestions")
        saved = client.get(f"/api/topics/{target_id}/prerequisites")

    assert response.status_code == 200
    assert response.json()[0]["topic_id"] == prerequisite_id
    assert saved.json() == before.json()
