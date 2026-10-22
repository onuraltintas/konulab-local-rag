import asyncio
import hashlib
import json
import mimetypes
import re
import shutil
import sqlite3
import tarfile
import tempfile
import zipfile
from io import BytesIO
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.chunking import parse_text_document, split_sections_by_tokens
from app.conversion import (
    SUPPORTED_CONVERTIBLE_SUFFIXES,
    ConversionQualityError,
    ConversionUnavailableError,
    convert_to_markdown,
)
from app.auth import hash_password, hash_session_token, new_session_token, verify_password
from app.config import get_settings
from app.table_repair import candidate_markdown, candidate_html, validate_against_source
from app.table_refinement import refine_table
from app.formula_validation import validate_pdf_formulas, formula_signature, formula_review_required, formula_crop
from app.db import Base, SessionLocal, engine, get_db, migrate_schema
from app.document_analysis import DocumentAnalysisUnavailableError, DocumentAnalyzer, SectionAnalysisInput, get_document_analyzer
from app.embeddings import EmbeddingUnavailableError, get_embedder, get_sparse_embedder
from app.indexing import index_document, rechunk_document_sections, remove_document_vectors
from app.contextual_indexing import enrich_document
from app.reindexing import BUSY, queue_lock, reserve_job, run_index_job
from app.llm import OllamaUnavailableError, get_ollama_client
from app.runtime_models import (
    ModelProviderError,
    RuntimeModelConfig,
    config_from_record,
    current_runtime_model_config,
    initialize_runtime_model_config,
    list_provider_models,
    normalize_base_url,
    set_runtime_model_config,
    validate_runtime_model_config,
)
from app.models import (
    AnalysisStatus,
    Conversation,
    ConversationMessage,
    DocumentAnalysis,
    DocumentIndexJob,
    DocumentAsset,
    DocumentConversion,
    DocumentMarkdownRevision,
    AssetAnalysisStatus,
    ConversionStatus,
    DocumentSection,
    DocumentStatus,
    LearnerFeedback,
    LearnerProfile,
    LearnerTopicProgress,
    LLMProviderType,
    ApplicationErrorEvent,
    SystemMaintenanceState,
    UserAccount,
    UserSession,
    RuntimeModelSettings,
    SectionTopicProposal,
    SourceDocument,
    Topic,
    TopicPrerequisite,
    TopicProposal,
    TopicSource,
)
from app.schemas import (
    AnalysisApprovalRequest,
    ReindexRequest,
    DocumentIndexJobResponse,
    BackupResponse,
    BulkDeleteResponse,
    DocumentBulkDeleteRequest,
    DocumentDeletionImpactResponse,
    DocumentAnalysisResponse,
    DocumentAssetResponse,
    DocumentConversionResponse,
    ConversionPublishRequest,
    MarkdownRevisionContentResponse,
    MarkdownRevisionResponse,
    MarkdownRevisionRestoreRequest,
    MarkdownSaveRequest,
    AssetBulkAnalysisRequest,
    DocumentResponse,
    DashboardReviewResponse,
    DashboardTopicResponse,
    FeedbackRequest,
    FeedbackResponse,
    FeedbackStateResponse,
    FeedbackReviewRequest,
    LearnerProfileResponse,
    LearnerDashboardResponse,
    ProfileBootstrapRequest,
    ProfileUpdateRequest,
    ChatRequest,
    ChatResponse,
    ChatSource,
    ConversationDetail,
    ConversationMessageResponse,
    ConversationSummary,
    HealthResponse,
    LessonMergePreviewRequest,
    LessonMergePreviewResponse,
    RuntimeResponse,
    RuntimeModelSettingsResponse,
    RuntimeModelSettingsUpdateRequest,
    RuntimeModelTestResponse,
    ProviderDiscoveryResponse,
    ProviderModelResponse,
    SystemStatusResponse,
    MaintenanceStateResponse,
    MaintenanceUpdateRequest,
    BackupRestorePreviewResponse,
    ApplicationErrorResponse,
    QualityFeedbackResponse,
    QualityOverviewResponse,
    QualityInsightsResponse,
    QualityIssueMetric,
    QualityExportResponse,
    SearchRequest,
    SearchResult,
    SectionResponse,
    SourceViewerResponse,
    SourceViewerSectionResponse,
    TopicStudyStepResponse,
    TopicQuizResponse,
    TopicResponse,
    TopicProgressPayload,
    TopicProgressUpdateRequest,
    TopicPrerequisiteUpdateRequest,
    TopicPrerequisiteSuggestion,
    TopicProposalResponse,
    UploadResponse,
    AuthLoginRequest,
    AuthRegisterRequest,
    AuthSessionResponse,
)
from app.vector_store import VectorStoreUnavailableError, get_vector_store


settings = get_settings()
SUPPORTED_SUFFIXES = {".txt", ".md", ".py"}
SAFE_KEY = re.compile(r"[^a-z0-9-]+")
TURKISH_TRANSLATION = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
SAFE_BACKUP_FILENAME = re.compile(r"^konulab-backup-[0-9]{8}T[0-9]{6}Z\.tar\.gz$")
SENSITIVE_ERROR_VALUE = re.compile(r"(?i)(password|token|api[_ -]?key|authorization|bearer)\s*[=:]\s*[^\s,;]+")
# UI'da rahat okunması için adım hedefi; bu bir kesme sınırı değildir. Uzun
# kaynaklar bütün parça sınırlarından bölünür ve sonraki öğrenme adımına taşınır.
STUDY_STEP_TARGET_CHARS = 6_000
VISUAL_APPENDIX_START = "<!-- konulab-visual-appendix:start -->"
VISUAL_APPENDIX_END = "<!-- konulab-visual-appendix:end -->"


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    migrate_schema()
    # Eski sürüm konu taslağını ancak iş sonunda oluşturuyordu. Mevcut taslakları
    # yeni ilerleme ekranında doğrudan onay bekliyor olarak göster.
    with SessionLocal() as database:
        initialize_runtime_model_config(database)
        for job in database.scalars(select(DocumentIndexJob).where(DocumentIndexJob.status.in_(BUSY))):
            job.status, job.stage = "failed", "interrupted"
            job.message = "Sunucu yeniden başladı; işlem yarım kaldı. Yeniden indekslemeyi tekrar başlatın."
        database.commit()
        legacy_analyses = list(
            database.scalars(select(DocumentAnalysis).where(DocumentAnalysis.status == AnalysisStatus.PENDING))
        )
        for analysis in legacy_analyses:
            analysis.status = AnalysisStatus.READY
            analysis.stage = "ready"
            analysis.progress = 100
            analysis.message = "Konu önerileri incelemeniz için hazır."
        if legacy_analyses:
            database.commit()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def current_maintenance_state(database: Session) -> SystemMaintenanceState:
    state = database.get(SystemMaintenanceState, 1)
    if state is None:
        state = SystemMaintenanceState(id=1, enabled=False)
        database.add(state)
        database.commit()
        database.refresh(state)
    return state


def safe_error_summary(error: Exception) -> str:
    """Hata kaydına tanı değeri bırakırken parola ve anahtarları saklamaz."""
    summary = SENSITIVE_ERROR_VALUE.sub(r"\1=[gizlendi]", str(error).replace("\n", " "))
    return summary[:1_000] or "İleti üretmeyen beklenmeyen hata"


@app.middleware("http")
async def maintenance_guard(request: Request, call_next):
    request.state.request_id = str(uuid4())
    exempt_write_paths = {"/api/admin/maintenance", "/api/admin/backups"}
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path not in exempt_write_paths:
        database = SessionLocal()
        try:
            maintenance = current_maintenance_state(database)
            if maintenance.enabled:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Sistem bakım modunda; geri yükleme tamamlanana kadar yazma işlemleri kapalı.", "request_id": request.state.request_id},
                    headers={"Retry-After": "60", "X-Request-Id": request.state.request_id},
                )
        finally:
            database.close()
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    return response


@app.exception_handler(Exception)
async def record_unhandled_error(request: Request, error: Exception) -> JSONResponse:
    """HTTPException dışındaki hataları kullanıcı ayrıntısı sızdırmadan kalıcılaştırır."""
    request_id = getattr(request.state, "request_id", str(uuid4()))
    try:
        database = SessionLocal()
        try:
            database.add(
                ApplicationErrorEvent(
                    request_id=request_id,
                    method=request.method,
                    path=request.url.path[:512],
                    error_type=type(error).__name__[:128],
                    summary=safe_error_summary(error),
                )
            )
            database.commit()
        finally:
            database.close()
    except Exception:
        # Hata günlüğü kullanılabilir değilse asıl hatayı maskelemeden istemciye
        # güvenli yanıt dönmeye devam ederiz.
        pass
    return JSONResponse(
        status_code=500,
        content={"detail": "Beklenmeyen bir sunucu hatası oluştu. Destek için istek kimliğini paylaşın.", "request_id": request_id},
        headers={"X-Request-Id": request_id},
    )


def normalize_logical_key(value: str) -> str:
    cleaned = SAFE_KEY.sub("-", value.lower()).strip("-")
    if not cleaned:
        raise HTTPException(status_code=422, detail="logical_key yalnızca anlamlı harf ve sayılar içermelidir.")
    return cleaned[:255]


def serialize_document(document: SourceDocument) -> DocumentResponse:
    return DocumentResponse.model_validate(document)


def serialize_topic_progress(progress: LearnerTopicProgress) -> TopicProgressPayload:
    return TopicProgressPayload(
        completed_step_ids=progress.completed_step_ids or [],
        review_plans=progress.review_plans or [],
        last_step_id=progress.last_step_id,
        last_visited_at=progress.last_visited_at,
        quiz_attempts=progress.quiz_attempts,
        last_quiz_score=progress.last_quiz_score,
        last_quiz_at=progress.last_quiz_at,
    )


def serialize_profile(profile: LearnerProfile) -> LearnerProfileResponse:
    return LearnerProfileResponse(
        id=profile.id,
        client_id=profile.client_id,
        display_name=profile.display_name,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        progress={str(item.topic_id): serialize_topic_progress(item) for item in profile.topic_progress},
    )


def require_profile(profile_id: UUID, client_id: str, database: Session) -> LearnerProfile:
    profile = database.get(LearnerProfile, profile_id)
    if profile is None or profile.client_id != client_id:
        raise HTTPException(status_code=404, detail="Yerel profil bulunamadı.")
    return profile


def optional_session_user(authorization: str | None, database: Session) -> UserAccount | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token_hash = hash_session_token(authorization.removeprefix("Bearer ").strip())
    session = database.scalar(select(UserSession).where(UserSession.token_hash == token_hash))
    expires_at = session.expires_at.replace(tzinfo=timezone.utc) if session and session.expires_at.tzinfo is None else session.expires_at if session else None
    if session is None or session.revoked_at is not None or expires_at <= datetime.now(timezone.utc):
        return None
    return session.user


def create_session_response(user: UserAccount, database: Session) -> AuthSessionResponse:
    from datetime import timedelta

    token = new_session_token()
    database.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_session_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    database.commit()
    return AuthSessionResponse(
        access_token=token, user_id=user.id, username=user.username, display_name=user.display_name
    )


def claim_local_profile(
    user: UserAccount, profile_id: UUID | None, client_id: str | None, database: Session
) -> None:
    if profile_id is None:
        return
    if client_id is None:
        raise HTTPException(status_code=422, detail="Yerel profil aktarımı için istemci kimliği gerekir.")
    profile = require_profile(profile_id, client_id, database)
    if profile.user_id is not None and profile.user_id != user.id:
        raise HTTPException(status_code=409, detail="Bu yerel profil başka bir hesaba bağlı.")
    profile.user_id = user.id


def serialize_conversation_message(message: ConversationMessage) -> ConversationMessageResponse:
    return ConversationMessageResponse(
        id=message.id,
        sequence=message.sequence,
        role=message.role,
        content=message.content,
        model=message.model,
        created_at=message.created_at,
        sources=[ChatSource.model_validate(source) for source in (message.source_snapshot or [])],
    )


def serialize_analysis(analysis: DocumentAnalysis) -> DocumentAnalysisResponse:
    proposals: list[TopicProposalResponse] = []
    for proposal in analysis.topic_proposals:
        assignments = proposal.section_proposals
        proposals.append(
            TopicProposalResponse(
                id=proposal.id,
                title=proposal.title,
                description=proposal.description,
                existing_topic_id=proposal.existing_topic_id,
                section_count=len(assignments),
                example_count=sum(item.content_type == "example" for item in assignments),
                code_count=sum(item.content_type == "code" for item in assignments),
                exercise_count=sum(item.content_type == "exercise" for item in assignments),
            )
        )
    return DocumentAnalysisResponse(
        document=serialize_document(analysis.document),
        model=analysis.model,
        suggested_lesson_title=analysis.suggested_lesson_title,
        status=analysis.status,
        stage=analysis.stage,
        progress=analysis.progress,
        message=analysis.message,
        error_message=analysis.error_message,
        topics=proposals,
    )


def serialize_document_asset(conversion: DocumentConversion, asset: DocumentAsset) -> DocumentAssetResponse:
    return DocumentAssetResponse(
        table_index=asset.table_index,
        table_repair_required=asset.table_repair_required,
        proposed_table=asset.proposed_table,
        table_validation=asset.table_validation,
        proposed_markdown=candidate_markdown(asset.proposed_table) if asset.proposed_table else None,
        id=asset.id,
        page_number=asset.page_number,
        kind=asset.kind,
        description=asset.description,
        confidence=asset.confidence,
        needs_review=asset.needs_review,
        url=f"/api/documents/{conversion.document_id}/assets/{asset.id}",
        analysis_status=asset.analysis_status,
        model=asset.model,
        message=asset.message,
        error_message=asset.error_message,
        structured_markdown=asset.structured_markdown,
        row_count=asset.row_count,
        column_count=asset.column_count,
        inclusion_status=asset.inclusion_status,
    )


def serialize_markdown_revision(revision: DocumentMarkdownRevision) -> MarkdownRevisionResponse:
    return MarkdownRevisionResponse(
        id=revision.id,
        revision_number=revision.revision_number,
        content_hash=revision.content_hash,
        byte_size=revision.byte_size,
        reason=revision.reason,
        created_at=revision.created_at,
    )


def normalized_markdown_bytes(markdown: str) -> bytes:
    """Sürüm karmaşı için platformdan bağımsız, tek son satırlı Markdown üretir."""
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"
    if not normalized.strip():
        raise ValueError("Markdown boş olamaz.")
    return normalized.encode("utf-8")


def ensure_markdown_revision_baseline(database: Session, conversion: DocumentConversion) -> None:
    """Eski kurulumlardan gelen tek dosyalı dönüşümlere geri yüklenebilir taban sürüm ekler."""
    if conversion.revisions:
        return
    if not conversion.markdown_path:
        # Yeni dönüşümde ilk sürüm henüz aşağıdaki create_markdown_revision
        # çağrısıyla yazılacaktır.
        return
    content_path = Path(conversion.markdown_path)
    content = content_path.read_bytes()
    content_hash = hashlib.sha256(content).hexdigest()
    if conversion.markdown_hash and content_hash != conversion.markdown_hash:
        raise ValueError("Markdown dosyasının bütünlük özeti değişmiş.")
    database.add(
        DocumentMarkdownRevision(
            conversion=conversion,
            revision_number=1,
            content_path=str(content_path),
            content_hash=content_hash,
            byte_size=len(content),
            reason="conversion",
        )
    )
    conversion.markdown_hash = content_hash
    database.flush()


def create_markdown_revision(
    database: Session,
    conversion: DocumentConversion,
    markdown: str,
    *,
    reason: str,
) -> DocumentMarkdownRevision:
    """Yeni içeriği eski dosyayı ezmeden kaydeder ve etkin sürümü değiştirir."""
    ensure_markdown_revision_baseline(database, conversion)
    markdown_bytes = normalized_markdown_bytes(markdown)
    content_hash = hashlib.sha256(markdown_bytes).hexdigest()
    revisions = sorted(conversion.revisions, key=lambda item: item.revision_number)
    if conversion.markdown_hash == content_hash and revisions:
        return revisions[-1]
    revision_number = (revisions[-1].revision_number + 1) if revisions else 1
    source_directory = Path(conversion.document.source_path).parent
    content_path = source_directory / f"markdown-v{conversion.document.version}-r{revision_number}-{content_hash[:12]}.md"
    content_path.write_bytes(markdown_bytes)
    revision = DocumentMarkdownRevision(
        conversion=conversion,
        revision_number=revision_number,
        content_path=str(content_path),
        content_hash=content_hash,
        byte_size=len(markdown_bytes),
        reason=reason[:64],
    )
    database.add(revision)
    conversion.markdown_path = str(content_path)
    conversion.markdown_hash = content_hash
    return revision


