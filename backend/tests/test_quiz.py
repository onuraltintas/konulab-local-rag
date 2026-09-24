from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.db import SessionLocal
from app.models import Topic
from app.schemas import TopicStudyStepResponse


def test_topic_quiz_uses_valid_study_step_reference(monkeypatch) -> None:
    from app import main

    step_id = uuid4()

    class FakeOllama:
        async def generate_topic_quiz(self, topic_title, steps):
            assert topic_title == "Quiz test konusu"
            assert [step.id for step in steps] == [step_id]
            return {
                "questions": [
                    {
                        "id": f"q{number}",
                        "question": f"Soru {number}",
                        "options": [
                            {"id": "A", "text": "Doğru"},
                            {"id": "B", "text": "Yanlış 1"},
                            {"id": "C", "text": "Yanlış 2"},
                            {"id": "D", "text": "Yanlış 3"},
                        ],
                        "correct_option_id": "A",
                        "explanation": "Kaynak açıklaması.",
                        "related_step_id": str(step_id),
                    }
                    for number in range(1, 4)
                ]
            }

    with SessionLocal() as database:
        topic = Topic(slug=f"quiz-test-{uuid4().hex}", title="Quiz test konusu", order_index=999)
        database.add(topic)
        database.commit()
        topic_id = topic.id

    monkeypatch.setattr(
        main,
        "build_topic_study_steps",
        lambda _topic_id, _database: [
            TopicStudyStepResponse(
                id=step_id,
                section_ids=[step_id],
                source_name="test.md",
                heading="Test adımı",
                content="Kaynak metin.",
                content_type="explanation",
                line_start=1,
                line_end=2,
            )
        ],
    )
    monkeypatch.setattr(main, "get_ollama_client", lambda: FakeOllama())

    with TestClient(app) as client:
        response = client.post(f"/api/topics/{topic_id}/quiz")

    assert response.status_code == 200
    body = response.json()
    assert body["topic_id"] == topic_id
    assert len(body["questions"]) == 3
    assert {question["related_step_id"] for question in body["questions"]} == {str(step_id)}
