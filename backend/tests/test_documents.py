from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.main import app
from app.models import Topic, TopicSource
from topic_fixtures import seed_topics


def test_same_logical_document_replaces_active_version(tmp_path, monkeypatch) -> None:
    from app import main

    monkeypatch.setattr(main.settings, "storage_path", tmp_path / "sources")
    monkeypatch.setattr(main, "index_document", lambda database, document: len(document.sections))
    monkeypatch.setattr(main, "remove_document_vectors", lambda document: None)
    logical_key = f"tokenizasyon-notlari-{uuid4().hex}"
    with TestClient(app) as client:
        first = client.post(
            "/api/documents",
            data={"logical_key": logical_key},
            files={"file": ("tokenizasyon.md", f"# Tokenizasyon\n\n{logical_key} " + "İlk sürüm. " * 40, "text/markdown")},
        )
        second = client.post(
            "/api/documents",
            data={"logical_key": logical_key},
            files={"file": ("tokenizasyon.md", f"# Tokenizasyon\n\n{logical_key} " + "Yeni sürüm. " * 40, "text/markdown")},
        )
        active_documents = client.get("/api/documents")
        versions = client.get("/api/documents?include_replaced=true")

    assert first.status_code == 201
    assert first.json()["action"] == "created"
    assert second.status_code == 201
    assert second.json()["action"] == "replaced"
    assert second.json()["document"]["version"] == 2
    active_ids = {document["id"] for document in active_documents.json()}
    all_statuses = {document["id"]: document["status"] for document in versions.json()}
    assert second.json()["document"]["id"] in active_ids
    assert first.json()["document"]["id"] not in active_ids
    assert all_statuses[first.json()["document"]["id"]] == "replaced"


def test_upload_assigns_all_sections_to_selected_topic(tmp_path, monkeypatch) -> None:
    from app import main

    monkeypatch.setattr(main.settings, "storage_path", tmp_path / "sources")
    monkeypatch.setattr(main, "index_document", lambda database, document: len(document.sections))
    with SessionLocal() as database:
        seed_topics(database)
    logical_key = f"konu-esleme-{uuid4().hex}"
    with TestClient(app) as client:
        topic_id = client.get("/api/topics").json()[0]["id"]
        response = client.post(
            "/api/documents",
            data={"logical_key": logical_key, "topic_id": str(topic_id)},
            files={"file": ("konu.md", "# İlk Başlık\n\nBirinci bölüm.\n\n# İkinci Başlık\n\nİkinci bölüm.", "text/markdown")},
        )

    assert response.status_code == 201
    with SessionLocal() as database:
        mapping_count = database.scalar(
            select(func.count(TopicSource.id)).where(TopicSource.topic_id == topic_id)
        )
    assert mapping_count >= response.json()["section_count"]


def test_delete_document_removes_source_and_vectors(tmp_path, monkeypatch) -> None:
    from app import main

    removed_document_ids: list[object] = []
    monkeypatch.setattr(main.settings, "storage_path", tmp_path / "sources")
    monkeypatch.setattr(main, "index_document", lambda database, document: len(document.sections))
    monkeypatch.setattr(main, "remove_document_vectors", lambda document: removed_document_ids.append(document.id))
    logical_key = f"silinecek-kaynak-{uuid4().hex}"
    with SessionLocal() as database:
        topic = Topic(slug=f"silinecek-konu-{uuid4().hex}", title="Silinecek konu", order_index=999)
        database.add(topic)
        database.commit()
        topic_id = topic.id

    with TestClient(app) as client:
        created = client.post(
            "/api/documents",
            data={"logical_key": logical_key, "topic_id": str(topic_id)},
            files={"file": ("silinecek.md", "# Kaynak\n\nSilinecek metin. " * 20, "text/markdown")},
        )
        document_id = created.json()["document"]["id"]
        impact = client.post("/api/documents/deletion-impact", json={"document_ids": [document_id]})
        deleted = client.delete(f"/api/documents/{document_id}")
        documents = client.get("/api/documents")

    assert created.status_code == 201
    assert impact.status_code == 200
    assert topic_id in {topic["id"] for topic in impact.json()["affected_topics"]}
    assert deleted.status_code == 204
    assert document_id not in {document["id"] for document in documents.json()}
    assert removed_document_ids
    assert not list((tmp_path / "sources" / logical_key).glob("*"))
    with SessionLocal() as database:
        assert database.get(Topic, topic_id) is None


def test_bulk_delete_documents_removes_every_requested_source(tmp_path, monkeypatch) -> None:
    from app import main

    monkeypatch.setattr(main.settings, "storage_path", tmp_path / "sources")
    monkeypatch.setattr(main, "index_document", lambda database, document: len(document.sections))
    monkeypatch.setattr(main, "remove_document_vectors", lambda document: None)
    keys = [f"toplu-silinecek-{uuid4().hex}", f"toplu-silinecek-{uuid4().hex}"]

    with TestClient(app) as client:
        created = [
            client.post(
                "/api/documents",
                data={"logical_key": key},
                files={"file": (f"{key}.md", "# Kaynak\n\nSilinecek metin.", "text/markdown")},
            )
            for key in keys
        ]
        document_ids = [response.json()["document"]["id"] for response in created]
        deleted = client.request("DELETE", "/api/documents", json={"document_ids": document_ids})
        documents = client.get("/api/documents")

    assert all(response.status_code == 201 for response in created)
    assert deleted.status_code == 200
    assert deleted.json()["deleted_count"] == 2
    assert not set(document_ids).intersection(document["id"] for document in documents.json())