def serialize_conversion(conversion: DocumentConversion, *, include_markdown: bool = False) -> DocumentConversionResponse:
    """Dönüşüm dosya yolunu açmadan kullanıcıya güvenli inceleme verisi döndürür."""
    markdown: str | None = None
    if include_markdown and conversion.markdown_path and conversion.status in {
        ConversionStatus.READY,
        ConversionStatus.PUBLISHED,
    }:
        try:
            markdown = Path(conversion.markdown_path).read_text(encoding="utf-8")
        except OSError:
            # Bozuk ya da haricen taşınmış bir dosyayı sessizce "hazır" gibi
            # göstermeyelim; işçi sonraki denemede kaydı hataya alır.
            markdown = None
    revisions = sorted(conversion.revisions, key=lambda item: item.revision_number, reverse=True)
    quality_report = {}
    try:
        quality_path = Path(conversion.document.source_path + ".quality.json")
        if quality_path.is_file():
            quality_report = json.loads(quality_path.read_text(encoding="utf-8"))
            quality_report.pop("tables", None)  # HTML tabloları ZIP içinde sunulur.
    except (OSError, ValueError):
        quality_report = {"requires_review": True, "issues": [{"type": "quality_report_unavailable"}]}
    return DocumentConversionResponse(
        document=serialize_document(conversion.document),
        status=conversion.status,
        markdown=markdown,
        markdown_hash=conversion.markdown_hash,
        converter=conversion.converter,
        converter_version=conversion.converter_version,
        ocr_used=conversion.ocr_used,
        page_count=conversion.page_count,
        table_count=conversion.table_count,
        image_count=conversion.image_count,
        stage=conversion.stage,
        progress=conversion.progress,
        message=conversion.message,
        error_message=conversion.error_message,
        assets=[serialize_document_asset(conversion, asset) for asset in conversion.assets],
        current_revision_number=revisions[0].revision_number if revisions else 0,
        revisions=[serialize_markdown_revision(revision) for revision in revisions],
        quality_report=quality_report,
        formula_review_required=formula_review_required(quality_report, markdown) if markdown is not None else bool(quality_report.get("formulas")),
        formula_review_hash=conversion.formula_review_hash,
    )


def normalize_topic_slug(value: str, database: Session) -> str:
    base = normalize_logical_key(value.translate(TURKISH_TRANSLATION))[:110]
    candidate = base
    suffix = 2
    while database.scalar(select(Topic.id).where(Topic.slug == candidate)) is not None:
        candidate = f"{base[:100]}-{suffix}"
        suffix += 1
    return candidate


def normalize_lesson_title(value: str) -> str:
    """Kullanıcı yazımındaki boşluk/büyük-küçük farklarını aynı ders sayar."""
    return " ".join(value.split()).casefold()


def parse_document_for_embedding(text: str, suffix: str):
    """Kaynak parçalarını hem yapısal hem embedding-token sınırlarına uygun üretir."""
    parsed_sections = parse_text_document(text, suffix)
    try:
        embedder = get_embedder()
    except EmbeddingUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return split_sections_by_tokens(parsed_sections, embedder.tokenizer, embedder.passage_token_budget())


def matching_lesson_documents(database: Session, lesson_title: str, excluding_id: UUID) -> list[SourceDocument]:
    expected = normalize_lesson_title(lesson_title)
    candidates = list(
        database.scalars(
            select(SourceDocument).where(
                SourceDocument.status == DocumentStatus.ACTIVE,
                SourceDocument.lesson_title.is_not(None),
                SourceDocument.id != excluding_id,
            )
        )
    )
    return [document for document in candidates if normalize_lesson_title(document.lesson_title or "") == expected]


def lesson_topics(database: Session, documents: list[SourceDocument]) -> list[Topic]:
    if not documents:
        return []
    return list(
        database.scalars(
            select(Topic)
            .join(TopicSource)
            .join(DocumentSection)
            .where(DocumentSection.document_id.in_([document.id for document in documents]))
            .distinct()
        )
    )


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", app_name=settings.app_name)


def backup_directory() -> Path:
    path = settings.storage_path.parent / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sqlite_database_path() -> Path:
    """Canlı SQLite dosyasını, URL ayrıntılarını dışarı açmadan bulur."""
    database_url = make_url(settings.database_url)
    if database_url.get_backend_name() != "sqlite" or not database_url.database or database_url.database == ":memory:":
        raise HTTPException(status_code=409, detail="Yedekleme yalnızca dosya tabanlı SQLite kurulumunda kullanılabilir.")
    return Path(database_url.database).expanduser().resolve()


def create_backup_archive() -> BackupResponse:
    """SQLite anlık görüntüsünü ve ham kaynakları tek arşivde saklar.

    Qdrant türetilmiş bir indeks olduğundan arşive eklenmez. Geri yükleme sonrası
    ham kaynaklardan yeniden üretmek, sürüm uyuşmazlığı ve yarım kopya riskini
    ortadan kaldırır.
    """
    created_at = datetime.now(timezone.utc)
    filename = f"konulab-backup-{created_at.strftime('%Y%m%dT%H%M%SZ')}.tar.gz"
    archive_path = backup_directory() / filename
    database_path = sqlite_database_path()
    if not database_path.is_file():
        raise HTTPException(status_code=503, detail="SQLite veritabanı dosyası bulunamadı; yedek oluşturulamadı.")

    temporary_directory = Path(tempfile.mkdtemp(prefix="konulab-backup-", dir=backup_directory()))
    snapshot_path = temporary_directory / "learning-platform.db"
    try:
        with sqlite3.connect(database_path) as source, sqlite3.connect(snapshot_path) as snapshot:
            source.backup(snapshot)
        manifest_path = temporary_directory / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "created_at": created_at.isoformat(),
                    "contents": ["learning-platform.db", "sources/"],
                    "qdrant": "derived-index-rebuild-required",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(snapshot_path, arcname="learning-platform.db")
            archive.add(manifest_path, arcname="manifest.json")
            if settings.storage_path.is_dir():
                archive.add(settings.storage_path, arcname="sources", recursive=True)
    finally:
        shutil.rmtree(temporary_directory, ignore_errors=True)

    return BackupResponse(
        filename=filename,
        created_at=created_at,
        byte_size=archive_path.stat().st_size,
        download_url=f"/api/admin/backups/{filename}",
    )


@app.post("/api/admin/backups", response_model=BackupResponse, status_code=status.HTTP_201_CREATED)
def create_backup() -> BackupResponse:
    return create_backup_archive()


@app.get("/api/admin/backups", response_model=list[BackupResponse])
def list_backups() -> list[BackupResponse]:
    backups: list[BackupResponse] = []
    for archive_path in sorted(backup_directory().glob("konulab-backup-*.tar.gz"), reverse=True):
        if not SAFE_BACKUP_FILENAME.fullmatch(archive_path.name):
            continue
        created_at = datetime.fromtimestamp(archive_path.stat().st_mtime, tz=timezone.utc)
        backups.append(
            BackupResponse(
                filename=archive_path.name,
                created_at=created_at,
                byte_size=archive_path.stat().st_size,
                download_url=f"/api/admin/backups/{archive_path.name}",
            )
        )
    return backups


@app.get("/api/admin/backups/{filename}")
def download_backup(filename: str) -> FileResponse:
    if not SAFE_BACKUP_FILENAME.fullmatch(filename):
        raise HTTPException(status_code=404, detail="Yedek dosyası bulunamadı.")
    archive_path = backup_directory() / filename
    if not archive_path.is_file():
        raise HTTPException(status_code=404, detail="Yedek dosyası bulunamadı.")
    return FileResponse(archive_path, media_type="application/gzip", filename=filename)


def backup_restore_preview(filename: str) -> BackupRestorePreviewResponse:
    if not SAFE_BACKUP_FILENAME.fullmatch(filename):
        raise HTTPException(status_code=404, detail="Yedek dosyası bulunamadı.")
    archive_path = backup_directory() / filename
    if not archive_path.is_file():
        raise HTTPException(status_code=404, detail="Yedek dosyası bulunamadı.")
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError("Yedek geçersiz dosya yolu içeriyor.")
            database_member = next((member for member in members if member.name == "learning-platform.db"), None)
            manifest_member = next((member for member in members if member.name == "manifest.json"), None)
            if database_member is None or manifest_member is None or not database_member.isfile() or not manifest_member.isfile():
                raise ValueError("Yedekte gerekli SQLite veya manifest dosyası yok.")
            manifest_file = archive.extractfile(manifest_member)
            manifest = json.loads((manifest_file.read() if manifest_file else b"{}").decode("utf-8"))
            created_at = datetime.fromisoformat(str(manifest["created_at"]).replace("Z", "+00:00")) if manifest.get("created_at") else None
            source_files = [member for member in members if member.isfile() and member.name.startswith("sources/")]
            return BackupRestorePreviewResponse(
                filename=filename,
                created_at=created_at,
                database_bytes=database_member.size,
                source_file_count=len(source_files),
                source_bytes=sum(member.size for member in source_files),
            )
    except (OSError, tarfile.TarError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError) as error:
        raise HTTPException(status_code=422, detail=f"Yedek doğrulanamadı: {str(error)[:180]}") from error


@app.get("/api/admin/backups/{filename}/restore-preview", response_model=BackupRestorePreviewResponse)
def get_backup_restore_preview(filename: str) -> BackupRestorePreviewResponse:
    return backup_restore_preview(filename)


@app.get("/api/admin/maintenance", response_model=MaintenanceStateResponse)
def get_maintenance_state(database: Session = Depends(get_db)) -> MaintenanceStateResponse:
    state = current_maintenance_state(database)
    return MaintenanceStateResponse(enabled=state.enabled, reason=state.reason, started_at=state.started_at)


@app.put("/api/admin/maintenance", response_model=MaintenanceStateResponse)
def update_maintenance_state(
    request: MaintenanceUpdateRequest, database: Session = Depends(get_db)
) -> MaintenanceStateResponse:
    expected_confirmation = "BAKIM_MODUNU_AC" if request.enabled else "BAKIM_MODUNU_KAPAT"
    if request.confirmation != expected_confirmation:
        raise HTTPException(status_code=422, detail="Bakım modu için açık onay metni eşleşmiyor.")
    state = current_maintenance_state(database)
    state.enabled = request.enabled
    state.reason = request.reason.strip() if request.enabled and request.reason else None
    state.started_at = datetime.now(timezone.utc) if request.enabled else None
    database.commit()
    database.refresh(state)
    return MaintenanceStateResponse(enabled=state.enabled, reason=state.reason, started_at=state.started_at)


@app.get("/api/system/errors", response_model=list[ApplicationErrorResponse])
def list_application_errors(limit: int = 50, database: Session = Depends(get_db)) -> list[ApplicationErrorEvent]:
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="Kayıt sınırı 1 ile 200 arasında olmalıdır.")
    return list(database.scalars(select(ApplicationErrorEvent).order_by(ApplicationErrorEvent.created_at.desc()).limit(limit)))


@app.get("/api/system/status", response_model=SystemStatusResponse)
def system_status() -> SystemStatusResponse:
    """İstemcinin bağlantı hatasını anlaması için bağımlılıkların kısa durumu."""
    checked_at = datetime.now(timezone.utc)
    database_state: str = "ok"
    qdrant_state: str = "ok"
    try:
        with engine.connect() as connection:
            connection.execute(select(1))
    except Exception:
        database_state = "unavailable"
    try:
        get_vector_store().client.get_collections()
    except Exception:
        qdrant_state = "unavailable"

    latest_backup: BackupResponse | None = None
    backup_paths = [
        path
        for path in backup_directory().glob("konulab-backup-*.tar.gz")
        if SAFE_BACKUP_FILENAME.fullmatch(path.name)
    ]
    if backup_paths:
        latest_path = max(backup_paths, key=lambda path: path.stat().st_mtime)
        latest_backup = BackupResponse(
            filename=latest_path.name,
            created_at=datetime.fromtimestamp(latest_path.stat().st_mtime, tz=timezone.utc),
            byte_size=latest_path.stat().st_size,
            download_url=f"/api/admin/backups/{latest_path.name}",
        )
    status_value: Literal["ok", "degraded"] = "ok" if database_state == "ok" and qdrant_state == "ok" else "degraded"
    return SystemStatusResponse(
        checked_at=checked_at,
        status=status_value,
        database=database_state,
        qdrant=qdrant_state,
        latest_backup=latest_backup,
    )


@app.get("/api/runtime", response_model=RuntimeResponse)
def runtime_status() -> RuntimeResponse:
    """Çalışma zamanının GPU'yu gerçekten görüp görmediğini model yüklemeden gösterir."""
    cuda_available = False
    cuda_device_name = None
    try:
        import torch

        cuda_available = torch.cuda.is_available()
        if cuda_available:
            cuda_device_name = str(torch.cuda.get_device_name(0))
    except ImportError:
        pass
    runtime_models = current_runtime_model_config()
    return RuntimeResponse(
        embedding_model=settings.embedding_model,
        embedding_device=settings.embedding_device,
        cuda_available=cuda_available,
        cuda_device_name=cuda_device_name,
        qdrant_url=settings.qdrant_url,
        ollama_base_url=runtime_models.text_base_url,
        ollama_model=runtime_models.text_model,
    )


async def provider_discovery(provider, base_url: str) -> ProviderDiscoveryResponse:
    """Arayüzü, çalışmayan isteğe bağlı bir sağlayıcı yüzünden bozmaz."""
    try:
        models = await list_provider_models(provider, base_url)
        return ProviderDiscoveryResponse(
            provider=provider,
            base_url=base_url,
            reachable=True,
            models=[ProviderModelResponse(**item.__dict__) for item in models],
        )
    except ModelProviderError as error:
        return ProviderDiscoveryResponse(provider=provider, base_url=base_url, reachable=False, error=str(error))


async def serialize_runtime_model_settings(record: RuntimeModelSettings) -> RuntimeModelSettingsResponse:
    config = config_from_record(record)
    discoveries = await asyncio.gather(
        provider_discovery(LLMProviderType.OLLAMA, config.ollama_base_url),
        provider_discovery(LLMProviderType.VLLM, config.vllm_base_url),
    )
    return RuntimeModelSettingsResponse(
        text_provider=config.text_provider,
        ollama_base_url=config.ollama_base_url,
        ollama_model=config.ollama_model,
        vllm_base_url=config.vllm_base_url,
        vllm_model=config.vllm_model,
        vision_provider=config.vision_provider,
        vision_model=config.vision_model,
        context_length=config.context_length,
        embedding_model=settings.embedding_model,
        embedding_device=settings.embedding_device,
        providers=discoveries,
    )


@app.get("/api/settings/models", response_model=RuntimeModelSettingsResponse)
async def get_runtime_model_settings(database: Session = Depends(get_db)) -> RuntimeModelSettingsResponse:
    record = initialize_runtime_model_config(database)
    return await serialize_runtime_model_settings(record)


def runtime_config_from_request(request: RuntimeModelSettingsUpdateRequest) -> RuntimeModelConfig:
    return RuntimeModelConfig(
        text_provider=request.text_provider,
        ollama_base_url=normalize_base_url(request.ollama_base_url),
        ollama_model=request.ollama_model.strip(),
        vllm_base_url=normalize_base_url(request.vllm_base_url),
        vllm_model=request.vllm_model.strip() if request.vllm_model else None,
        vision_provider=request.vision_provider,
        vision_model=request.vision_model.strip(),
        context_length=request.context_length,
    )


@app.post("/api/settings/models/test", response_model=RuntimeModelTestResponse)
async def test_runtime_model_settings(request: RuntimeModelSettingsUpdateRequest) -> RuntimeModelTestResponse:
    try:
        config = runtime_config_from_request(request)
        await validate_runtime_model_config(config)
    except ModelProviderError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return RuntimeModelTestResponse(ok=True, message="Bağlantı ve seçili metin/görsel model listeleri doğrulandı.", text_model=config.text_model)


@app.put("/api/settings/models", response_model=RuntimeModelSettingsResponse)
async def update_runtime_model_settings(
    request: RuntimeModelSettingsUpdateRequest, database: Session = Depends(get_db)
) -> RuntimeModelSettingsResponse:
    try:
        config = runtime_config_from_request(request)
        await validate_runtime_model_config(config)
    except ModelProviderError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    record = initialize_runtime_model_config(database)
    record.text_provider = config.text_provider
    record.ollama_base_url = config.ollama_base_url
    record.ollama_model = config.ollama_model
    record.vllm_base_url = config.vllm_base_url
    record.vllm_model = config.vllm_model
    record.vision_provider = config.vision_provider
    record.vision_model = config.vision_model
    record.context_length = config.context_length
    database.commit()
    database.refresh(record)
    set_runtime_model_config(config)
    return await serialize_runtime_model_settings(record)


@app.get("/api/topics", response_model=list[TopicResponse])
def list_topics(database: Session = Depends(get_db)) -> list[Topic]:
    return list(database.scalars(select(Topic).order_by(Topic.parent_id, Topic.order_index)))


@app.get("/api/documents", response_model=list[DocumentResponse])
def list_documents(
    include_replaced: bool = False, database: Session = Depends(get_db)
) -> list[SourceDocument]:
    query = select(SourceDocument).order_by(SourceDocument.logical_key, SourceDocument.version.desc())
    if not include_replaced:
        query = query.where(SourceDocument.status.in_([DocumentStatus.ACTIVE, DocumentStatus.PROCESSING]))
    return list(database.scalars(query))


@app.get("/api/documents/{document_id}/sections", response_model=list[SectionResponse])
def list_document_sections(document_id: UUID, database: Session = Depends(get_db)) -> list[DocumentSection]:
    document = database.get(SourceDocument, document_id)
    if document is None or document.status != DocumentStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="Aktif doküman bulunamadı.")
    query = select(DocumentSection).where(DocumentSection.document_id == document_id).order_by(DocumentSection.sequence)
    return list(database.scalars(query))


@app.get("/api/sections/{section_id}/source-view", response_model=SourceViewerResponse)
def get_source_view(section_id: UUID, database: Session = Depends(get_db)) -> SourceViewerResponse:
    """Alıntının kendisini ve komşu parçalarını, satır aralıklarıyla döndürür.

    Tüm dokümanı istemciye göndermek yerine sınırlı komşuluk kullanılır. Bu hem
    uzun kaynaklarda arayüzü hızlı tutar hem de kullanıcının alıntının gerçek
    bağlamını doğrudan denetlemesine yeter.
    """
    target = database.get(DocumentSection, section_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Kaynak bölümü bulunamadı.")
    document = database.get(SourceDocument, target.document_id)
    if document is None or document.status != DocumentStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="Bu alıntının aktif kaynak sürümü bulunamadı.")
    sections = list(
        database.scalars(
            select(DocumentSection)
            .where(
                DocumentSection.document_id == document.id,
                DocumentSection.sequence.between(max(0, target.sequence - 2), target.sequence + 2),
            )
            .order_by(DocumentSection.sequence)
        )
    )
    conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document.id))
    return SourceViewerResponse(
        document_id=document.id,
        source_name=document.source_name,
        lesson_title=document.lesson_title,
        target_section_id=target.id,
        sections=[
            SourceViewerSectionResponse(
                id=section.id,
                heading_path=section.heading_path,
                content=section.content,
                content_type=section.content_type,
                sequence=section.sequence,
                line_start=section.line_start,
                line_end=section.line_end,
                is_focus=section.id == target.id,
            )
            for section in sections
        ],
        assets=[serialize_document_asset(conversion, asset) for asset in conversion.assets] if conversion else [],
    )


