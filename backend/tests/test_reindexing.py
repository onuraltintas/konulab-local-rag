from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import contextual_indexing, main, reindexing
from app.db import Base, get_db
from app.models import DocumentIndexJob, DocumentSection, DocumentStatus, SourceDocument


@pytest.fixture
def environment(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "migrate_schema", lambda: None)  # fresh, isolated schema
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(reindexing, "SessionLocal", factory)
    def database():
        with factory() as session:
            yield session
    main.app.dependency_overrides[get_db] = database
    calls = []
    async def complete(*args, **kwargs):
        calls.append("model")
        return '{"context":"Koşullu olasılık konusu."}'
    monkeypatch.setattr(contextual_indexing, "complete", complete)
    def index(database, document):
        calls.append(("index", document.contextual_enrichment))
        return 1
    monkeypatch.setattr(main, "index_document", index)
    monkeypatch.setattr(reindexing, "index_document", index)
    with factory() as session:
        path = tmp_path / "source.md"
        path.write_text("Özgün kaynak.", encoding="utf-8")
        document = SourceDocument(logical_key="test", source_name="test.md", source_path=str(path),
            media_type="text/markdown", content_hash="a"*64, byte_size=20, status=DocumentStatus.ACTIVE)
        session.add(document)
        session.flush()
        section = DocumentSection(document_id=document.id, heading_path="Olasılık", content="Özgün kaynak.", sequence=1, line_start=1, line_end=2)
        session.add(section)
        session.commit()
        document_id = str(document.id)
    with TestClient(main.app) as client:
        yield client, factory, document_id, calls
    main.app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def test_turn_on_cached_reindex_and_turn_off_preserve_source(environment):
    client, factory, document_id, calls = environment
    for enabled in (True, True, False):
        response = client.post(f"/api/documents/{document_id}/index-job", json={"contextual_enrichment": enabled})
        assert response.status_code == 202
        job = client.get(f"/api/documents/{document_id}/index-job").json()
        assert job["status"] == "completed"
        with factory() as session:
            document = session.scalar(select(SourceDocument))
            assert document.contextual_enrichment is enabled
            assert document.status == DocumentStatus.ACTIVE
            assert Path(document.source_path).read_text(encoding="utf-8") == "Özgün kaynak."
            section = session.scalar(select(DocumentSection))
            assert section.content == "Özgün kaynak."
            assert section.search_context == "Koşullu olasılık konusu."
    assert calls.count("model") == 1


def test_failed_vector_write_does_not_change_setting_or_sql_source(environment, monkeypatch):
    client, factory, document_id, calls = environment
    def fail(*args):
        raise RuntimeError("Qdrant offline")
    monkeypatch.setattr(reindexing, "index_document", fail)
    assert client.post(f"/api/documents/{document_id}/index-job", json={"contextual_enrichment": True}).status_code == 202
    assert client.get(f"/api/documents/{document_id}/index-job").json()["status"] == "failed"
    with factory() as session:
        assert session.scalar(select(SourceDocument)).contextual_enrichment is False
        section = session.scalar(select(DocumentSection))
        assert section.content == "Özgün kaynak."
        assert section.search_context is None


def test_busy_job_rejects_duplicate_and_delete(environment):
    client, factory, document_id, calls = environment
    with factory() as session:
        document = session.scalar(select(SourceDocument))
        session.add(DocumentIndexJob(document_id=document.id, status="running", contextual_enrichment=True))
        session.commit()
    assert client.post(f"/api/documents/{document_id}/index-job", json={}).status_code == 409
    assert client.delete(f"/api/documents/{document_id}").status_code == 409
    assert client.post(f"/api/documents/{document_id}/reindex", json={}).status_code == 409


def test_legacy_reindex_regenerates_context(environment):
    client, factory, document_id, calls = environment
    response = client.post(f"/api/documents/{document_id}/reindex", json={"contextual_enrichment": True})
    assert response.status_code == 200
    assert response.json()["document"]["contextual_enrichment"] is True
    assert calls.count("model") == 1
    assert ("index", True) in calls


def test_rechunk_generates_context_for_new_sections_before_old_vector_cleanup(environment, monkeypatch):
    from types import SimpleNamespace
    client, factory, document_id, calls = environment
    with factory() as session:
        document = session.scalar(select(SourceDocument))
        document.contextual_enrichment = True
        session.commit()
        old_id = session.scalar(select(DocumentSection.id))
    def rechunk(session, document):
        old = session.get(DocumentSection, old_id)
        session.add(DocumentSection(document_id=document.id, heading_path=old.heading_path, content=old.content,
                                    sequence=1, line_start=1, line_end=2))
        session.delete(old)
        session.flush()
        return True, 1
    def cleanup(ids):
        assert ("index", True) in calls
        assert ids == [old_id]
        calls.append("cleanup")
    monkeypatch.setattr(main, "rechunk_document_sections", rechunk)
    monkeypatch.setattr(main, "get_vector_store", lambda: SimpleNamespace(delete_sections=cleanup))
    response = client.post(f"/api/documents/{document_id}/rechunk-reindex")
    assert response.status_code == 200
    with factory() as session:
        section = session.scalar(select(DocumentSection))
        assert section.id != old_id
        assert section.search_context == "Koşullu olasılık konusu."
    assert "cleanup" in calls


def test_interrupted_job_is_recoverable_after_server_restart(environment):
    client, factory, document_id, calls = environment
    with factory() as session:
        document = session.scalar(select(SourceDocument))
        session.add(DocumentIndexJob(document_id=document.id, status="running", contextual_enrichment=True))
        session.commit()
    with TestClient(main.app) as restarted:
        job = restarted.get(f"/api/documents/{document_id}/index-job").json()
        assert job["status"] == "failed"
        assert job["stage"] == "interrupted"
        assert restarted.post(f"/api/documents/{document_id}/index-job", json={}).status_code == 202


def test_model_change_rebuilds_context_instead_of_reusing_stale_cache(environment, monkeypatch):
    from dataclasses import replace
    from app.runtime_models import current_runtime_model_config
    client, factory, document_id, calls = environment
    client.post(f"/api/documents/{document_id}/index-job", json={"contextual_enrichment": True})
    config = current_runtime_model_config()
    monkeypatch.setattr(contextual_indexing, "current_runtime_model_config", lambda: replace(config, ollama_model="different-model", vllm_model="different-model"))
    client.post(f"/api/documents/{document_id}/index-job", json={"contextual_enrichment": True})
    assert calls.count("model") == 2


def test_unexpected_direct_reindex_error_releases_job_for_retry(environment, monkeypatch):
    client, factory, document_id, calls = environment
    def fail(*args):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(main, "index_document", fail)
    assert client.post(f"/api/documents/{document_id}/reindex", json={}).status_code == 503
    assert client.get(f"/api/documents/{document_id}/index-job").json()["status"] == "failed"
    monkeypatch.setattr(main, "index_document", lambda *args: 1)
    assert client.post(f"/api/documents/{document_id}/reindex", json={}).status_code == 200
