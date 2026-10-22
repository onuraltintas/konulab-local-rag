"""Kaynakları değiştirmeden, izlenebilir belge indeks yenilemesi."""
import threading
from uuid import uuid4

from sqlalchemy import select

from app.contextual_indexing import enrich_document
from app.db import SessionLocal
from app.indexing import index_document
from app.models import ApplicationErrorEvent, DocumentIndexJob, DocumentSection, DocumentStatus, SourceDocument

queue_lock = threading.RLock()
BUSY = {"queued", "running"}


def reserve_job(database, document, target, *, status="queued"):
    """Tek API sürecindeki tüm yeniden indeksleme girişlerini aynı kilitle korur."""
    job = database.get(DocumentIndexJob, document.id)
    if job and job.status in BUSY:
        raise ValueError("Bu doküman için indeks yenilemesi zaten sürüyor.")
    if job is None:
        job = DocumentIndexJob(document_id=document.id)
        database.add(job)
    job.contextual_enrichment = target
    job.status, job.stage, job.progress = status, "queued", 0
    job.message = "İndeks yenilemesi sıraya alındı. Kaynak ve öğrenme ilerlemesi korunur."
    database.commit()
    database.refresh(job)
    return job


def set_job_progress(document_id, stage, progress, message, status="running"):
    with SessionLocal() as database:
        job = database.get(DocumentIndexJob, document_id)
        if job:
            job.stage, job.progress, job.message, job.status = stage, progress, message, status
            database.commit()


async def run_index_job(document_id):
    with SessionLocal() as database:
        job = database.get(DocumentIndexJob, document_id)
        document = database.get(SourceDocument, document_id)
        if job is None or document is None or document.status != DocumentStatus.ACTIVE or job.status != "queued":
            return
        target = job.contextual_enrichment
        set_job_progress(document_id, "contextualizing", 5, "Arama bağlamı denetleniyor; mevcut kaynak kullanılabilir.")
        try:
            def progress(done, total):
                set_job_progress(document_id, "contextualizing", 5 + int(65 * done / max(1, total)),
                                 f"Arama bağlamı: {done}/{total}. Kaynak metni değişmiyor.")

            # Progress ayrı oturumdadır. Kaynak ayarı ve yeni bağlam ancak vektör
            # yazımı başarılı olduğunda kaydedilir; model çağrısında SQLite kilitlenmez.
            await enrich_document(database, document, progress, enabled=target, flush=False)
            set_job_progress(document_id, "vectorizing", 75, "Embedding ve Qdrant güncelleniyor.")
            document.contextual_enrichment = target
            index_document(database, document)
            sections = list(database.scalars(select(DocumentSection).where(DocumentSection.document_id == document_id)))
            used = sum(bool((s.search_context_metadata or {}).get("used_in_embedding")) for s in sections) if target else 0
            failed = sum((s.search_context_metadata or {}).get("status") == "failed" for s in sections) if target else 0
            job.status, job.stage, job.progress = "completed", "completed", 100
            job.message = f"İndeks yenilendi. Bağlam {'açık' if target else 'kapalı'}; {used}/{len(sections)} parçada kullanıldı; {failed} üretim hatası. Kaynak korundu."
            # Başarı durumu ve etkin ayar aynı SQLite işlemiyle kesinleşir.
            database.commit()
        except Exception as error:
            database.rollback()
            database.add(ApplicationErrorEvent(request_id=str(uuid4()), method="BACKGROUND",
                path=f"/api/documents/{document_id}/index-job", error_type=type(error).__name__[:128],
                summary="Belge indeksi yenilenemedi; önceki SQL ayarı korunuyor."))
            database.commit()
            set_job_progress(document_id, "failed", 0,
                "İndeks yenilenemedi. Önceki ayar ve kaynak metin korundu; tekrar deneyebilirsiniz.", "failed")