def build_topic_study_steps(topic_id: int, database: Session) -> list[TopicStudyStepResponse]:
    """Bir konunun onaylı kaynaklarını okunabilir, doküman sıralı öğrenme adımlarına dönüştürür."""
    topic = database.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Konu bulunamadı.")
    rows = list(
        database.execute(
            select(DocumentSection, SourceDocument)
            .join(TopicSource, TopicSource.section_id == DocumentSection.id)
            .join(SourceDocument, SourceDocument.id == DocumentSection.document_id)
            .where(TopicSource.topic_id == topic_id, TopicSource.approved.is_(True), SourceDocument.status == DocumentStatus.ACTIVE)
            .order_by(SourceDocument.lesson_title, SourceDocument.source_name, DocumentSection.sequence)
        )
    )
    grouped: dict[tuple[UUID, str, str], list[tuple[DocumentSection, SourceDocument]]] = {}
    for section, document in rows:
        grouped.setdefault((document.id, section.heading_path, section.content_type), []).append((section, document))

    steps: list[TopicStudyStepResponse] = []
    for members in grouped.values():
        first, document = members[0]
        full_content = "\n\n".join(section.content for section, _ in members)
        # Kapak, telif ve güncelleme bilgileri konuya atansa bile öğrenme adımı
        # değildir; ilk ekranda öğrenciyi bu metinle karşılamayalım.
        if first.heading_path.split(" > ")[-1].casefold() in {"başlangıç", "genel"} and re.search(
            r"eğitmen\s*:|son güncelleme|özet notlar", full_content, re.IGNORECASE
        ):
            continue
        heading = first.heading_path.split(" > ")[-1]
        if re.match(r"^(?:ders|lesson)\s*\d+\b", heading, re.IGNORECASE):
            heading = topic.title

        batches: list[list[tuple[DocumentSection, SourceDocument]]] = []
        batch: list[tuple[DocumentSection, SourceDocument]] = []
        batch_chars = 0
        for member in members:
            section_chars = len(member[0].content)
            if batch and batch_chars + section_chars > STUDY_STEP_TARGET_CHARS:
                batches.append(batch)
                batch = []
                batch_chars = 0
            batch.append(member)
            batch_chars += section_chars
        if batch:
            batches.append(batch)

        for batch_index, batch_members in enumerate(batches, start=1):
            batch_first, batch_document = batch_members[0]
            batch_content = "\n\n".join(section.content for section, _ in batch_members)
            step_heading = heading if len(batches) == 1 else f"{heading} · bölüm {batch_index}/{len(batches)}"
            steps.append(
                TopicStudyStepResponse(
                    id=batch_first.id,
                    section_ids=[section.id for section, _ in batch_members],
                    source_name=batch_document.lesson_title or batch_document.source_name,
                    heading=step_heading,
                    content=batch_content,
                    content_type=batch_first.content_type,
                    line_start=batch_first.line_start,
                    line_end=batch_members[-1][0].line_end,
                )
            )
    return steps


@app.get("/api/topics/{topic_id}/study", response_model=list[TopicStudyStepResponse])
def get_topic_study_steps(topic_id: int, database: Session = Depends(get_db)) -> list[TopicStudyStepResponse]:
    return build_topic_study_steps(topic_id, database)


@app.get("/api/topics/{topic_id}/prerequisites", response_model=list[TopicResponse])
def get_topic_prerequisites(topic_id: int, database: Session = Depends(get_db)) -> list[Topic]:
    if database.get(Topic, topic_id) is None:
        raise HTTPException(status_code=404, detail="Konu bulunamadı.")
    return list(
        database.scalars(
            select(Topic)
            .join(TopicPrerequisite, TopicPrerequisite.prerequisite_topic_id == Topic.id)
            .where(TopicPrerequisite.topic_id == topic_id)
            .order_by(Topic.order_index, Topic.title)
        )
    )


