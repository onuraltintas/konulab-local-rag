from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import SearchResult


def test_chat_response_is_persisted_with_source_snapshot(monkeypatch) -> None:
    from app import main

    class FakeOllama:
        async def answer(self, question, sources, history, learning_mode=False):
            assert question.startswith("__test sohbet")
            assert history == []
            return "Kaynağa dayalı yanıt [K1]."

    monkeypatch.setattr(
        main,
        "semantic_search",
        lambda request, database: [
            SearchResult(
                section_id=uuid4(),
                document_id=uuid4(),
                score=0.91,
                source_name="ders-notu.md",
                logical_key="ders-notu",
                heading_path="Tokenizasyon",
                content_type="explanation",
                line_start=10,
                line_end=22,
                content="Tokenizasyon metni parçalara ayırır.",
            )
        ],
    )
    monkeypatch.setattr(main, "get_ollama_client", lambda: FakeOllama())
    question = f"__test sohbet {uuid4().hex}"

    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": question})
        conversation = client.get(f"/api/conversations/{response.json()['conversation_id']}")

    assert response.status_code == 200
    assert conversation.status_code == 200
    messages = conversation.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["sources"][0]["source_name"] == "ders-notu.md"
