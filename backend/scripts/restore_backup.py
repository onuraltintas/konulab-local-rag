"""Bakım modunda KonuLab yedeğini geri yükler ve Qdrant indeksini üretir.

Bu komut API kapalıyken çalıştırılır. Canlı SQLite ve kaynaklar önce aynı veri
dizininde ayrı bir emniyet kopyasına alınır; doğrulanmamış arşiv asla açılmaz.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db import SessionLocal
from app.indexing import index_document
from app.models import DocumentStatus, SourceDocument, SystemMaintenanceState
from app.vector_store import get_vector_store


def sqlite_path() -> Path:
    database = make_url(get_settings().database_url).database
    if not database or database == ":memory:":
        raise RuntimeError("Geri yükleme dosya tabanlı SQLite gerektirir.")
    return Path(database).expanduser().resolve()


def validate_archive(archive_path: Path) -> tuple[tarfile.TarFile, list[tarfile.TarInfo]]:
    archive = tarfile.open(archive_path, "r:gz")
    members = archive.getmembers()
    for member in members:
        member_path = PurePosixPath(member.name)
        if member_path.is_absolute() or ".." in member_path.parts or member.issym() or member.islnk():
            archive.close()
            raise RuntimeError("Yedek güvenli olmayan bir dosya yolu içeriyor.")
    names = {member.name for member in members if member.isfile()}
    if {"manifest.json", "learning-platform.db"} - names:
        archive.close()
        raise RuntimeError("Yedek manifest veya SQLite anlık görüntüsünü içermiyor.")
    manifest_member = next(member for member in members if member.name == "manifest.json")
    manifest_stream = archive.extractfile(manifest_member)
    try:
        manifest = json.loads((manifest_stream.read() if manifest_stream else b"{}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        archive.close()
        raise RuntimeError("Yedek manifest dosyası okunamadı.") from error
    if manifest.get("format_version") != 1:
        archive.close()
        raise RuntimeError("Desteklenmeyen yedek biçimi.")
    return archive, members


def snapshot_current_state(database_path: Path, sources_path: Path, safety_path: Path) -> None:
    safety_path.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(database_path) as source, sqlite3.connect(safety_path / "learning-platform.db") as snapshot:
        source.backup(snapshot)
    if sources_path.is_dir():
        shutil.copytree(sources_path, safety_path / "sources")


def ensure_integrity(candidate: Path) -> None:
    with sqlite3.connect(candidate) as database:
        result = database.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise RuntimeError("Yedek SQLite bütünlük denetiminden geçemedi.")


def replace_state(archive_path: Path, database_path: Path, sources_path: Path) -> Path:
    safety_path = database_path.parent / f"restore-safety-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    snapshot_current_state(database_path, sources_path, safety_path)
    temporary_path = Path(tempfile.mkdtemp(prefix="konulab-restore-", dir=database_path.parent))
    previous_sources = database_path.parent / f".sources-before-restore-{os.getpid()}"
    sources_swapped = False
    try:
        archive, members = validate_archive(archive_path)
        try:
            archive.extractall(temporary_path, members=members, filter="data")
        finally:
            archive.close()
        candidate_database = temporary_path / "learning-platform.db"
        ensure_integrity(candidate_database)
        candidate_sources = temporary_path / "sources"
        staged_database = database_path.parent / f".{database_path.name}.restore-{os.getpid()}"
        shutil.copy2(candidate_database, staged_database)
        staged_sources = database_path.parent / f".sources-restore-{os.getpid()}"
        if candidate_sources.is_dir():
            shutil.copytree(candidate_sources, staged_sources)
        else:
            staged_sources.mkdir()

        if sources_path.exists():
            os.replace(sources_path, previous_sources)
        os.replace(staged_sources, sources_path)
        sources_swapped = True
        os.replace(staged_database, database_path)
        shutil.rmtree(previous_sources, ignore_errors=True)
        return safety_path
    except Exception:
        if previous_sources.exists() and sources_swapped:
            shutil.rmtree(sources_path, ignore_errors=True)
            os.replace(previous_sources, sources_path)
        elif previous_sources.exists() and not sources_path.exists():
            os.replace(previous_sources, sources_path)
        raise
    finally:
        shutil.rmtree(temporary_path, ignore_errors=True)


def rebuild_qdrant() -> int:
    database = SessionLocal()
    try:
        store = get_vector_store()
        if store.client.collection_exists(store.collection_name):
            store.client.delete_collection(store.collection_name)
        documents = list(
            database.scalars(
                select(SourceDocument)
                .where(SourceDocument.status == DocumentStatus.ACTIVE)
                .order_by(SourceDocument.created_at)
            )
        )
        indexed_count = 0
        for document in documents:
            print(f"İndeksleniyor: {document.lesson_title or document.source_name}", flush=True)
            indexed_count += index_document(database, document)
            database.commit()
        maintenance = database.get(SystemMaintenanceState, 1)
        if maintenance is not None:
            maintenance.enabled = False
            maintenance.reason = None
            maintenance.started_at = None
            database.commit()
        return indexed_count
    except Exception:
        database.rollback()
        # Bakım kilidi açık kalır; yarım indeksle yazma işlemlerine izin verilmez.
        raise
    finally:
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="KonuLab yedeğini geri yükle ve Qdrant'ı yeniden indeksle.")
    parser.add_argument("--archive", required=True, type=Path, help="Doğrulanacak .tar.gz yedek dosyası")
    parser.add_argument("--confirm-offline-restore", action="store_true", help="API'nin durdurulduğunu onaylar")
    args = parser.parse_args()
    if not args.confirm_offline_restore:
        raise SystemExit("Koruma: API'yi durdurun ve --confirm-offline-restore ile yeniden çalıştırın.")
    archive_path = args.archive.expanduser().resolve()
    if not archive_path.is_file():
        raise SystemExit("Yedek dosyası bulunamadı.")

    settings = get_settings()
    database_path = sqlite_path()
    sources_path = settings.storage_path.resolve()
    if not database_path.is_file():
        raise SystemExit("Canlı SQLite dosyası bulunamadı.")
    safety_path = replace_state(archive_path, database_path, sources_path)
    print(f"SQLite ve kaynaklar geri yüklendi. Emniyet kopyası: {safety_path}", flush=True)
    section_count = rebuild_qdrant()
    print(f"Geri yükleme tamamlandı; {section_count} bölüm Qdrant'a yeniden indekslendi.", flush=True)


if __name__ == "__main__":
    main()