@app.post("/api/topics/{topic_id}/prerequisite-suggestions", response_model=list[TopicPrerequisiteSuggestion])
async def suggest_topic_prerequisites(topic_id: int, database: Session = Depends(get_db)) -> list[TopicPrerequisiteSuggestion]:
    target = database.get(Topic, topic_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Konu bulunamadı.")
    candidates = list(database.scalars(select(Topic).where(Topic.id != topic_id).order_by(Topic.order_index, Topic.title)))
    if not candidates:
        return []
    compact_candidates = [{"id": item.id, "title": item.title, "description": item.description} for item in candidates]
    try:
        raw = await get_ollama_client().suggest_prerequisites(
            {"id": target.id, "title": target.title, "description": target.description}, compact_candidates
        )
    except OllamaUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    by_id = {item.id: item for item in candidates}
    return [
        TopicPrerequisiteSuggestion(topic_id=item["topic_id"], title=by_id[item["topic_id"]].title, reason=item["reason"], confidence=item["confidence"])
        for item in raw
    ]


@app.put("/api/topics/{topic_id}/prerequisites", response_model=list[TopicResponse])
def update_topic_prerequisites(
    topic_id: int, request: TopicPrerequisiteUpdateRequest, database: Session = Depends(get_db)
) -> list[Topic]:
    if database.get(Topic, topic_id) is None:
        raise HTTPException(status_code=404, detail="Konu bulunamadı.")
    prerequisite_ids = set(request.prerequisite_topic_ids)
    if topic_id in prerequisite_ids:
        raise HTTPException(status_code=422, detail="Bir konu kendisinin ön koşulu olamaz.")
    existing_topics = {item.id for item in database.scalars(select(Topic).where(Topic.id.in_(prerequisite_ids)))} if prerequisite_ids else set()
    if existing_topics != prerequisite_ids:
        raise HTTPException(status_code=404, detail="Ön koşul olarak seçilen konulardan biri bulunamadı.")

    edges: dict[int, set[int]] = {}
    for edge in database.scalars(select(TopicPrerequisite)):
        edges.setdefault(edge.topic_id, set()).add(edge.prerequisite_topic_id)
    edges[topic_id] = prerequisite_ids

    def reaches_target(current: int, seen: set[int]) -> bool:
        if current == topic_id:
            return True
        if current in seen:
            return False
        return any(reaches_target(next_id, seen | {current}) for next_id in edges.get(current, set()))

    if any(reaches_target(prerequisite_id, set()) for prerequisite_id in prerequisite_ids):
        raise HTTPException(status_code=422, detail="Bu seçim konu haritasında döngü oluşturur.")
    database.query(TopicPrerequisite).filter(TopicPrerequisite.topic_id == topic_id).delete(synchronize_session=False)
    database.add_all(
        [TopicPrerequisite(topic_id=topic_id, prerequisite_topic_id=prerequisite_id) for prerequisite_id in prerequisite_ids]
    )
    database.commit()
    return list(database.scalars(select(Topic).where(Topic.id.in_(prerequisite_ids)).order_by(Topic.order_index, Topic.title)))


@app.post("/api/topics/{topic_id}/quiz", response_model=TopicQuizResponse)
async def generate_topic_quiz(topic_id: int, database: Session = Depends(get_db)) -> TopicQuizResponse:
    """Konuya dengeli dağılmış kaynak adımlarından üç soruluk mini quiz üretir."""
    topic = database.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Konu bulunamadı.")
    steps = build_topic_study_steps(topic_id, database)
    if not steps:
        raise HTTPException(status_code=422, detail="Bu konu için quiz üretecek onaylı kaynak bulunamadı.")

    # Çok uzun konularda bağlam penceresini taşırmadan başlangıç, orta ve son
    # bölümlerden eşit aralıklı adımlar seçilir. Quiz yine yalnızca bu açık
    # kaynaklara dayanır ve her sorunun geri dönüş adımı doğrulanır.
    sample_count = min(len(steps), 8)
    sample_indices = sorted({round(index * (len(steps) - 1) / max(sample_count - 1, 1)) for index in range(sample_count)})
    sampled_steps = [steps[index] for index in sample_indices]
    try:
        raw_quiz = await get_ollama_client().generate_topic_quiz(topic.title, sampled_steps)
        return TopicQuizResponse(topic_id=topic_id, questions=raw_quiz["questions"])
    except OllamaUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def pinned_study_sources(request: ChatRequest, database: Session) -> list[SearchResult]:
    """Açık öğrenme adımının bütün kaynak parçalarını, sırasını koruyarak döndürür."""
    if not request.pinned_section_ids:
        return []
    if request.topic_id is None:
        raise HTTPException(status_code=422, detail="Sabitlenmiş bir öğrenme adımı için konu seçilmelidir.")

    rows = list(
        database.execute(
            select(DocumentSection, SourceDocument)
            .join(TopicSource, TopicSource.section_id == DocumentSection.id)
            .join(SourceDocument, SourceDocument.id == DocumentSection.document_id)
            .where(
                DocumentSection.id.in_(request.pinned_section_ids),
                TopicSource.topic_id == request.topic_id,
                TopicSource.approved.is_(True),
                SourceDocument.status == DocumentStatus.ACTIVE,
            )
        )
    )
    by_id = {section.id: (section, document) for section, document in rows}
    if len(by_id) != len(request.pinned_section_ids):
        raise HTTPException(status_code=422, detail="Açık öğrenme adımının kaynakları artık kullanılamıyor.")

    return [
        SearchResult(
            section_id=section.id,
            document_id=document.id,
            score=1.0,
            source_name=document.lesson_title or document.source_name,
            logical_key=document.logical_key,
            heading_path=section.heading_path,
            content_type=section.content_type,
            line_start=section.line_start,
            line_end=section.line_end,
            content=section.content,
        )
        for section_id in request.pinned_section_ids
        for section, document in [by_id[section_id]]
    ]


@app.post("/api/search", response_model=list[SearchResult])
def semantic_search(request: SearchRequest, database: Session = Depends(get_db)) -> list[SearchResult]:
    """Yalnızca SQL tarafında hâlâ aktif olan kaynak bölümlerini döndürür.

    Bu ikinci kontrol, Qdrant temizleme işi gecikse bile yer değiştirmiş eski
    doküman sürümünün kullanıcıya veya sonraki sohbet katmanına sızmasını önler.
    """
    try:
        if request.topic_id is not None and database.get(Topic, request.topic_id) is None:
            raise HTTPException(status_code=404, detail="Konu bulunamadı.")
        query_vector = get_embedder().embed_query(request.query)
        sparse_query = get_sparse_embedder().embed_query(request.query)
        hits = get_vector_store().search(
            query_vector,
            sparse_query.indices,
            sparse_query.values,
            limit=request.limit * 4,
            topic_id=request.topic_id,
        )
    except (EmbeddingUnavailableError, VectorStoreUnavailableError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    if not hits:
        return []
    section_ids = [hit.section_id for hit in hits]
    active_sections = {
        section.id: section
        for section in database.scalars(
            select(DocumentSection)
            .join(SourceDocument)
            .where(
                DocumentSection.id.in_(section_ids),
                SourceDocument.status == DocumentStatus.ACTIVE,
            )
        )
    }
    results: list[SearchResult] = []
    for hit in hits:
        section = active_sections.get(hit.section_id)
        if section is None:
            continue
        results.append(
            SearchResult(
                section_id=section.id,
                document_id=section.document_id,
                score=hit.score,
                source_name=str(hit.payload["source_name"]),
                logical_key=str(hit.payload["logical_key"]),
                heading_path=section.heading_path,
                content_type=section.content_type,
                line_start=section.line_start,
                line_end=section.line_end,
                content=section.content,
            )
        )
        if len(results) == request.limit:
            break
    return results


@app.post("/api/chat", response_model=ChatResponse)
async def source_grounded_chat(
    request: ChatRequest, authorization: str | None = Header(default=None), database: Session = Depends(get_db)
) -> ChatResponse:
    """Qdrant kaynaklarıyla sınırlandırılmış Ollama yanıtı üretir."""
    user = optional_session_user(authorization, database)
    if request.topic_id is not None and database.get(Topic, request.topic_id) is None:
        raise HTTPException(status_code=404, detail="Konu bulunamadı.")
    if request.conversation_id is not None:
        conversation = database.get(Conversation, request.conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Sohbet bulunamadı.")
        if conversation.user_id != (user.id if user else None):
            raise HTTPException(status_code=403, detail="Bu sohbet başka bir kullanıcıya ait.")
        if request.topic_id is not None and conversation.topic_id != request.topic_id:
            raise HTTPException(status_code=409, detail="Sohbet farklı bir konuya ait.")
    else:
        conversation = Conversation(title=request.message.strip()[:255], topic_id=request.topic_id, user_id=user.id if user else None)
        database.add(conversation)
        database.flush()

    history = [
        (message.role, message.content)
        for message in database.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation.id)
            .order_by(ConversationMessage.sequence.desc())
            .limit(6)
        )
    ][::-1]
    sources = (
        pinned_study_sources(request, database)
        if request.pinned_section_ids
        else semantic_search(
            SearchRequest(query=request.message, topic_id=request.topic_id, limit=request.source_limit),
            database,
        )
    )
    if not sources:
        raise HTTPException(
            status_code=422,
            detail="Bu soruyu cevaplamak için aktif ders dokümanlarında yeterli kaynak bulunamadı.",
    )
    try:
        answer = await get_ollama_client().answer(
            request.message, sources, history=history, learning_mode=request.learning_mode
        )
    except OllamaUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    last_sequence = database.scalar(
        select(func.max(ConversationMessage.sequence)).where(ConversationMessage.conversation_id == conversation.id)
    )
    next_sequence = (last_sequence or 0) + 1
    source_snapshot = [
        ChatSource(
            index=index,
            section_id=str(source.section_id),
            source_name=source.source_name,
            heading_path=source.heading_path,
            line_start=source.line_start,
            line_end=source.line_end,
            score=source.score,
        ).model_dump(mode="json")
        for index, source in enumerate(sources, start=1)
    ]
    database.add_all(
        [
            ConversationMessage(
                conversation_id=conversation.id,
                sequence=next_sequence,
                role="user",
                content=request.message,
            ),
            ConversationMessage(
                conversation_id=conversation.id,
                sequence=next_sequence + 1,
                role="assistant",
                content=answer,
                model=current_runtime_model_config().text_model,
                source_snapshot=source_snapshot,
            ),
        ]
    )
    conversation.updated_at = datetime.now(timezone.utc)
    database.commit()
    return ChatResponse(
        conversation_id=conversation.id,
        answer=answer,
        model=current_runtime_model_config().text_model,
        sources=[ChatSource.model_validate(source) for source in source_snapshot],
    )


@app.post("/api/profiles/bootstrap", response_model=LearnerProfileResponse)
def bootstrap_profile(request: ProfileBootstrapRequest, database: Session = Depends(get_db)) -> LearnerProfileResponse:
    """Tarayıcı profilini bulur; ilk açılışta localStorage ilerlemesini SQLite'a taşır."""
    profile = database.scalar(select(LearnerProfile).where(LearnerProfile.client_id == request.client_id))
    is_new_profile = profile is None
    if profile is None:
        profile = LearnerProfile(client_id=request.client_id)
        database.add(profile)
        database.flush()

    # İlk aktarım dışında SQLite kaynak kaynaktır. Böylece bayat tarayıcı
    # verisi sonraki açılışlarda tamamlanmış tekrarları geri getiremez.
    if is_new_profile:
        for raw_topic_id, payload in request.local_progress.items():
            try:
                topic_id = int(raw_topic_id)
            except (TypeError, ValueError):
                continue
            if database.get(Topic, topic_id) is None:
                continue
            database.add(
                LearnerTopicProgress(
                    profile_id=profile.id,
                    topic_id=topic_id,
                    completed_step_ids=payload.completed_step_ids,
                    review_plans=payload.review_plans,
                    last_step_id=payload.last_step_id,
                    last_visited_at=payload.last_visited_at,
                    quiz_attempts=payload.quiz_attempts,
                    last_quiz_score=payload.last_quiz_score,
                    last_quiz_at=payload.last_quiz_at,
                )
            )
    database.commit()
    database.refresh(profile)
    return serialize_profile(profile)


@app.patch("/api/profiles/{profile_id}", response_model=LearnerProfileResponse)
def update_profile(
    profile_id: UUID, request: ProfileUpdateRequest, database: Session = Depends(get_db)
) -> LearnerProfileResponse:
    profile = require_profile(profile_id, request.client_id, database)
    profile.display_name = request.display_name.strip()
    if not profile.display_name:
        raise HTTPException(status_code=422, detail="Profil adı boş olamaz.")
    database.commit()
    database.refresh(profile)
    return serialize_profile(profile)


@app.put("/api/profiles/{profile_id}/progress/{topic_id}", response_model=TopicProgressPayload)
def save_topic_progress(
    profile_id: UUID,
    topic_id: int,
    request: TopicProgressUpdateRequest,
    database: Session = Depends(get_db),
) -> TopicProgressPayload:
    profile = require_profile(profile_id, request.client_id, database)
    if database.get(Topic, topic_id) is None:
        raise HTTPException(status_code=404, detail="Konu bulunamadı.")
    progress = database.scalar(
        select(LearnerTopicProgress).where(
            LearnerTopicProgress.profile_id == profile.id,
            LearnerTopicProgress.topic_id == topic_id,
        )
    )
    if progress is None:
        progress = LearnerTopicProgress(profile_id=profile.id, topic_id=topic_id)
        database.add(progress)
    progress.completed_step_ids = request.completed_step_ids
    progress.review_plans = request.review_plans
    progress.last_step_id = request.last_step_id
    progress.last_visited_at = request.last_visited_at
    progress.quiz_attempts = request.quiz_attempts
    progress.last_quiz_score = request.last_quiz_score
    progress.last_quiz_at = request.last_quiz_at
    database.commit()
    database.refresh(progress)
    return serialize_topic_progress(progress)


@app.get("/api/profiles/{profile_id}/dashboard", response_model=LearnerDashboardResponse)
def get_learner_dashboard(
    profile_id: UUID, client_id: str, database: Session = Depends(get_db)
) -> LearnerDashboardResponse:
    """Profil ilerlemesini, yalnızca aktif ve onaylı öğrenme kaynaklarıyla özetler."""
    profile = require_profile(profile_id, client_id, database)
    now = int(datetime.now(timezone.utc).timestamp() * 1_000)
    active_topics = list(
        database.scalars(
            select(Topic)
            .join(TopicSource, TopicSource.topic_id == Topic.id)
            .join(DocumentSection, DocumentSection.id == TopicSource.section_id)
            .join(SourceDocument, SourceDocument.id == DocumentSection.document_id)
            .where(TopicSource.approved.is_(True), SourceDocument.status == DocumentStatus.ACTIVE)
            .distinct()
            .order_by(Topic.order_index, Topic.title)
        )
    )
    progress_by_topic = {item.topic_id: item for item in profile.topic_progress}
    dashboard_topics: list[DashboardTopicResponse] = []
    reviews: list[DashboardReviewResponse] = []
    for topic in active_topics:
        steps = build_topic_study_steps(topic.id, database)
        available_step_ids = {str(step.id) for step in steps}
        progress = progress_by_topic.get(topic.id)
        completed_ids = set(progress.completed_step_ids or []) if progress else set()
        completed_count = len(completed_ids & available_step_ids)
        review_plans = progress.review_plans or [] if progress else []
        is_complete = bool(steps) and completed_count == len(steps) and not review_plans
        is_in_progress = bool(progress and progress.last_visited_at and not is_complete)
        topic_summary = DashboardTopicResponse(
            topic_id=topic.id,
            title=topic.title,
            completed_steps=completed_count,
            total_steps=len(steps),
            completion_percent=round((completed_count / len(steps)) * 100) if steps else 0,
            review_count=len(review_plans),
            last_quiz_score=progress.last_quiz_score if progress else None,
            last_quiz_at=progress.last_quiz_at if progress else 0,
            is_complete=is_complete,
            is_in_progress=is_in_progress,
        )
        dashboard_topics.append(topic_summary)
        for plan in review_plans:
            due_at = plan.get("dueAt")
            step_id = plan.get("stepId")
            heading = plan.get("heading")
            if isinstance(due_at, int) and isinstance(step_id, str) and isinstance(heading, str) and due_at <= now:
                reviews.append(
                    DashboardReviewResponse(
                        topic_id=topic.id,
                        topic_title=topic.title,
                        step_id=step_id,
                        heading=heading,
                        due_at=due_at,
                        overdue=due_at < now - 86_400_000,
                    )
                )
    in_progress = sorted(
        (item for item in dashboard_topics if item.is_in_progress),
        key=lambda item: progress_by_topic[item.topic_id].last_visited_at,
        reverse=True,
    )[:8]
    recent_quizzes = sorted(
        (item for item in dashboard_topics if item.last_quiz_at),
        key=lambda item: item.last_quiz_at,
        reverse=True,
    )[:5]
    return LearnerDashboardResponse(
        display_name=profile.display_name,
        total_topics=len(dashboard_topics),
        completed_topics=sum(item.is_complete for item in dashboard_topics),
        in_progress_topics=sum(item.is_in_progress for item in dashboard_topics),
        total_completed_steps=sum(item.completed_steps for item in dashboard_topics),
        total_steps=sum(item.total_steps for item in dashboard_topics),
        today_reviews=sorted(reviews, key=lambda item: item.due_at),
        in_progress=in_progress,
        recent_quizzes=recent_quizzes,
    )


@app.post("/api/auth/register", response_model=AuthSessionResponse, status_code=status.HTTP_201_CREATED)
def register_account(request: AuthRegisterRequest, database: Session = Depends(get_db)) -> AuthSessionResponse:
    username = request.username.casefold()
    if database.scalar(select(UserAccount).where(UserAccount.username == username)) is not None:
        raise HTTPException(status_code=409, detail="Bu kullanıcı adı zaten kullanılıyor.")
    user = UserAccount(
        username=username,
        display_name=request.display_name.strip(),
        password_hash=hash_password(request.password),
    )
    if not user.display_name:
        raise HTTPException(status_code=422, detail="Görünen ad boş olamaz.")
    database.add(user)
    database.flush()
    claim_local_profile(user, request.profile_id, request.client_id, database)
    return create_session_response(user, database)


@app.post("/api/auth/login", response_model=AuthSessionResponse)
def login_account(request: AuthLoginRequest, database: Session = Depends(get_db)) -> AuthSessionResponse:
    user = database.scalar(select(UserAccount).where(UserAccount.username == request.username.casefold()))
    if user is None or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Kullanıcı adı veya parola hatalı.")
    claim_local_profile(user, request.profile_id, request.client_id, database)
    return create_session_response(user, database)


@app.get("/api/auth/me", response_model=AuthSessionResponse)
def get_current_account(
    authorization: str | None = Header(default=None), database: Session = Depends(get_db)
) -> AuthSessionResponse:
    user = optional_session_user(authorization, database)
    if user is None:
        raise HTTPException(status_code=401, detail="Geçerli oturum bulunamadı.")
    # İstemci bu alanı yalnızca kullanıcı bilgisini yenilemek için kullanır;
    # yeni erişim anahtarı üretmez.
    return AuthSessionResponse(access_token="", user_id=user.id, username=user.username, display_name=user.display_name)


@app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_account(authorization: str | None = Header(default=None), database: Session = Depends(get_db)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        return
    token_hash = hash_session_token(authorization.removeprefix("Bearer ").strip())
    session = database.scalar(select(UserSession).where(UserSession.token_hash == token_hash))
    if session is not None:
        session.revoked_at = datetime.now(timezone.utc)
        database.commit()


@app.post("/api/feedback", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
def record_feedback(request: FeedbackRequest, database: Session = Depends(get_db)) -> FeedbackResponse:
    """Anonim yerel geri bildirimi kalite değerlendirmesinde kullanılmak üzere saklar."""
    if request.topic_id is not None and database.get(Topic, request.topic_id) is None:
        raise HTTPException(status_code=404, detail="Konu bulunamadı.")
    profile_id = None
    if request.profile_id is not None:
        profile_id = require_profile(request.profile_id, request.client_id, database).id
    feedback = LearnerFeedback(
        profile_id=profile_id,
        client_id=request.client_id,
        kind=request.kind,
        topic_id=request.topic_id,
        conversation_id=request.conversation_id,
        source_section_id=request.source_section_id,
        payload=request.payload,
    )
    database.add(feedback)
    database.commit()
    return FeedbackResponse(id=feedback.id)


@app.get("/api/feedback/state", response_model=FeedbackStateResponse)
def get_feedback_state(
    client_id: str = Query(min_length=8, max_length=64),
    profile_id: UUID | None = None,
    database: Session = Depends(get_db),
) -> FeedbackStateResponse:
    """Yalnızca aynı yerel istemcinin önceki oylarını öğrenme ekranına döndürür."""
    query = select(LearnerFeedback).where(LearnerFeedback.client_id == client_id).order_by(LearnerFeedback.created_at.desc()).limit(500)
    if profile_id is not None:
        require_profile(profile_id, client_id, database)
        query = query.where(LearnerFeedback.profile_id == profile_id)
    else:
        query = query.where(LearnerFeedback.profile_id.is_(None))
    keys: list[str] = []
    for feedback in database.scalars(query):
        if feedback.kind in {"answer_helpful", "answer_unhelpful"} and feedback.conversation_id:
            state = "helpful" if feedback.kind == "answer_helpful" else "unhelpful"
            keys.append(f"answer:{feedback.conversation_id}:{state}")
        elif feedback.kind == "source_mismatch" and feedback.source_section_id:
            keys.append(f"source:{feedback.source_section_id}")
    return FeedbackStateResponse(keys=list(dict.fromkeys(keys)))


def serialize_quality_feedback(
    feedback: LearnerFeedback,
    topic_title: str | None,
    heading_path: str | None,
    source_name: str | None,
) -> QualityFeedbackResponse:
    return QualityFeedbackResponse(
        id=feedback.id,
        kind=feedback.kind,
        topic_id=feedback.topic_id,
        topic_title=topic_title,
        source_section_id=feedback.source_section_id,
        source_name=source_name,
        heading_path=heading_path,
        conversation_id=feedback.conversation_id,
        payload=feedback.payload,
        review_status=feedback.review_status,
        review_note=feedback.review_note,
        created_at=feedback.created_at,
        reviewed_at=feedback.reviewed_at,
    )


@app.get("/api/quality/overview", response_model=QualityOverviewResponse)
def get_quality_overview(database: Session = Depends(get_db)) -> QualityOverviewResponse:
    status_counts = dict(
        database.execute(select(LearnerFeedback.review_status, func.count()).group_by(LearnerFeedback.review_status)).all()
    )
    kind_counts = dict(database.execute(select(LearnerFeedback.kind, func.count()).group_by(LearnerFeedback.kind)).all())
    return QualityOverviewResponse(
        new_count=status_counts.get("new", 0),
        reviewed_count=status_counts.get("reviewed", 0),
        ignored_count=status_counts.get("ignored", 0),
        by_kind=kind_counts,
    )


@app.get("/api/quality/insights", response_model=QualityInsightsResponse)
def get_quality_insights(database: Session = Depends(get_db)) -> QualityInsightsResponse:
    issue_kinds = {"answer_unhelpful", "quiz_question_issue", "source_mismatch"}
    topic_rows = database.execute(
        select(Topic.id, Topic.title, func.count())
        .join(LearnerFeedback, LearnerFeedback.topic_id == Topic.id)
        .where(LearnerFeedback.kind.in_(issue_kinds), LearnerFeedback.review_status != "ignored")
        .group_by(Topic.id, Topic.title).order_by(func.count().desc()).limit(8)
    ).all()
    source_rows = database.execute(
        select(DocumentSection.id, SourceDocument.source_name, DocumentSection.heading_path, func.count())
        .join(LearnerFeedback, LearnerFeedback.source_section_id == DocumentSection.id)
        .join(SourceDocument, SourceDocument.id == DocumentSection.document_id)
        .where(LearnerFeedback.kind.in_(issue_kinds), LearnerFeedback.review_status != "ignored")
        .group_by(DocumentSection.id, SourceDocument.source_name, DocumentSection.heading_path)
        .order_by(func.count().desc()).limit(8)
    ).all()
    reviewed_count = database.scalar(
        select(func.count()).select_from(LearnerFeedback).where(
            LearnerFeedback.review_status == "reviewed", LearnerFeedback.kind.in_(issue_kinds)
        )
    ) or 0
    return QualityInsightsResponse(
        reviewed_candidate_count=reviewed_count,
        topic_issues=[QualityIssueMetric(key=str(row[0]), label=row[1], count=row[2]) for row in topic_rows],
        source_issues=[QualityIssueMetric(key=str(row[0]), label=f"{row[1]} · {row[2]}", count=row[3]) for row in source_rows],
    )


@app.post("/api/quality/export-reviewed", response_model=QualityExportResponse)
def export_reviewed_feedback(database: Session = Depends(get_db)) -> QualityExportResponse:
    issue_kinds = {"answer_unhelpful", "quiz_question_issue", "source_mismatch"}
    rows = database.execute(
        select(LearnerFeedback, Topic.title)
        .outerjoin(Topic, Topic.id == LearnerFeedback.topic_id)
        .where(LearnerFeedback.review_status == "reviewed", LearnerFeedback.kind.in_(issue_kinds))
        .order_by(LearnerFeedback.reviewed_at.desc())
    ).all()
    generated_at = datetime.now(timezone.utc)
    filename = f"feedback-candidates-{generated_at.strftime('%Y%m%d-%H%M%S')}.json"
    export_dir = settings.storage_path.parent / "evaluations"
    export_dir.mkdir(parents=True, exist_ok=True)
    output = export_dir / filename
    candidates = [{
        "feedback_id": str(feedback.id), "kind": feedback.kind, "topic": topic_title,
        "conversation_id": str(feedback.conversation_id) if feedback.conversation_id else None,
        "source_section_id": str(feedback.source_section_id) if feedback.source_section_id else None,
        "payload": feedback.payload, "review_note": feedback.review_note,
        "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
        "reviewed_at": feedback.reviewed_at.isoformat() if feedback.reviewed_at else None,
    } for feedback, topic_title in rows]
    output.write_text(json.dumps({"generated_at": generated_at.isoformat(), "candidate_count": len(candidates), "candidates": candidates}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return QualityExportResponse(candidate_count=len(candidates), filename=filename, download_url=f"/api/quality/exports/{filename}")


@app.get("/api/quality/exports/{filename}")
def download_quality_export(filename: str) -> FileResponse:
    if not re.fullmatch(r"feedback-candidates-\d{8}-\d{6}\.json", filename):
        raise HTTPException(status_code=404, detail="Dışa aktarma dosyası bulunamadı.")
    output = settings.storage_path.parent / "evaluations" / filename
    if not output.is_file():
        raise HTTPException(status_code=404, detail="Dışa aktarma dosyası bulunamadı.")
    return FileResponse(output, media_type="application/json", filename=filename)


@app.get("/api/quality/feedback", response_model=list[QualityFeedbackResponse])
def list_quality_feedback(
    review_status: str = "new", limit: int = 100, database: Session = Depends(get_db)
) -> list[QualityFeedbackResponse]:
    if review_status not in {"new", "reviewed", "ignored", "all"}:
        raise HTTPException(status_code=422, detail="Geçersiz kalite inceleme durumu.")
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="Kayıt sınırı 1 ile 200 arasında olmalıdır.")
    query = (
        select(LearnerFeedback, Topic.title, DocumentSection.heading_path, SourceDocument.source_name)
        .outerjoin(Topic, Topic.id == LearnerFeedback.topic_id)
        .outerjoin(DocumentSection, DocumentSection.id == LearnerFeedback.source_section_id)
        .outerjoin(SourceDocument, SourceDocument.id == DocumentSection.document_id)
        .order_by(LearnerFeedback.created_at.desc())
        .limit(limit)
    )
    if review_status != "all":
        query = query.where(LearnerFeedback.review_status == review_status)
    return [
        serialize_quality_feedback(feedback, topic_title, heading_path, source_name)
        for feedback, topic_title, heading_path, source_name in database.execute(query)
    ]


@app.patch("/api/quality/feedback/{feedback_id}", response_model=QualityFeedbackResponse)
def review_quality_feedback(
    feedback_id: UUID, request: FeedbackReviewRequest, database: Session = Depends(get_db)
) -> QualityFeedbackResponse:
    feedback = database.get(LearnerFeedback, feedback_id)
    if feedback is None:
        raise HTTPException(status_code=404, detail="Geri bildirim bulunamadı.")
    feedback.review_status = request.review_status
    feedback.review_note = request.review_note.strip() if request.review_note else None
    feedback.reviewed_at = datetime.now(timezone.utc) if request.review_status != "new" else None
    database.commit()
    database.refresh(feedback)
    topic_title = database.scalar(select(Topic.title).where(Topic.id == feedback.topic_id)) if feedback.topic_id else None
    source_row = database.execute(
        select(DocumentSection.heading_path, SourceDocument.source_name)
        .join(SourceDocument, SourceDocument.id == DocumentSection.document_id)
        .where(DocumentSection.id == feedback.source_section_id)
    ).first() if feedback.source_section_id else None
    return serialize_quality_feedback(
        feedback,
        topic_title,
        source_row[0] if source_row else None,
        source_row[1] if source_row else None,
    )


@app.get("/api/conversations", response_model=list[ConversationSummary])
def list_conversations(
    topic_id: int | None = None, authorization: str | None = Header(default=None), database: Session = Depends(get_db)
) -> list[Conversation]:
    user = optional_session_user(authorization, database)
    query = select(Conversation).where(Conversation.user_id == (user.id if user else None)).order_by(Conversation.updated_at.desc())
    if topic_id is not None:
        query = query.where(Conversation.topic_id == topic_id)
    return list(database.scalars(query))


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: UUID, authorization: str | None = Header(default=None), database: Session = Depends(get_db)
) -> ConversationDetail:
    conversation = database.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Sohbet bulunamadı.")
    user = optional_session_user(authorization, database)
    if conversation.user_id != (user.id if user else None):
        raise HTTPException(status_code=403, detail="Bu sohbet başka bir kullanıcıya ait.")
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        topic_id=conversation.topic_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[serialize_conversation_message(message) for message in conversation.messages],
    )


async def create_topic_proposals(document_id: UUID) -> None:
    """Uzun süren LLM adımını istekten ayırır; arayüz bu kaydı anlık sorgular."""
    database = SessionLocal()
    try:
        analysis = database.scalar(select(DocumentAnalysis).where(DocumentAnalysis.document_id == document_id))
        document = database.get(SourceDocument, document_id)
        if analysis is None or document is None or analysis.status != AnalysisStatus.PROCESSING:
            return

        analysis.stage = "analyzing"
        analysis.progress = 35
        analysis.message = "Yerel model konu başlıklarını ve örnekleri ilişkilendiriyor."
        conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
        if conversion is not None:
            conversion.stage = "topic_analysis"
            conversion.message = "Onaylanan Markdown'dan konu başlıkları hazırlanıyor."
        database.commit()

        sections = list(
            database.scalars(
                select(DocumentSection)
                .where(DocumentSection.document_id == document.id)
                .order_by(DocumentSection.sequence)
            )
        )
        suggestions = await get_document_analyzer().suggest_topics(
            [
                SectionAnalysisInput(
                    id=section.id,
                    heading_path=section.heading_path,
                    content=section.content,
                    content_type=section.content_type,
                )
                for section in sections
            ]
        )

        analysis.progress = 72
        analysis.message = "Konu taslağından ders adı önerisi hazırlanıyor."
        database.commit()
        analysis.suggested_lesson_title = await get_document_analyzer().suggest_lesson_title(suggestions)

        analysis.stage = "saving"
        analysis.progress = 85
        analysis.message = "Konu önerileri kaydediliyor."
        database.commit()
        existing_topics = {topic.title.casefold(): topic for topic in database.scalars(select(Topic))}
        for order_index, suggestion in enumerate(suggestions, start=1):
            proposal = TopicProposal(
                analysis_id=analysis.id,
                title=suggestion.title,
                description=suggestion.description,
                existing_topic_id=(existing_topics.get(suggestion.title.casefold()) or None).id
                if existing_topics.get(suggestion.title.casefold())
                else None,
                order_index=order_index,
            )
            database.add(proposal)
            database.flush()
            for section_id in suggestion.section_ids:
                database.add(
                    SectionTopicProposal(
                        section_id=section_id,
                        topic_proposal_id=proposal.id,
                        content_type=suggestion.section_types[section_id],
                        confidence=85,
                    )
                )
        analysis.status = AnalysisStatus.READY
        analysis.stage = "ready"
        analysis.progress = 100
        analysis.message = "Konu önerileri incelemeniz için hazır."
        if conversion is not None:
            conversion.stage = "topic_review"
            conversion.message = "Markdown yayınlandı; konu önerileri incelemeniz için hazır."
        database.commit()
    except DocumentAnalysisUnavailableError as error:
        database.rollback()
        analysis = database.scalar(select(DocumentAnalysis).where(DocumentAnalysis.document_id == document_id))
        document = database.get(SourceDocument, document_id)
        if analysis is not None:
            analysis.status = AnalysisStatus.FAILED
            analysis.stage = "failed"
            analysis.message = "Konu taslağı üretilemedi; yerel model yanıtı gerekli."
            analysis.error_message = str(error)[:1_000]
        if document is not None:
            document.status = DocumentStatus.FAILED
        conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
        if conversion is not None:
            conversion.stage = "failed"
            conversion.message = "Markdown'dan konu taslağı üretilemedi."
        database.commit()
    except Exception as error:
        database.rollback()
        analysis = database.scalar(select(DocumentAnalysis).where(DocumentAnalysis.document_id == document_id))
        document = database.get(SourceDocument, document_id)
        if analysis is not None:
            analysis.status = AnalysisStatus.FAILED
            analysis.stage = "failed"
            analysis.message = "Konu analizi tamamlanamadı."
            analysis.error_message = str(error)[:1_000]
        if document is not None:
            document.status = DocumentStatus.FAILED
        conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
        if conversion is not None:
            conversion.stage = "failed"
            conversion.message = "Markdown'dan konu analizi tamamlanamadı."
        database.commit()
    finally:
        database.close()


def convert_document_to_markdown(document_id: UUID) -> None:
    """Dönüşüm işçisinin kalıcı durum güncellemeleri.

    Bu işçi yalnızca Markdown üretir. Bölümlere ayırma ve LLM konu analizi,
    kullanıcının açık yayın onayından sonra başlatılır.
    """
    database = SessionLocal()
    try:
        conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
        document = database.get(SourceDocument, document_id)
        if conversion is None or document is None or conversion.status != ConversionStatus.PROCESSING:
            return
        conversion.stage = "converting"
        conversion.progress = 15
        conversion.message = "Belge yapısı ve tablolar Markdown'a dönüştürülüyor."
        database.commit()

        converted = convert_to_markdown(Path(document.source_path))
        if Path(document.source_path).suffix.lower() == ".pdf" and converted.quality_report.get("formulas"):
            conversion.stage = "formula_validation"
            conversion.progress = 70
            conversion.message = "Formüller kaynak PDF ve bağımsız görsel okumayla karşılaştırılıyor."
            database.commit()
            def formula_progress(done, total):
                conversion.progress = 70 + int(12 * done / max(1, total))
                conversion.message = f"Formül kaynak kontrolü: {done}/{total}."
                database.commit()
            asyncio.run(validate_pdf_formulas(Path(document.source_path), converted.quality_report, get_ollama_client(), formula_progress))
        converted.quality_report["formula_signature"] = formula_signature(converted.markdown)
        for suffix, payload in ((".structure.json", converted.source_structure), (".quality.json", converted.quality_report)):
            Path(document.source_path + suffix).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if converted.source_pages:
            page_directory = Path(document.source_path + ".pages")
            page_directory.mkdir(parents=True, exist_ok=True)
            for number, content in enumerate(converted.source_pages, start=1):
                (page_directory / f"page-{number:04}.png").write_bytes(content)
        conversion.stage = "saving"
        conversion.progress = 85
        conversion.message = "Markdown inceleme sürümü güvenle kaydediliyor."
        database.commit()

        markdown_hash = hashlib.sha256(normalized_markdown_bytes(converted.markdown)).hexdigest()
        markdown = converted.markdown
        saved_assets = []

        # Varlıklar orijinal ve Markdown sürümüyle aynı yaşam döngüsündedir.
        # İçeriğe dayalı dosya adı, aynı görselin sürüm içinde yeniden yazılmasını
        # önler ve yedek/geri yüklemede izlenebilirlik sağlar.
        asset_directory = Path(document.source_path).parent / f"assets-v{document.version}-{markdown_hash[:12]}"
        for position, asset in enumerate(converted.assets, start=1):
            asset_hash = hashlib.sha256(asset.content).hexdigest()
            asset_path = asset_directory / f"{asset.kind}-{position}-{asset_hash[:12]}.png"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_bytes(asset.content)
            saved_asset = DocumentAsset(
                    conversion_id=conversion.id,
                    page_number=asset.page_number,
                    kind=asset.kind,
                    asset_path=str(asset_path),
                    content_hash=asset_hash,
                    structured_markdown=asset.structured_markdown,
                    row_count=asset.row_count,
                    column_count=asset.column_count,
                    table_index=asset.table_index,
                    table_repair_required=any(table.get("index") == asset.table_index and table.get("source_image_fallback") for table in converted.quality_report.get("tables", [])) if asset.kind == "table" else False,
                )
            database.add(saved_asset)
            saved_assets.append(saved_asset)

        database.flush()
        # Görsel bağlantıları açıklama onayından bağımsız olarak kaynak yerinde
        # bulunur; açıklama daha sonra aynı belirtece eklenir.
        for asset in saved_assets if len(saved_assets) == converted.image_count + converted.table_count else []:
            if asset.kind == "picture":
                marker = f"<!-- konulab-asset:{asset.id} -->"
                path = f"./{Path(asset.asset_path).parent.name}/{Path(asset.asset_path).name}"
                block = marker + f"\n\n![Dokümandan çıkarılan görsel]({path})"
                if "<!-- image -->" in markdown:
                    markdown = markdown.replace("<!-- image -->", block, 1)
        for index, asset in enumerate(item for item in saved_assets if item.kind == "table" and len(saved_assets) == converted.image_count + converted.table_count):
            marker = f"<!-- konulab-table:{index} -->"
            path = f"./{Path(asset.asset_path).parent.name}/{Path(asset.asset_path).name}"
            markdown = markdown.replace(marker, marker + f"\n\n![Kaynak tablo]({path})", 1)
        create_markdown_revision(database, conversion, markdown, reason="conversion")

        conversion.converter = converted.converter
        conversion.converter_version = converted.converter_version
        conversion.ocr_used = converted.ocr_used
        conversion.page_count = converted.page_count
        conversion.table_count = converted.table_count
        conversion.image_count = converted.image_count
        automatic_tables = [asset for asset in saved_assets if asset.table_repair_required]
        if automatic_tables:
            conversion.stage = "table_repair"
            conversion.progress = 90
            conversion.message = f"Şüpheli tablolar görsel modelle sırayla okunuyor: 0/{len(automatic_tables)}."
            for asset in automatic_tables:
                asset.analysis_status = AssetAnalysisStatus.PROCESSING
                asset.message = "Otomatik tablo çıkarımı sırasında bekliyor."
            database.commit()
            for number, asset in enumerate(automatic_tables, start=1):
                describe_document_asset(document_id, asset.id)
                database.expire_all()
                conversion = database.get(DocumentConversion, conversion.id)
                conversion.progress = 90 + int(9 * number / len(automatic_tables))
                conversion.message = f"Görsel model tablo kontrolü: {number}/{len(automatic_tables)} tamamlandı."
                database.commit()
        conversion.status = ConversionStatus.READY
        conversion.stage = "review"
        conversion.progress = 100
        issue_count = len(converted.quality_report.get("issues", []))
        conversion.message = f"Markdown hazır. {issue_count} bölüm kaynak kontrolü gerektiriyor." if issue_count else "Markdown hazır. İnceleyip öğrenme haritasına dönüştürmeyi onaylayabilirsiniz."
        if automatic_tables:
            conversion.message = f"{len(automatic_tables)} şüpheli tablo görsel modelle işlendi. Tablo önerilerini inceleyip kabul edin veya özgün tabloyu koruyun."
        conversion.error_message = None
        conversion.completed_at = datetime.now(timezone.utc)
        database.commit()
    except (ConversionUnavailableError, ConversionQualityError) as error:
        database.rollback()
        conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
        document = database.get(SourceDocument, document_id)
        if conversion is not None:
            conversion.status = ConversionStatus.FAILED
            conversion.stage = "failed"
            conversion.progress = 100
            conversion.message = "Belge dönüşümü tamamlanamadı."
            conversion.error_message = str(error)[:1_000]
        if document is not None:
            document.status = DocumentStatus.FAILED
        database.commit()
    except Exception as error:
        database.rollback()
        conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
        document = database.get(SourceDocument, document_id)
        if conversion is not None:
            conversion.status = ConversionStatus.FAILED
            conversion.stage = "failed"
            conversion.progress = 100
            conversion.message = "Belge dönüşümünde beklenmeyen bir hata oluştu."
            conversion.error_message = str(error)[:1_000]
        if document is not None:
            document.status = DocumentStatus.FAILED
        database.commit()
    finally:
        database.close()


def markdown_with_visual_appendix(markdown: str, assets: list[DocumentAsset]) -> str:
    """Görsel açıklamalarını Docling'in özgün konum belirteçlerine bağlar."""
    base = re.sub(
        rf"\n?{re.escape(VISUAL_APPENDIX_START)}.*?{re.escape(VISUAL_APPENDIX_END)}\n?",
        "\n",
        markdown,
        flags=re.DOTALL,
    ).rstrip()
    pictures = [asset for asset in assets if asset.kind == "picture"]
    for asset in pictures:
        marker = f"<!-- konulab-asset:{asset.id} -->"
        if marker not in base:
            if "<!-- image -->" in base:
                base = base.replace("<!-- image -->", marker, 1)
            else:
                # Bazı dönüştürücüler görsel belirteci üretmez; açıklamayı
                # kaybetmek yerine güvenli bir son ekle yine görünür kıl.
                base = f"{base.rstrip()}\n\n{marker}"
    for asset in pictures:
        marker = f"<!-- konulab-asset:{asset.id} -->"
        relative_asset_path = f"./{Path(asset.asset_path).parent.name}/{Path(asset.asset_path).name}"
        description_block = f"\n\n![Dokümandan çıkarılan görsel]({relative_asset_path})"
        if asset.inclusion_status == "accepted" and asset.analysis_status == AssetAnalysisStatus.READY and asset.description:
            review_note = " Belirsizlik olduğu için görselden doğrulayın." if asset.needs_review else ""
            description_block += f"\n\n> Görsel açıklaması: {asset.description.strip()}{review_note}"
        # Açıklama bloğu, Docling konum belirtecinin hemen sonrasındadır.
        # Önceki kabul denemesinin bloğu varsa yalnız o belirteçte yenilenir.
        existing = re.escape(marker) + r"(?:\n\n!\[Dokümandan çıkarılan görsel\]\([^\n]+\))?(?:\n\n> Görsel açıklaması:[^\n]*)?"
        base = re.sub(existing, lambda _: marker + description_block, base, count=1)
    described = []
    for asset in assets:
        if asset.kind == "table" and getattr(asset, "table_repair_required", False) and asset.table_index is not None:
            index = asset.table_index
            start, end = f"<!-- konulab-table-body:{index}:start -->", f"<!-- konulab-table-body:{index}:end -->"
            body = asset.structured_markdown or ""
            if asset.inclusion_status == "accepted" and asset.proposed_table:
                body = candidate_markdown(asset.proposed_table)
                body += "\n\n> Görsel modelden yeniden çıkarıldı; kullanıcı tarafından kabul edildi."
                if asset.needs_review:
                    body += " Kaynak eşleşmesi tam doğrulanamadı; kaynak görseli esas alınmalıdır."
            elif asset.inclusion_status == "rejected":
                body += "\n\n> Kullanıcı özgün tabloyu korumayı seçti."
            framed = re.escape(start) + r".*?" + re.escape(end)
            if re.search(framed, base, flags=re.DOTALL):
                base = re.sub(framed, lambda _: f"{start}\n\n{body}\n\n{end}", base, count=1, flags=re.DOTALL)
            # Eski belgede güvenilir konum belirteci yoksa yanlış tabloyu değiştirme.
            continue
        if asset.kind != "table" or asset.inclusion_status != "accepted" or asset.analysis_status != AssetAnalysisStatus.READY or not asset.description:
            continue
        if asset.structured_markdown and asset.structured_markdown in base:
            note = f"\n\n> Tablo açıklaması: {asset.description.strip()}"
            base = base.replace(asset.structured_markdown, asset.structured_markdown + note, 1)
        else:
            described.append(asset)
    if not described:
        return base + "\n"
    entries: list[str] = []
    for asset in described:
        title = f"{asset.page_number}. sayfa · " if asset.page_number is not None else ""
        title += "Tablo"
        review_note = "\n\n> Not: Bu açıklama model tarafından belirsiz olarak işaretlendi; görselden doğrulayın." if asset.needs_review else ""
        entries.append(f"### {title}\n\n{asset.description.strip()}{review_note}")
    return f"{base}\n\n{VISUAL_APPENDIX_START}\n\n## Görsel ekler\n\n" + "\n\n".join(entries) + f"\n\n{VISUAL_APPENDIX_END}\n"


def persist_visual_appendix(database: Session, conversion: DocumentConversion, assets: list[DocumentAsset]) -> None:
    """VLM ekini eski sürümü ezmeden ayrı bir Markdown revizyonu olarak kaydeder."""
    if not conversion.markdown_path:
        raise FileNotFoundError("Markdown inceleme dosyası bulunamadı.")
    if not conversion.markdown_path or not Path(conversion.markdown_path).is_file():
        raise HTTPException(status_code=409, detail="Markdown dosyası bulunamadı.")
    markdown_path = Path(conversion.markdown_path)
    markdown = markdown_path.read_text(encoding="utf-8")
    updated_markdown = markdown_with_visual_appendix(markdown, assets)
    create_markdown_revision(database, conversion, updated_markdown, reason="visual_appendix")


def describe_document_asset(document_id: UUID, asset_id: UUID, refine_only: bool = False) -> None:
    """Seçilen görseli açıklar veya şüpheli tabloyu onaya sunmak üzere yeniden çıkarır."""
    database = SessionLocal()
    try:
        conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
        asset = database.get(DocumentAsset, asset_id)
        if (
            conversion is None
            or asset is None
            or asset.conversion_id != conversion.id
            or (conversion.status != ConversionStatus.READY and not (conversion.status == ConversionStatus.PROCESSING and asset.table_repair_required))
            or asset.analysis_status != AssetAnalysisStatus.PROCESSING
        ):
            return
        asset_path = Path(asset.asset_path)
        if not asset_path.is_file():
            raise FileNotFoundError("Görsel dosyası bulunamadı.")
        asset.message = "Görsel model kaynağı inceliyor."
        database.commit()
        if asset.table_repair_required:
            result = asset.proposed_table if refine_only and asset.proposed_table else asyncio.run(get_ollama_client().reconstruct_table(asset_path.read_bytes(), asset.page_number))
            quality_path = Path(conversion.document.source_path + ".quality.json")
            quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.is_file() else {}
            source = next((table.get("validation", {}).get("source_text", "") for table in quality.get("tables", []) if table.get("index") == asset.table_index), "")
            initial_validation = validate_against_source(result, source, quality.get("font_aliases", {}))
            if refine_only or initial_validation["status"] != "passed":
                def refinement_progress(message):
                    asset.message = message
                    database.commit()
                result = asyncio.run(refine_table(result, Path(conversion.document.source_path), asset.table_index, source, quality.get("font_aliases", {}), get_ollama_client(), asset_path.parent / f"refinement-{asset.id}-{uuid4().hex[:8]}", refinement_progress))
            asset.proposed_table = result
            asset.table_validation = validate_against_source(result, source, quality.get("font_aliases", {}))
            result["needs_review"] = result["needs_review"] or asset.table_validation["status"] != "passed"
            asset.inclusion_status = "pending"
        else:
            result = asyncio.run(get_ollama_client().describe_visual_asset(asset_path.read_bytes(), asset.kind, asset.page_number))
        asset.inclusion_status = "pending"
        asset.description = str(result["description"])
        asset.confidence = int(result["confidence"])
        asset.needs_review = bool(result["needs_review"])
        asset.analysis_status = AssetAnalysisStatus.READY
        asset.model = current_runtime_model_config().vision_model
        asset.message = "Tablo yeniden çıkarıldı; doğrulama sonucunu inceleyip onaylayın." if asset.table_repair_required else "Görsel açıklaması hazır; kaynak konumunda eklemek için onaylayın."
        asset.error_message = None
        asset.analyzed_at = datetime.now(timezone.utc)
        # Üretim yalnızca bir öneridir. Önceden kabul edilmiş Markdown dahi
        # yeni model yanıtıyla kullanıcı onayı olmadan değiştirilmez.
        database.commit()
    except (OllamaUnavailableError, OSError, ValueError) as error:
        database.rollback()
        asset = database.get(DocumentAsset, asset_id)
        if asset is not None:
            asset.analysis_status = AssetAnalysisStatus.FAILED
            asset.message = "Görsel açıklaması tamamlanamadı."
            asset.error_message = str(error)[:1_000]
            database.commit()
    except Exception as error:
        database.rollback()
        asset = database.get(DocumentAsset, asset_id)
        if asset is not None:
            asset.analysis_status = AssetAnalysisStatus.FAILED
            asset.message = "Görsel açıklamasında beklenmeyen bir hata oluştu."
            asset.error_message = str(error)[:1_000]
            database.commit()
    finally:
        database.close()


@app.get("/api/documents/{document_id}/conversion", response_model=DocumentConversionResponse)
def get_document_conversion(document_id: UUID, database: Session = Depends(get_db)) -> DocumentConversionResponse:
    conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
    if conversion is None:
        raise HTTPException(status_code=404, detail="Bu doküman için dönüşüm kaydı bulunamadı.")
    return serialize_conversion(conversion, include_markdown=True)


@app.get("/api/documents/{document_id}/formulas/{formula_index}/image")
def get_formula_source_image(document_id: UUID, formula_index: int, database: Session = Depends(get_db)):
    document = database.get(SourceDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Doküman bulunamadı.")
    if Path(document.source_path).suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail="Bu belge için PDF formül kırpımı bulunmuyor.")
    try:
        quality = json.loads(Path(document.source_path + ".quality.json").read_text(encoding="utf-8"))
        formulas = quality.get("formulas", [])
        if formula_index < 0 or formula_index >= len(formulas):
            raise ValueError("Formül bulunamadı.")
        image, _ = formula_crop(Path(document.source_path), formulas[formula_index])
        return Response(content=image, media_type="image/png", headers={"Cache-Control": "no-store"})
    except (OSError, ValueError, KeyError, IndexError):
        raise HTTPException(status_code=404, detail="Formülün kaynak görüntüsü alınamadı.")


@app.put("/api/documents/{document_id}/conversion/markdown", response_model=DocumentConversionResponse)
def save_document_conversion_markdown(
    document_id: UUID,
    request: MarkdownSaveRequest,
    database: Session = Depends(get_db),
) -> DocumentConversionResponse:
    """İnceleme Markdown'ını iyimser eşzamanlılıkla yeni, geri alınabilir sürüm olarak saklar."""
    conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
    if conversion is None:
        raise HTTPException(status_code=404, detail="Bu doküman için dönüşüm kaydı bulunamadı.")
    if conversion.status != ConversionStatus.READY:
        raise HTTPException(status_code=409, detail="Markdown yalnızca yayın öncesi inceleme aşamasında düzenlenebilir.")
    if any(asset.analysis_status == AssetAnalysisStatus.PROCESSING for asset in conversion.assets):
        raise HTTPException(status_code=409, detail="Görsel açıklaması sürerken Markdown kaydedilemez.")
    if not conversion.markdown_hash or request.expected_markdown_hash != conversion.markdown_hash:
        raise HTTPException(status_code=409, detail="Markdown başka bir işlemde değişti. Güncel sürümü yükleyip değişikliklerinizi yeniden uygulayın.")
    try:
        create_markdown_revision(database, conversion, request.markdown, reason="user_edit")
        conversion.message = "Markdown düzenlemeniz yeni sürüm olarak kaydedildi."
        database.commit()
        database.refresh(conversion)
    except (OSError, ValueError) as error:
        database.rollback()
        raise HTTPException(status_code=422, detail="Markdown sürümü kaydedilemedi.") from error
    except IntegrityError as error:
        database.rollback()
        raise HTTPException(status_code=409, detail="Markdown aynı anda değişti. Güncel sürümü yükleyin.") from error
    return serialize_conversion(conversion, include_markdown=True)


@app.get(
    "/api/documents/{document_id}/conversion/revisions/{revision_number}",
    response_model=MarkdownRevisionContentResponse,
)
def get_document_markdown_revision(
    document_id: UUID,
    revision_number: int,
    database: Session = Depends(get_db),
) -> MarkdownRevisionContentResponse:
    conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
    revision = database.scalar(
        select(DocumentMarkdownRevision).where(
            DocumentMarkdownRevision.conversion_id == (conversion.id if conversion else None),
            DocumentMarkdownRevision.revision_number == revision_number,
        )
    )
    if conversion is None or revision is None:
        raise HTTPException(status_code=404, detail="Markdown sürümü bulunamadı.")
    try:
        content = Path(revision.content_path).read_bytes()
        if hashlib.sha256(content).hexdigest() != revision.content_hash:
            raise ValueError("Markdown sürümünün bütünlük özeti değişmiş.")
        markdown = content.decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail="Markdown sürümü okunamadı.") from error
    return MarkdownRevisionContentResponse(**serialize_markdown_revision(revision).model_dump(), markdown=markdown)


@app.post(
    "/api/documents/{document_id}/conversion/revisions/{revision_number}/restore",
    response_model=DocumentConversionResponse,
)
def restore_document_markdown_revision(
    document_id: UUID,
    revision_number: int,
    request: MarkdownRevisionRestoreRequest,
    database: Session = Depends(get_db),
) -> DocumentConversionResponse:
    """Geçmişteki içeriği etkin sürümü ezmeden yeni bir geri yükleme sürümü yapar."""
    conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
    if conversion is None:
        raise HTTPException(status_code=404, detail="Bu doküman için dönüşüm kaydı bulunamadı.")
    if conversion.status != ConversionStatus.READY:
        raise HTTPException(status_code=409, detail="Markdown sürümü yalnızca yayın öncesi geri yüklenebilir.")
    if not conversion.markdown_hash or request.expected_markdown_hash != conversion.markdown_hash:
        raise HTTPException(status_code=409, detail="Markdown başka bir işlemde değişti. Güncel sürümü yükleyin.")
    revision = database.scalar(
        select(DocumentMarkdownRevision).where(
            DocumentMarkdownRevision.conversion_id == conversion.id,
            DocumentMarkdownRevision.revision_number == revision_number,
        )
    )
    if revision is None:
        raise HTTPException(status_code=404, detail="Geri yüklenecek Markdown sürümü bulunamadı.")
    try:
        content = Path(revision.content_path).read_bytes()
        if hashlib.sha256(content).hexdigest() != revision.content_hash:
            raise ValueError("Markdown sürümünün bütünlük özeti değişmiş.")
        create_markdown_revision(database, conversion, content.decode("utf-8"), reason=f"restore:r{revision_number}")
        conversion.message = f"Markdown sürüm {revision_number} içeriği yeni bir sürüm olarak geri yüklendi."
        database.commit()
        database.refresh(conversion)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        database.rollback()
        raise HTTPException(status_code=422, detail="Markdown sürümü geri yüklenemedi.") from error
    except IntegrityError as error:
        database.rollback()
        raise HTTPException(status_code=409, detail="Markdown aynı anda değişti. Güncel sürümü yükleyin.") from error
    return serialize_conversion(conversion, include_markdown=True)


@app.get("/api/documents/{document_id}/conversion/bundle")
def download_conversion_bundle(document_id: UUID, database: Session = Depends(get_db)) -> Response:
    conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
    if conversion is None or conversion.status not in {ConversionStatus.READY, ConversionStatus.PUBLISHED}:
        raise HTTPException(status_code=409, detail="Markdown henüz hazır değil.")
    document = database.get(SourceDocument, document_id)
    # Paket, kayıtlı inceleme sürümünü ve ona bağlı tüm varlıkları içerir.
    assets = list(database.scalars(select(DocumentAsset).where(DocumentAsset.conversion_id == conversion.id)))
    if not conversion.markdown_path or not Path(conversion.markdown_path).is_file():
        raise HTTPException(status_code=409, detail="Markdown dosyası bulunamadı.")
    markdown_path = Path(conversion.markdown_path)
    markdown = markdown_path.read_text(encoding="utf-8")
    pictures = [asset for asset in assets if asset.kind == "picture"]
    if len(assets) != conversion.image_count + conversion.table_count:
        raise HTTPException(status_code=409, detail="Tüm görsel ve tablolar çıkarılmamış. Eksiksiz paket için belge yeniden dönüştürülmeli.")
    for asset in pictures:
        marker = f"<!-- konulab-asset:{asset.id} -->"
        link = f"![Doküman görseli](assets/{Path(asset.asset_path).name})"
        existing_path = f"./{Path(asset.asset_path).parent.name}/{Path(asset.asset_path).name}"
        if existing_path in markdown:
            continue
        if marker in markdown:
            markdown = markdown.replace(marker, marker + "\n\n" + link, 1)
        elif "<!-- image -->" in markdown:
            markdown = markdown.replace("<!-- image -->", link, 1)
    for asset in assets:
        if not Path(asset.asset_path).is_file():
            raise HTTPException(status_code=409, detail="Paket oluşturulamadı: kayıtlı görsel dosyası eksik.")
        directory = Path(asset.asset_path).parent.name
        markdown = markdown.replace(f"./{directory}/", "assets/")
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        quality_path = Path(document.source_path + ".quality.json")
        if quality_path.is_file():
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            source_page_count = int(quality.get("source_page_count", 0))
            if source_page_count:
                page_links = []
                for number in range(1, source_page_count + 1):
                    name = f"page-{number:04}.png"
                    page_path = Path(document.source_path + ".pages") / name
                    if not page_path.is_file():
                        raise HTTPException(status_code=409, detail="Kaynak sayfa görüntüsü eksik; paket tamamlanamadı.")
                    archive.write(page_path, f"source-pages/{name}")
                    page_links.append(f'<p><a href="{name}">Kaynak sayfa {number}</a></p>')
                archive.writestr("source-pages/index.html", '<!doctype html><meta charset="utf-8"><h1>Özgün PDF sayfa görünümleri</h1>' + "".join(page_links))
                markdown += "\n\n[Özgün PDF’nin bütün sayfa görünümleri](source-pages/index.html)\n"
            for table in quality.get("tables", []):
                accepted_asset = next((asset for asset in assets if asset.table_index == table["index"] and asset.inclusion_status == "accepted" and asset.proposed_table), None)
                html = candidate_html(accepted_asset.proposed_table) if accepted_asset else table.get("html")
                if not html:
                    continue
                name = f"tables/table-{int(table['index']) + 1}.html"
                archive.writestr(name, '<!doctype html><meta charset="utf-8"><style>table{border-collapse:collapse}td,th{border:1px solid #888;padding:6px}</style>' + html)
                marker = table["marker"]
                if marker in markdown:
                    markdown = markdown.replace(marker, marker + f"\n\n[Tabloyu birleşik hücreleriyle görüntüle]({name})", 1)
        archive.writestr("belge.md", markdown)
        for suffix, filename in ((".structure.json", "conversion-structure.json"), (".quality.json", "conversion-quality.json")):
            audit_path = Path(document.source_path + suffix)
            if audit_path.is_file():
                archive.write(audit_path, filename)
        for asset in assets:
            archive.write(asset.asset_path, f"assets/{Path(asset.asset_path).name}")
        archive.writestr("manifest.json", json.dumps({"document_id": str(document_id), "source_name": document.source_name, "expected_pictures": conversion.image_count, "expected_tables": conversion.table_count, "exported_assets": len(assets)}, ensure_ascii=False, indent=2))
    return Response(output.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="konulab-markdown.zip"'})


@app.get("/api/documents/{document_id}/assets/{asset_id}")
def get_document_asset(document_id: UUID, asset_id: UUID, database: Session = Depends(get_db)) -> FileResponse:
    """İnceleme ekranına yalnızca kayda bağlı dönüştürülmüş görseli verir."""
    asset = database.get(DocumentAsset, asset_id)
    if asset is None or asset.conversion.document_id != document_id:
        raise HTTPException(status_code=404, detail="Doküman varlığı bulunamadı.")
    asset_path = Path(asset.asset_path)
    if not asset_path.is_file():
        raise HTTPException(status_code=404, detail="Doküman varlığı dosyada bulunamadı.")
    return FileResponse(asset_path, media_type="image/png")


@app.post(
    "/api/documents/{document_id}/assets/{asset_id}/analyze",
    response_model=DocumentAssetResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_document_asset_analysis(
    document_id: UUID,
    asset_id: UUID,
    background_tasks: BackgroundTasks,
    database: Session = Depends(get_db),
    refine_only: bool = False,
) -> DocumentAssetResponse:
    """Görsel model açıklamasını yalnızca kullanıcı seçtiğinde işleme alır."""
    conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
    asset = database.get(DocumentAsset, asset_id)
    if conversion is None or asset is None or asset.conversion_id != conversion.id:
        raise HTTPException(status_code=404, detail="Doküman varlığı bulunamadı.")
    if conversion.status != ConversionStatus.READY:
        raise HTTPException(status_code=409, detail="Görsel açıklaması yalnızca yayın öncesi Markdown incelemesinde yapılabilir.")
    if asset.analysis_status == AssetAnalysisStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="Bu görsel için açıklama zaten hazırlanıyor.")
    if refine_only and (not asset.table_repair_required or not asset.proposed_table):
        raise HTTPException(status_code=409, detail="Yüksek çözünürlüklü kontrol için önce tablo önerisi oluşturulmalı.")
    asset.analysis_status = AssetAnalysisStatus.PROCESSING
    asset.model = current_runtime_model_config().vision_model
    asset.message = "Görsel açıklama sırasına alındı."
    asset.error_message = None
    database.commit()
    database.refresh(asset)
    background_tasks.add_task(describe_document_asset, document_id, asset.id, refine_only)
    return serialize_document_asset(conversion, asset)


@app.post("/api/documents/{document_id}/assets/analyze-all", response_model=list[DocumentAssetResponse], status_code=status.HTTP_202_ACCEPTED)
def request_all_document_asset_analysis(document_id: UUID, request: AssetBulkAnalysisRequest, background_tasks: BackgroundTasks, database: Session = Depends(get_db)) -> list[DocumentAssetResponse]:
    """Açıklanmamış varlıkları tek işçi sırasıyla çalıştırır; kabul otomatik değildir."""
    conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
    if conversion is None or conversion.status != ConversionStatus.READY:
        raise HTTPException(status_code=409, detail="Toplu açıklama yalnızca yayın öncesi yapılabilir.")
    requested_ids = set(request.asset_ids or [asset.id for asset in conversion.assets])
    queued = [asset for asset in conversion.assets if asset.id in requested_ids and asset.analysis_status in {AssetAnalysisStatus.PENDING, AssetAnalysisStatus.FAILED}]
    for asset in queued:
        asset.analysis_status = AssetAnalysisStatus.PROCESSING
        asset.model = current_runtime_model_config().vision_model
        asset.message = "Toplu görsel açıklama sırasında bekliyor."
        asset.error_message = None
    database.commit()
    for asset in queued:
        background_tasks.add_task(describe_document_asset, document_id, asset.id)
    return [serialize_document_asset(conversion, asset) for asset in queued]


@app.post("/api/documents/{document_id}/assets/{asset_id}/inclusion/{decision}", response_model=DocumentAssetResponse)
def decide_document_asset_inclusion(document_id: UUID, asset_id: UUID, decision: str, database: Session = Depends(get_db)) -> DocumentAssetResponse:
    if decision not in {"accepted", "rejected"}:
        raise HTTPException(status_code=422, detail="Karar accepted veya rejected olmalıdır.")
    conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
    asset = database.get(DocumentAsset, asset_id)
    if conversion is None or asset is None or asset.conversion_id != conversion.id:
        raise HTTPException(status_code=404, detail="Doküman varlığı bulunamadı.")
    if conversion.status != ConversionStatus.READY or asset.analysis_status != AssetAnalysisStatus.READY:
        if not (conversion.status == ConversionStatus.READY and asset.table_repair_required and decision == "rejected" and asset.analysis_status != AssetAnalysisStatus.PROCESSING):
            raise HTTPException(status_code=409, detail="Yalnız hazır açıklama yayın öncesinde kabul veya çıkarılabilir.")
    if asset.table_repair_required and decision == "accepted" and not asset.proposed_table:
        raise HTTPException(status_code=409, detail="Kabul edilebilecek bir tablo önerisi bulunamadı.")
    if asset.table_repair_required and decision == "accepted":
        content = Path(conversion.markdown_path).read_text(encoding="utf-8") if conversion.markdown_path else ""
        if f"<!-- konulab-table-body:{asset.table_index}:start -->" not in content:
            raise HTTPException(status_code=409, detail="Tablonun kaynak konumu düzenleme sırasında kaldırılmış; güncel Markdown'ı inceleyin.")
    asset.inclusion_status = decision
    persist_visual_appendix(database, conversion, list(conversion.assets))
    database.commit()
    return serialize_document_asset(conversion, asset)


@app.post("/api/documents/{document_id}/assets/accept-all", response_model=DocumentConversionResponse)
def accept_all_ready_asset_descriptions(document_id: UUID, database: Session = Depends(get_db)) -> DocumentConversionResponse:
    conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
    if conversion is None or conversion.status != ConversionStatus.READY:
        raise HTTPException(status_code=409, detail="Toplu kabul yalnızca yayın öncesi yapılabilir.")
    if any(asset.analysis_status == AssetAnalysisStatus.PROCESSING for asset in conversion.assets):
        raise HTTPException(status_code=409, detail="Görsel açıklamaları tamamlanmadan toplu kabul yapılamaz.")
    for asset in conversion.assets:
        if asset.analysis_status == AssetAnalysisStatus.READY and asset.description and asset.inclusion_status != "rejected":
            if asset.table_repair_required and (not asset.proposed_table or asset.needs_review):
                continue
            asset.inclusion_status = "accepted"
    persist_visual_appendix(database, conversion, list(conversion.assets))
    database.commit()
    return serialize_conversion(conversion, include_markdown=True)


@app.post(
    "/api/documents/convert",
    response_model=DocumentConversionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def convert_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    logical_key: str | None = Form(default=None),
    database: Session = Depends(get_db),
) -> DocumentConversionResponse:
    """Orijinal belgeyi saklar ve Markdown inceleme sürümünü üretmeye alır."""
    source_name = Path(file.filename or "dokuman").name
    suffix = Path(source_name).suffix.lower()
    if suffix not in SUPPORTED_CONVERTIBLE_SUFFIXES:
        accepted = ", ".join(sorted(SUPPORTED_CONVERTIBLE_SUFFIXES))
        raise HTTPException(status_code=415, detail=f"Dönüştürme modülü yalnızca {accepted} dosyalarını kabul eder.")
    raw_content = await file.read()
    if not raw_content:
        raise HTTPException(status_code=422, detail="Boş dosya yüklenemez.")

    key = normalize_logical_key(logical_key or Path(source_name).stem)
    content_hash = hashlib.sha256(raw_content).hexdigest()
    existing = database.scalar(
        select(SourceDocument)
        .join(DocumentConversion)
        .where(
            SourceDocument.logical_key == key,
            SourceDocument.content_hash == content_hash,
            DocumentConversion.status.in_([ConversionStatus.PROCESSING, ConversionStatus.READY, ConversionStatus.PUBLISHED]),
        )
    )
    if existing and existing.conversion:
        return serialize_conversion(existing.conversion, include_markdown=True)
    active_duplicate = database.scalar(
        select(SourceDocument).where(
            SourceDocument.logical_key == key,
            SourceDocument.content_hash == content_hash,
            SourceDocument.status == DocumentStatus.ACTIVE,
        )
    )
    if active_duplicate:
        raise HTTPException(status_code=409, detail="Bu dokümanın aynı aktif sürümü zaten indeksli.")

    latest = database.scalar(
        select(SourceDocument).where(SourceDocument.logical_key == key).order_by(SourceDocument.version.desc())
    )
    next_version = (latest.version + 1) if latest else 1
    stored_path = settings.storage_path / key / f"v{next_version}-{content_hash[:12]}-{source_name}"
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_bytes(raw_content)
    document = SourceDocument(
        logical_key=key,
        source_name=source_name,
        source_path=str(stored_path),
        media_type=file.content_type or mimetypes.guess_type(source_name)[0] or "application/octet-stream",
        content_hash=content_hash,
        version=next_version,
        status=DocumentStatus.PROCESSING,
        byte_size=len(raw_content),
    )
    conversion = DocumentConversion(
        document=document,
        status=ConversionStatus.PROCESSING,
        stage="queued",
        progress=5,
        message="Dönüştürme sırasına eklendi.",
    )
    try:
        database.add_all([document, conversion])
        database.commit()
        database.refresh(conversion)
    except IntegrityError as error:
        database.rollback()
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="Bu doküman sürümü zaten kaydedilmiş.") from error
    background_tasks.add_task(convert_document_to_markdown, document.id)
    return serialize_conversion(conversion)


@app.post(
    "/api/documents/{document_id}/publish-conversion",
    response_model=DocumentAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def publish_conversion(
    document_id: UUID,
    request: ConversionPublishRequest,
    background_tasks: BackgroundTasks,
    database: Session = Depends(get_db),
) -> DocumentAnalysisResponse:
    """Onaylanan Markdown'ı mevcut konu taslağı akışına geçirir."""
    document = database.get(SourceDocument, document_id)
    conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
    if document is None or conversion is None:
        raise HTTPException(status_code=404, detail="Yayınlanacak dönüşüm bulunamadı.")
    if conversion.status != ConversionStatus.READY:
        raise HTTPException(status_code=409, detail="Yalnızca incelemeye hazır Markdown yayınlanabilir.")
    if any(asset.analysis_status == AssetAnalysisStatus.PROCESSING for asset in conversion.assets):
        raise HTTPException(status_code=409, detail="Görsel işlemleri tamamlanmadan yayınlanamaz.")
    if any(asset.table_repair_required and asset.inclusion_status == "pending" for asset in conversion.assets):
        raise HTTPException(status_code=409, detail="Yeniden çıkarılan tabloları kabul edin veya özgün tabloyu korumayı seçin.")
    if not conversion.markdown_path:
        raise HTTPException(status_code=409, detail="Markdown dosyası bulunamadı.")
    try:
        markdown = Path(conversion.markdown_path).read_text(encoding="utf-8")
        quality_path = Path(document.source_path + ".quality.json")
        quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else {}
        if formula_review_required(quality, markdown) and not request.accept_formula_warnings:
            raise HTTPException(status_code=409, detail="Doğrulanamayan veya düzenlenen formülleri kaynakla inceleyip formül uyarılarını kabul edin.")
        if request.accept_formula_warnings:
            if request.expected_markdown_hash != conversion.markdown_hash:
                raise HTTPException(status_code=409, detail="Markdown sürümü değişti. Güncel formülleri inceleyip yeniden onaylayın.")
            conversion.formula_review_hash = conversion.markdown_hash
        parsed_sections = parse_document_for_embedding(markdown, ".md")
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=422, detail="Markdown konu analizine hazırlanamadı.") from error
    if not parsed_sections:
        raise HTTPException(status_code=422, detail="Markdown indekslenebilir yeterli bölüm içermiyor.")
    if database.scalar(select(DocumentAnalysis).where(DocumentAnalysis.document_id == document_id)) is not None:
        raise HTTPException(status_code=409, detail="Bu dönüşüm zaten öğrenme haritasına gönderildi.")
    for sequence, section in enumerate(parsed_sections, start=1):
        database.add(
            DocumentSection(
                document_id=document.id,
                heading_path=" > ".join(section.heading_path),
                content=section.content,
                content_type=section.content_type,
                sequence=sequence,
                line_start=section.line_start,
                line_end=section.line_end,
            )
        )
    analysis = DocumentAnalysis(
        document_id=document.id,
        model=current_runtime_model_config().text_model,
        status=AnalysisStatus.PROCESSING,
        stage="queued",
        progress=20,
        message="Onaylanan Markdown ayrıştırıldı; konu analizi sıraya alındı.",
    )
    conversion.status = ConversionStatus.PUBLISHED
    conversion.stage = "topic_analysis"
    conversion.message = "Markdown yayınlandı; konu önerileri hazırlanıyor."
    conversion.published_at = datetime.now(timezone.utc)
    database.add(analysis)
    database.commit()
    database.refresh(analysis)
    background_tasks.add_task(create_topic_proposals, document.id)
    return serialize_analysis(analysis)


@app.get("/api/documents/{document_id}/analysis", response_model=DocumentAnalysisResponse)
def get_document_analysis(document_id: UUID, database: Session = Depends(get_db)) -> DocumentAnalysisResponse:
    analysis = database.scalar(select(DocumentAnalysis).where(DocumentAnalysis.document_id == document_id))
    if analysis is None:
        raise HTTPException(status_code=404, detail="Doküman için analiz kaydı bulunamadı.")
    return serialize_analysis(analysis)


def remove_documents(database: Session, documents: list[SourceDocument]) -> None:
    """Silme işleminin SQL ve Qdrant tarafını tek bir akışta tutar."""
    if database.scalar(select(DocumentIndexJob).where(DocumentIndexJob.document_id.in_([d.id for d in documents]), DocumentIndexJob.status.in_(BUSY))):
        raise HTTPException(status_code=409, detail="İndeks yenilemesi sürerken doküman kaldırılamaz.")
    source_paths = [Path(document.source_path) for document in documents]
    converted_paths: list[Path] = []
    for document in documents:
        converted_paths.extend(Path(document.source_path + suffix) for suffix in (".structure.json", ".quality.json"))
        converted_paths.extend(Path(document.source_path + ".pages").glob("page-*.png"))
        conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document.id))
        if conversion is not None:
            if conversion.markdown_path:
                converted_paths.append(Path(conversion.markdown_path))
            converted_paths.extend(Path(revision.content_path) for revision in conversion.revisions)
            converted_paths.extend(Path(asset.asset_path) for asset in conversion.assets)
    try:
        for document in documents:
            if document.status == DocumentStatus.ACTIVE:
                remove_document_vectors(document)
            analysis = database.scalar(select(DocumentAnalysis).where(DocumentAnalysis.document_id == document.id))
            if analysis is not None:
                database.delete(analysis)
            database.delete(document)
        database.flush()
        # Kaynaksız kalan çocuklar önce, üst düğümler sonra silinir. Böylece silinen
        # bir dersin öğrenme haritasında hayalet başlık bırakılmaz; başka kaynağa
        # bağlı konu ve onun üst dalları ise korunur.
        while True:
            removable = [
                topic
                for topic in database.scalars(select(Topic))
                if not database.scalar(select(func.count(TopicSource.id)).where(TopicSource.topic_id == topic.id))
                and not database.scalar(select(func.count(Topic.id)).where(Topic.parent_id == topic.id))
            ]
            if not removable:
                break
            for topic in removable:
                database.delete(topic)
            database.flush()
        database.commit()
    except VectorStoreUnavailableError as error:
        database.rollback()
        raise HTTPException(status_code=503, detail=str(error)) from error
    except IntegrityError as error:
        database.rollback()
        raise HTTPException(status_code=422, detail="Doküman kaldırılamadı.") from error
    for source_path in [*source_paths, *converted_paths]:
        source_path.unlink(missing_ok=True)


