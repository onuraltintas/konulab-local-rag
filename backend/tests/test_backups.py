import tarfile

from fastapi.testclient import TestClient

from app import main
from app.main import app


def test_backup_contains_a_consistent_database_snapshot_and_manifest(tmp_path, monkeypatch) -> None:
    """Yedek, canlı SQLite dosyasını doğrudan kopyalamak yerine snapshot alır."""
    monkeypatch.setattr(main.settings, "storage_path", tmp_path / "sources")

    with TestClient(app) as client:
        created = client.post("/api/admin/backups")
        listed = client.get("/api/admin/backups")

    assert created.status_code == 201
    payload = created.json()
    assert payload["filename"].endswith(".tar.gz")
    assert payload["download_url"].endswith(payload["filename"])
    assert listed.status_code == 200
    assert payload["filename"] in {backup["filename"] for backup in listed.json()}

    archive_path = tmp_path / "backups" / payload["filename"]
    with tarfile.open(archive_path, "r:gz") as archive:
        assert {"learning-platform.db", "manifest.json"}.issubset(archive.getnames())

    with TestClient(app) as client:
        preview = client.get(f"/api/admin/backups/{payload['filename']}/restore-preview")
    assert preview.status_code == 200
    assert preview.json()["database_bytes"] > 0
    assert preview.json()["requires_reindex"] is True