def document_deletion_impact(database: Session, documents: list[SourceDocument]) -> DocumentDeletionImpactResponse:
    """Kaynak silinmeden önce konulara bağlı öğrenci verisini görünür yapar."""
    document_ids = [document.id for document in documents]
    affected_topics = lesson_topics(database, documents)
    topic_ids = [topic.id for topic in affected_topics]
    if not topic_ids:
        return DocumentDeletionImpactResponse(
            document_ids=document_ids,
            affected_topics=[],
            learner_progress_count=0,
            prerequisite_relation_count=0,
            conversation_count=0,
            feedback_count=0,
        )
    return DocumentDeletionImpactResponse(
        document_ids=document_ids,
        affected_topics=affected_topics,
        learner_progress_count=int(
            database.scalar(select(func.count(LearnerTopicProgress.id)).where(LearnerTopicProgress.topic_id.in_(topic_ids))) or 0
        ),
        prerequisite_relation_count=int(
            database.scalar(
                select(func.count(TopicPrerequisite.id)).where(
                    (TopicPrerequisite.topic_id.in_(topic_ids))
                    | (TopicPrerequisite.prerequisite_topic_id.in_(topic_ids))
                )
            )
            or 0
        ),
        conversation_count=int(database.scalar(select(func.count(Conversation.id)).where(Conversation.topic_id.in_(topic_ids))) or 0),
        feedback_count=int(database.scalar(select(func.count(LearnerFeedback.id)).where(LearnerFeedback.topic_id.in_(topic_ids))) or 0),
    )


@app.post("/api/documents/deletion-impact", response_model=DocumentDeletionImpactResponse)
def get_documents_deletion_impact(
    request: DocumentBulkDeleteRequest, database: Session = Depends(get_db)
) -> DocumentDeletionImpactResponse:
    requested_ids = list(dict.fromkeys(request.document_ids))
    documents = list(database.scalars(select(SourceDocument).where(SourceDocument.id.in_(requested_ids))))
    if len(documents) != len(requested_ids):
        raise HTTPException(status_code=404, detail="Silinmek istenen dokümanlardan biri bulunamadı.")
    return document_deletion_impact(database, documents)


@app.delete("/api/documents", response_model=BulkDeleteResponse)
def delete_documents(request: DocumentBulkDeleteRequest, database: Session = Depends(get_db)) -> BulkDeleteResponse:
    requested_ids = list(dict.fromkeys(request.document_ids))
    documents = list(database.scalars(select(SourceDocument).where(SourceDocument.id.in_(requested_ids))))
    if len(documents) != len(requested_ids):
        raise HTTPException(status_code=404, detail="Silinmek istenen dokümanlardan biri bulunamadı.")
    remove_documents(database, documents)
    return BulkDeleteResponse(deleted_count=len(documents))


@app.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: UUID, database: Session = Depends(get_db)) -> None:
    """Kaynak dosya, bölüm eşleşmeleri ve vektörlerini birlikte kaldırır."""
    document = database.get(SourceDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Doküman bulunamadı.")
    remove_documents(database, [document])


@app.post(
    "/api/documents/{document_id}/reanalyze",
    response_model=DocumentAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reanalyze_document(
    document_id: UUID,
    background_tasks: BackgroundTasks,
    database: Session = Depends(get_db),
) -> DocumentAnalysisResponse:
    """Henüz onaylanmamış bir taslağı silip aynı dokümanı yeniden konu analizine alır."""
    document = database.get(SourceDocument, document_id)
    analysis = database.scalar(select(DocumentAnalysis).where(DocumentAnalysis.document_id == document_id))
    if document is None or analysis is None or document.status not in [DocumentStatus.PROCESSING, DocumentStatus.FAILED]:
        raise HTTPException(status_code=404, detail="Yeniden analiz edilebilecek bir doküman bulunamadı.")
    if analysis.status not in [AnalysisStatus.READY, AnalysisStatus.FAILED, AnalysisStatus.PENDING, AnalysisStatus.PROCESSING]:
        raise HTTPException(status_code=409, detail="Bu doküman için analiz zaten sürüyor.")
    for proposal in list(analysis.topic_proposals):
        database.delete(proposal)
    analysis.status = AnalysisStatus.PROCESSING
    analysis.stage = "queued"
    analysis.progress = 20
    analysis.message = "Konu merkezli analiz başlatılmak üzere sırada."
    analysis.error_message = None
    analysis.suggested_lesson_title = None
    document.status = DocumentStatus.PROCESSING
    database.commit()
    database.refresh(analysis)
    database.expire(analysis, ["topic_proposals"])
    background_tasks.add_task(create_topic_proposals, document.id)
    return serialize_analysis(analysis)


@app.post("/api/documents/analyze", response_model=DocumentAnalysisResponse, status_code=status.HTTP_202_ACCEPTED)
async def analyze_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    logical_key: str | None = Form(default=None),
    database: Session = Depends(get_db),
) -> DocumentAnalysisResponse:
    """Dokümanı Qdrant'a yazmadan önce konu ve örnek taslağı üretir."""
    source_name = Path(file.filename or "dokuman").name
    suffix = Path(source_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=415, detail="İlk sürüm yalnızca .txt, .md ve .py dosyalarını kabul eder.")
    raw_content = await file.read()
    if not raw_content:
        raise HTTPException(status_code=422, detail="Boş dosya yüklenemez.")
    try:
        text = raw_content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=422, detail="Dosya UTF-8 olarak okunamadı.") from error

    key = normalize_logical_key(logical_key or Path(source_name).stem)
    content_hash = hashlib.sha256(raw_content).hexdigest()
    active_duplicate = database.scalar(
        select(SourceDocument).where(
            SourceDocument.logical_key == key,
            SourceDocument.content_hash == content_hash,
            SourceDocument.status == DocumentStatus.ACTIVE,
        )
    )
    if active_duplicate:
        raise HTTPException(status_code=409, detail="Bu dokümanın aynı aktif sürümü zaten indeksli.")
    pending_duplicate = database.scalar(
        select(DocumentAnalysis)
        .join(SourceDocument)
        .where(
            SourceDocument.logical_key == key,
            SourceDocument.content_hash == content_hash,
            SourceDocument.status == DocumentStatus.PROCESSING,
            DocumentAnalysis.status.in_([AnalysisStatus.PROCESSING, AnalysisStatus.READY]),
        )
    )
    if pending_duplicate:
        return serialize_analysis(pending_duplicate)

    latest = database.scalar(
        select(SourceDocument).where(SourceDocument.logical_key == key).order_by(SourceDocument.version.desc())
    )
    next_version = (latest.version + 1) if latest else 1
    stored_path = settings.storage_path / key / f"v{next_version}-{content_hash[:12]}-{source_name}"
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_bytes(raw_content)
    document = SourceDocument(
        logical_key=key,
        source_name=source_name,
        source_path=str(stored_path),
        media_type=file.content_type or mimetypes.guess_type(source_name)[0] or "text/plain",
        content_hash=content_hash,
        version=next_version,
        status=DocumentStatus.PROCESSING,
        byte_size=len(raw_content),
    )
    try:
        database.add(document)
        database.flush()
        parsed_sections = parse_document_for_embedding(text, suffix)
        if not parsed_sections:
            raise ValueError("Doküman indekslenebilir yeterli bölüm içermiyor.")
        for sequence, section in enumerate(parsed_sections, start=1):
            database.add(
                DocumentSection(
                    document_id=document.id,
                    heading_path=" > ".join(section.heading_path),
                    content=section.content,
                    content_type=section.content_type,
                    sequence=sequence,
                    line_start=section.line_start,
                    line_end=section.line_end,
                )
            )
        database.commit()
    except (IntegrityError, ValueError) as error:
        database.rollback()
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Doküman analiz için işlenemedi.") from error

    analysis = DocumentAnalysis(
        document_id=document.id,
        model=current_runtime_model_config().text_model,
        status=AnalysisStatus.PROCESSING,
        stage="queued",
        progress=20,
        message="Doküman ayrıştırıldı; konu analizi başlatılmak üzere sırada.",
    )
    database.add(analysis)
    database.commit()
    database.refresh(analysis)
    background_tasks.add_task(create_topic_proposals, document.id)
    return serialize_analysis(analysis)


async def index_approved_document(document_id: UUID) -> None:
    """Onaylanan taslağı arka planda indeksler ve kullanıcıya aşamalı durum yazar."""
    database = SessionLocal()
    try:
        document = database.get(SourceDocument, document_id)
        analysis = database.scalar(select(DocumentAnalysis).where(DocumentAnalysis.document_id == document_id))
        if document is None or analysis is None or analysis.status != AnalysisStatus.PROCESSING:
            return
        decisions = {
            UUID(str(item["proposal_id"])): item
            for item in (analysis.approval_payload or [])
        }
        proposals = {proposal.id: proposal for proposal in analysis.topic_proposals}
        if not decisions or not set(decisions).issubset(proposals):
            raise ValueError("Onaylanacak konu taslağı geçersiz.")

        analysis.stage = "indexing"
        analysis.progress = 35
        analysis.message = "Konu eşleştirmeleri hazırlanıyor; embedding oluşturulacak."
        database.commit()
        previous = database.scalar(
            select(SourceDocument)
            .where(SourceDocument.logical_key == document.logical_key, SourceDocument.status == DocumentStatus.ACTIVE)
            .order_by(SourceDocument.version.desc())
        )
        # Aynı ders adı altında bulunan kaynaklarda yalnızca birebir değil,
        # güvenle yakın sayılan başlıkları da aynı konuya bağlarız. Bu adım,
        # kullanıcı ders birleştirmesini açıkça onayladıktan sonra çalışır.
        same_lesson_documents = matching_lesson_documents(database, document.lesson_title or "", document.id)
        existing_lesson_topics = lesson_topics(database, same_lesson_documents)
        existing_by_title = {topic.title.casefold(): topic for topic in database.scalars(select(Topic))}
        existing_lesson_by_title = {topic.title.casefold(): topic for topic in existing_lesson_topics}
        created_by_title: dict[str, Topic] = {}
        for proposal_id, decision in decisions.items():
            proposal = proposals[proposal_id]
            title = str(decision["title"]).strip()
            description = decision.get("description")
            target_topic = (
                existing_lesson_by_title.get(title.casefold())
                or existing_by_title.get(title.casefold())
                or created_by_title.get(title.casefold())
            )
            if target_topic is None:
                target_topic = next(
                    (
                        topic
                        for topic in existing_lesson_topics
                        if DocumentAnalyzer._titles_are_near_duplicates(title, topic.title)
                    ),
                    None,
                )
            if target_topic is None:
                target_topic = Topic(
                    slug=normalize_topic_slug(title, database),
                    title=title,
                    description=str(description).strip() if description else proposal.description,
                    order_index=999,
                )
                database.add(target_topic)
                database.flush()
                created_by_title[title.casefold()] = target_topic
            for assignment in proposal.section_proposals:
                assignment.section.content_type = assignment.content_type
                database.add(TopicSource(topic_id=target_topic.id, section_id=assignment.section_id, relevance=85, approved=True))
        database.flush()
        def context_progress(done, total):
            analysis.stage = "contextualizing"
            analysis.progress = 35 + int(20 * done / max(1, total))
            analysis.message = f"Arama bağlamı hazırlanıyor: {done}/{total}. Kaynak metni korunuyor."
            database.commit()

        if document.contextual_enrichment:
            context_progress(0, len(document.sections))
        await enrich_document(database, document, context_progress)
        analysis.stage = "vectorizing"
        analysis.progress = 60
        analysis.message = "Embedding oluşturuluyor ve Qdrant'a yazılıyor."
        database.commit()
        index_document(database, document)
        if previous:
            previous.status = DocumentStatus.REPLACED
            previous.replaced_at = datetime.now(timezone.utc)
        document.status = DocumentStatus.ACTIVE
        analysis.status = AnalysisStatus.APPROVED
        analysis.stage = "completed"
        analysis.progress = 100
        analysis.message = "Konular indekslendi; kaynak öğrenmeye hazır."
        if document.contextual_enrichment:
            sections = list(database.scalars(select(DocumentSection).where(DocumentSection.document_id == document.id)))
            used = sum(bool((s.search_context_metadata or {}).get("used_in_embedding")) for s in sections)
            failed = sum((s.search_context_metadata or {}).get("status") == "failed" for s in sections)
            analysis.message += f" Arama bağlamı: {used}/{len(sections)} parçada kullanıldı; {failed} üretim hatası. Diğer parçalar özgün içerikle indekslendi."
        analysis.error_message = None
        analysis.approval_payload = None
        analysis.approved_at = datetime.now(timezone.utc)
        conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
        if conversion is not None:
            conversion.stage = "ready"
            conversion.message = "Markdown ve öğrenme haritası hazır."
        database.commit()
        if previous:
            try:
                remove_document_vectors(previous)
            except VectorStoreUnavailableError:
                pass
    except Exception as error:
        database.rollback()
        analysis = database.scalar(select(DocumentAnalysis).where(DocumentAnalysis.document_id == document_id))
        if analysis is not None:
            analysis.status = AnalysisStatus.FAILED
            analysis.stage = "failed"
            analysis.message = "İndeksleme tamamlanamadı."
            analysis.error_message = str(error)[:1_000]
            conversion = database.scalar(select(DocumentConversion).where(DocumentConversion.document_id == document_id))
            if conversion is not None:
                conversion.stage = "failed"
                conversion.message = "Markdown yayınlandı ancak indeksleme tamamlanamadı."
            database.commit()
    finally:
        database.close()


@app.post(
    "/api/documents/{document_id}/approve-analysis",
    response_model=DocumentAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def approve_document_analysis(
    document_id: UUID,
    request: AnalysisApprovalRequest,
    background_tasks: BackgroundTasks,
    database: Session = Depends(get_db),
) -> DocumentAnalysisResponse:
    """Onayı kuyruğa alır; indeksleme ilerlemesi analiz kaydından izlenir."""
    document = database.get(SourceDocument, document_id)
    if document is None or document.status != DocumentStatus.PROCESSING:
        raise HTTPException(status_code=404, detail="Onay bekleyen doküman bulunamadı.")
    analysis = database.scalar(
        select(DocumentAnalysis).where(
            DocumentAnalysis.document_id == document_id,
            DocumentAnalysis.status.in_([AnalysisStatus.READY, AnalysisStatus.PENDING]),
        )
    )
    if analysis is None:
        raise HTTPException(status_code=409, detail="Bu dokümanın onay bekleyen bir konu taslağı yok.")
    proposals = {proposal.id: proposal for proposal in analysis.topic_proposals}
    decisions = {decision.proposal_id: decision for decision in request.decisions}
    selected_ids = set(decisions)
    if not selected_ids.issubset(proposals):
        raise HTTPException(status_code=422, detail="Bilinmeyen konu önerisi seçildi.")

    lesson_title = (request.lesson_title or analysis.suggested_lesson_title or Path(document.source_name).stem).strip()
    if len(lesson_title) < 2:
        raise HTTPException(status_code=422, detail="Ders adı en az iki karakter olmalıdır.")
    matches = matching_lesson_documents(database, lesson_title, document.id)
    if matches and not request.allow_existing_lesson_merge:
        raise HTTPException(
            status_code=409,
            detail="Bu ders adıyla mevcut kaynaklar var. Birleştirme onayı gereklidir.",
        )
    document.lesson_title = lesson_title[:255]
    document.contextual_enrichment = request.contextual_enrichment

    analysis.status = AnalysisStatus.PROCESSING
    analysis.stage = "approval_queued"
    analysis.progress = 5
    analysis.message = "Onay alındı; indeksleme sırasına eklendi."
    analysis.error_message = None
    analysis.approval_payload = [
        {"proposal_id": str(item.proposal_id), "title": item.title.strip(), "description": item.description}
        for item in request.decisions
    ]
    database.commit()
    database.refresh(analysis)
    background_tasks.add_task(index_approved_document, document.id)
    return serialize_analysis(analysis)


@app.post(
    "/api/documents/{document_id}/lesson-merge-preview",
    response_model=LessonMergePreviewResponse,
)
def preview_lesson_merge(
    document_id: UUID,
    request: LessonMergePreviewRequest,
    database: Session = Depends(get_db),
) -> LessonMergePreviewResponse:
    """Onay öncesinde aynı ders adıyla eşleşen kaynakları ve konu çakışmalarını döndürür."""
    document = database.get(SourceDocument, document_id)
    analysis = database.scalar(select(DocumentAnalysis).where(DocumentAnalysis.document_id == document_id))
    if document is None or analysis is None or analysis.status not in [AnalysisStatus.READY, AnalysisStatus.PENDING]:
        raise HTTPException(status_code=404, detail="Birleştirme önizlemesi için hazır bir doküman bulunamadı.")
    proposals = {proposal.id: proposal for proposal in analysis.topic_proposals}
    decisions = {decision.proposal_id: decision for decision in request.decisions}
    if not decisions or not set(decisions).issubset(proposals):
        raise HTTPException(status_code=422, detail="Bilinmeyen konu önerisi seçildi.")
    title = " ".join(request.lesson_title.split())
    matches = matching_lesson_documents(database, title, document.id)
    existing_topics = lesson_topics(database, matches)
    overlaps: list[str] = []
    for decision in request.decisions:
        proposal_title = decision.title.strip()
        if any(
            proposal_title.casefold() == topic.title.casefold()
            or DocumentAnalyzer._titles_are_near_duplicates(proposal_title, topic.title)
            for topic in existing_topics
        ):
            overlaps.append(proposal_title)
    return LessonMergePreviewResponse(
        lesson_title=title,
        matching_documents=[serialize_document(item) for item in matches],
        overlapping_topics=list(dict.fromkeys(overlaps)),
    )


@app.post("/api/documents", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    logical_key: str | None = Form(default=None),
    topic_id: int | None = Form(default=None),
    database: Session = Depends(get_db),
) -> UploadResponse:
    if topic_id is not None and database.get(Topic, topic_id) is None:
        raise HTTPException(status_code=404, detail="Konu bulunamadı.")
    source_name = Path(file.filename or "dokuman").name
    suffix = Path(source_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail="İlk sürüm yalnızca .txt, .md ve .py dosyalarını kabul eder.",
        )

    raw_content = await file.read()
    if not raw_content:
        raise HTTPException(status_code=422, detail="Boş dosya yüklenemez.")
    try:
        text = raw_content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=422, detail="Dosya UTF-8 olarak okunamadı.") from error

    key = normalize_logical_key(logical_key or Path(source_name).stem)
    content_hash = hashlib.sha256(raw_content).hexdigest()
    duplicate = database.scalar(
        select(SourceDocument).where(
            SourceDocument.logical_key == key,
            SourceDocument.content_hash == content_hash,
            SourceDocument.status == DocumentStatus.ACTIVE,
        )
    )
    if duplicate:
        return UploadResponse(action="unchanged", document=serialize_document(duplicate), section_count=len(duplicate.sections))

    previous = database.scalar(
        select(SourceDocument)
        .where(SourceDocument.logical_key == key, SourceDocument.status == DocumentStatus.ACTIVE)
        .order_by(SourceDocument.version.desc())
    )
    next_version = (previous.version + 1) if previous else 1
    stored_path = settings.storage_path / key / f"v{next_version}-{content_hash[:12]}-{source_name}"
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_bytes(raw_content)

    document = SourceDocument(
        logical_key=key,
        source_name=source_name,
        source_path=str(stored_path),
        media_type=file.content_type or mimetypes.guess_type(source_name)[0] or "text/plain",
        content_hash=content_hash,
        version=next_version,
        status=DocumentStatus.PROCESSING,
        byte_size=len(raw_content),
    )
    try:
        database.add(document)
        database.flush()
        parsed_sections = parse_document_for_embedding(text, suffix)
        for sequence, section in enumerate(parsed_sections, start=1):
            document_section = DocumentSection(
                document_id=document.id,
                heading_path=" > ".join(section.heading_path),
                content=section.content,
                content_type=section.content_type,
                sequence=sequence,
                line_start=section.line_start,
                line_end=section.line_end,
            )
            if topic_id is not None:
                document_section.topics.append(TopicSource(topic_id=topic_id, relevance=100, approved=True))
            database.add(document_section)

        # Session autoflush kapalı: indeksleyicinin yeni bölümleri SQL'den
        # görebilmesi ve Qdrant nokta kimliklerini alabilmesi için zorunlu.
        database.flush()
        index_document(database, document)

        if previous:
            previous.status = DocumentStatus.REPLACED
            previous.replaced_at = datetime.now(timezone.utc)
        document.status = DocumentStatus.ACTIVE
        database.commit()
        database.refresh(document)
    except (EmbeddingUnavailableError, VectorStoreUnavailableError) as error:
        database.rollback()
        stored_path.unlink(missing_ok=True)
        try:
            remove_document_vectors(document)
        except VectorStoreUnavailableError:
            pass
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (IntegrityError, ValueError) as error:
        database.rollback()
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Doküman işlenemedi.") from error

    if previous:
        # Temizleme başarısız olsa bile /api/search SQL'de aktiflik denetimi yapar.
        # Böylece eski sürümün retrieval bağlamına girmesi engellenir.
        try:
            remove_document_vectors(previous)
        except VectorStoreUnavailableError:
            pass

    return UploadResponse(
        action="replaced" if previous else "created",
        document=serialize_document(document),
        section_count=len(parsed_sections),
    )


@app.post("/api/documents/{document_id}/reindex", response_model=UploadResponse)
async def reindex_document(document_id: UUID, request: ReindexRequest | None = None, database: Session = Depends(get_db)) -> UploadResponse:
    document = database.get(SourceDocument, document_id)
    if document is None or document.status != DocumentStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="Aktif doküman bulunamadı.")
    with queue_lock:
        try:
            job = reserve_job(database, document, document.contextual_enrichment if request is None or request.contextual_enrichment is None else request.contextual_enrichment, status="running")
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    try:
        if request and request.contextual_enrichment is not None:
            document.contextual_enrichment = request.contextual_enrichment
        await enrich_document(database, document, flush=False)
        section_count = index_document(database, document)
        job.status, job.stage, job.progress, job.message = "completed", "completed", 100, "İndeks yenilendi; kaynak korundu."
        database.commit()
        database.refresh(document)
    except Exception as error:
        database.rollback()
        job.status, job.stage, job.message = "failed", "failed", "İndeks yenilenemedi; önceki SQL ayarı korundu."
        database.add(ApplicationErrorEvent(request_id=str(uuid4()), method="POST", path=f"/api/documents/{document_id}/reindex",
            error_type=type(error).__name__[:128], summary="Yeniden indeksleme tamamlanamadı; SQL geri alındı."))
        database.commit()
        raise HTTPException(status_code=503, detail=job.message) from error

    return UploadResponse(action="reindexed", document=serialize_document(document), section_count=section_count)


@app.post("/api/documents/{document_id}/rechunk-reindex", response_model=UploadResponse)
async def rechunk_and_reindex_document(document_id: UUID, database: Session = Depends(get_db)) -> UploadResponse:
    """Önceki sürümlerde karaktere göre oluşturulmuş kaynakları token güvenli biçimde yeniler."""
    document = database.get(SourceDocument, document_id)
    if document is None or document.status != DocumentStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="Aktif doküman bulunamadı.")
    with queue_lock:
        try:
            job = reserve_job(database, document, document.contextual_enrichment, status="running")
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    try:
        old_ids = list(database.scalars(select(DocumentSection.id).where(DocumentSection.document_id == document.id)))
        changed, section_count = rechunk_document_sections(database, document)
        await enrich_document(database, document)
        section_count = index_document(database, document)
        job.status, job.stage, job.progress, job.message = "completed", "completed", 100, "Bölümler ve arama bağlamı yenilendi."
        database.commit()
        database.refresh(document)
    except Exception as error:
        database.rollback()
        job.status, job.stage, job.message = "failed", "failed", "Bölümler yenilenemedi; kaynak korundu."
        database.add(ApplicationErrorEvent(request_id=str(uuid4()), method="POST", path=f"/api/documents/{document_id}/rechunk-reindex",
            error_type=type(error).__name__[:128], summary="Yeniden parçalama tamamlanamadı; SQL geri alındı."))
        database.commit()
        raise HTTPException(status_code=503, detail=job.message) from error
    if changed:
        try:
            get_vector_store().delete_sections(old_ids)
        except VectorStoreUnavailableError:
            # /api/search SQL'de olmayan eski bölüm kimliklerini zaten eler.
            pass
    return UploadResponse(action="reindexed", document=serialize_document(document), section_count=section_count)


@app.post("/api/documents/{document_id}/index-job", response_model=DocumentIndexJobResponse, status_code=202)
def start_document_index_job(document_id: UUID, request: ReindexRequest, background_tasks: BackgroundTasks,
                             database: Session = Depends(get_db)):
    with queue_lock:
        document = database.get(SourceDocument, document_id)
        if document is None or document.status != DocumentStatus.ACTIVE:
            raise HTTPException(status_code=404, detail="Aktif doküman bulunamadı.")
        try:
            job = reserve_job(database, document, document.contextual_enrichment if request.contextual_enrichment is None else request.contextual_enrichment)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    background_tasks.add_task(run_index_job, document_id)
    return job


@app.get("/api/documents/{document_id}/index-job", response_model=DocumentIndexJobResponse | None)
def get_document_index_job(document_id: UUID, database: Session = Depends(get_db)):
    if database.get(SourceDocument, document_id) is None:
        raise HTTPException(status_code=404, detail="Doküman bulunamadı.")
    return database.get(DocumentIndexJob, document_id)
