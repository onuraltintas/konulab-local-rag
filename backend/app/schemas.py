from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import AnalysisStatus, AssetAnalysisStatus, ConversionStatus, DocumentStatus, EmbeddingStatus, LLMProviderType


class HealthResponse(BaseModel):
    status: str
    app_name: str


class RuntimeResponse(BaseModel):
    embedding_model: str
    embedding_device: str
    cuda_available: bool
    cuda_device_name: str | None
    qdrant_url: str
    ollama_base_url: str
    ollama_model: str


class ProviderModelResponse(BaseModel):
    id: str
    label: str
    size_bytes: int | None = None
    parameter_size: str | None = None
    quantization: str | None = None


class ProviderDiscoveryResponse(BaseModel):
    provider: LLMProviderType
    base_url: str
    reachable: bool
    error: str | None = None
    models: list[ProviderModelResponse] = Field(default_factory=list)


class RuntimeModelSettingsResponse(BaseModel):
    text_provider: LLMProviderType
    ollama_base_url: str
    ollama_model: str
    vllm_base_url: str
    vllm_model: str | None = None
    vision_provider: LLMProviderType
    vision_model: str
    context_length: int
    embedding_model: str
    embedding_device: str
    providers: list[ProviderDiscoveryResponse] = Field(default_factory=list)


class RuntimeModelSettingsUpdateRequest(BaseModel):
    text_provider: LLMProviderType
    ollama_base_url: str = Field(min_length=8, max_length=512)
    ollama_model: str = Field(min_length=1, max_length=255)
    vllm_base_url: str = Field(min_length=8, max_length=512)
    vllm_model: str | None = Field(default=None, max_length=255)
    vision_provider: LLMProviderType
    vision_model: str = Field(min_length=1, max_length=255)
    context_length: int = Field(ge=4_096, le=131_072)


class RuntimeModelTestResponse(BaseModel):
    ok: bool
    message: str
    text_model: str


class TopicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    description: str | None
    parent_id: int | None
    order_index: int


class TopicPrerequisiteUpdateRequest(BaseModel):
    prerequisite_topic_ids: list[int] = Field(default_factory=list, max_length=50)


class TopicPrerequisiteSuggestion(BaseModel):
    topic_id: int
    title: str
    reason: str
    confidence: float = Field(ge=0, le=1)


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    logical_key: str
    source_name: str
    lesson_title: str | None
    media_type: str
    content_hash: str
    version: int
    status: DocumentStatus
    byte_size: int
    created_at: datetime | None
    replaced_at: datetime | None


class UploadResponse(BaseModel):
    action: str
    document: DocumentResponse
    section_count: int


class DocumentAssetResponse(BaseModel):
    id: UUID
    page_number: int | None
    kind: str
    description: str | None
    confidence: int | None
    needs_review: bool
    url: str
    analysis_status: AssetAnalysisStatus
    model: str | None
    message: str | None
    error_message: str | None
    structured_markdown: str | None
    row_count: int | None
    column_count: int | None
    inclusion_status: str


class MarkdownRevisionResponse(BaseModel):
    id: UUID
    revision_number: int
    content_hash: str
    byte_size: int
    reason: str
    created_at: datetime | None


class MarkdownRevisionContentResponse(MarkdownRevisionResponse):
    markdown: str


class DocumentConversionResponse(BaseModel):
    """Dönüşümden yayın onayına kadarki istemci sözleşmesi."""

    document: DocumentResponse
    status: ConversionStatus
    markdown: str | None = None
    markdown_hash: str | None
    converter: str | None
    converter_version: str | None
    ocr_used: bool
    page_count: int | None
    table_count: int
    image_count: int
    stage: str
    progress: int = Field(ge=0, le=100)
    message: str | None
    error_message: str | None
    assets: list[DocumentAssetResponse] = Field(default_factory=list)
    current_revision_number: int = 0
    revisions: list[MarkdownRevisionResponse] = Field(default_factory=list)


class ConversionPublishRequest(BaseModel):
    """Dönüştürülmüş metni konu analizine geçirirken açık kullanıcı onayı."""

    confirmation: Literal["OGRENME_HARITASINA_DONUSTUR"]


class MarkdownSaveRequest(BaseModel):
    markdown: str = Field(min_length=1, max_length=5_000_000)
    expected_markdown_hash: str = Field(min_length=64, max_length=64)


class MarkdownRevisionRestoreRequest(BaseModel):
    expected_markdown_hash: str = Field(min_length=64, max_length=64)


class AssetBulkAnalysisRequest(BaseModel):
    asset_ids: list[UUID] | None = Field(default=None, max_length=100)


class DocumentBulkDeleteRequest(BaseModel):
    document_ids: list[UUID] = Field(min_length=1, max_length=100)


class BulkDeleteResponse(BaseModel):
    deleted_count: int


class DocumentDeletionImpactResponse(BaseModel):
    """Bir veya daha çok kaynağı silmeden önce korunacak bağlam özeti."""

    document_ids: list[UUID]
    affected_topics: list[TopicResponse]
    learner_progress_count: int
    prerequisite_relation_count: int
    conversation_count: int
    feedback_count: int


class BackupResponse(BaseModel):
    filename: str
    created_at: datetime
    byte_size: int
    download_url: str


class SystemStatusResponse(BaseModel):
    checked_at: datetime
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    qdrant: Literal["ok", "unavailable"]
    latest_backup: BackupResponse | None = None


class MaintenanceStateResponse(BaseModel):
    enabled: bool
    reason: str | None
    started_at: datetime | None


class MaintenanceUpdateRequest(BaseModel):
    enabled: bool
    confirmation: Literal["BAKIM_MODUNU_AC", "BAKIM_MODUNU_KAPAT"]
    reason: str | None = Field(default=None, max_length=255)


class BackupRestorePreviewResponse(BaseModel):
    filename: str
    created_at: datetime | None
    database_bytes: int
    source_file_count: int
    source_bytes: int
    requires_reindex: bool = True


class ApplicationErrorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    request_id: str
    method: str
    path: str
    error_type: str
    summary: str
    created_at: datetime | None
    resolved_at: datetime | None


class TopicProposalResponse(BaseModel):
    id: UUID
    title: str
    description: str | None
    existing_topic_id: int | None
    section_count: int
    example_count: int
    code_count: int
    exercise_count: int


class DocumentAnalysisResponse(BaseModel):
    document: DocumentResponse
    model: str | None
    suggested_lesson_title: str | None
    status: AnalysisStatus
    stage: str
    progress: int = Field(ge=0, le=100)
    message: str | None
    error_message: str | None
    topics: list[TopicProposalResponse]


class TopicApprovalDecision(BaseModel):
    proposal_id: UUID
    title: str = Field(min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=1_000)


class AnalysisApprovalRequest(BaseModel):
    lesson_title: str | None = Field(default=None, max_length=255)
    allow_existing_lesson_merge: bool = False
    decisions: list[TopicApprovalDecision] = Field(min_length=1, max_length=100)


class LessonMergePreviewRequest(BaseModel):
    lesson_title: str = Field(min_length=2, max_length=255)
    decisions: list[TopicApprovalDecision] = Field(min_length=1, max_length=100)


class LessonMergePreviewResponse(BaseModel):
    lesson_title: str
    matching_documents: list[DocumentResponse]
    overlapping_topics: list[str]


class SectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    heading_path: str
    content: str
    content_type: str
    sequence: int
    line_start: int
    line_end: int
    embedding_status: EmbeddingStatus


class SourceViewerSectionResponse(BaseModel):
    id: UUID
    heading_path: str
    content: str
    content_type: str
    sequence: int
    line_start: int
    line_end: int
    is_focus: bool = False


class SourceViewerResponse(BaseModel):
    document_id: UUID
    source_name: str
    lesson_title: str | None
    target_section_id: UUID
    sections: list[SourceViewerSectionResponse]
    assets: list[DocumentAssetResponse] = Field(default_factory=list)


class TopicStudyStepResponse(BaseModel):
    id: UUID
    # Bir öğrenme ekranı bazen aynı başlık altındaki birden fazla vektör
    # parçasından oluşur. Açıklama isteğinde tam olarak bu parçaları sabitlemek
    # için kimliklerin tamamını istemciye de gönderiyoruz.
    section_ids: list[UUID]
    source_name: str
    heading: str
    content: str
    content_type: str
    line_start: int
    line_end: int


class QuizOptionResponse(BaseModel):
    id: str
    text: str


class TopicQuizQuestionResponse(BaseModel):
    id: str
    question: str
    options: list[QuizOptionResponse]
    correct_option_id: str
    explanation: str
    related_step_id: UUID


class TopicQuizResponse(BaseModel):
    topic_id: int
    questions: list[TopicQuizQuestionResponse]


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1_000)
    topic_id: int | None = None
    limit: int = Field(default=8, ge=1, le=20)


class SearchResult(BaseModel):
    section_id: UUID
    document_id: UUID
    score: float
    source_name: str
    logical_key: str
    heading_path: str
    content_type: str
    line_start: int
    line_end: int
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=2, max_length=4_000)
    topic_id: int | None = None
    conversation_id: UUID | None = None
    source_limit: int = Field(default=6, ge=1, le=8)
    pinned_section_ids: list[UUID] = Field(default_factory=list, max_length=16)
    learning_mode: bool = False


class ChatSource(BaseModel):
    index: int
    section_id: UUID | None = None
    source_name: str
    heading_path: str
    line_start: int
    line_end: int
    score: float


class ChatResponse(BaseModel):
    conversation_id: UUID
    answer: str
    model: str
    sources: list[ChatSource]


class FeedbackRequest(BaseModel):
    client_id: str = Field(min_length=8, max_length=64)
    profile_id: UUID | None = None
    kind: Literal["answer_helpful", "answer_unhelpful", "quiz_question_issue", "source_mismatch"]
    topic_id: int | None = None
    conversation_id: UUID | None = None
    source_section_id: UUID | None = None
    payload: dict[str, object] | None = None


class FeedbackResponse(BaseModel):
    id: UUID


class FeedbackStateResponse(BaseModel):
    """İstemcinin daha önce verdiği oyları yeniden çizmesi için dar durum özeti."""

    keys: list[str] = Field(default_factory=list, max_length=500)


class FeedbackReviewRequest(BaseModel):
    review_status: Literal["reviewed", "ignored", "new"]
    review_note: str | None = Field(default=None, max_length=1_000)


class QualityFeedbackResponse(BaseModel):
    id: UUID
    kind: str
    topic_id: int | None
    topic_title: str | None
    source_section_id: UUID | None
    source_name: str | None
    heading_path: str | None
    conversation_id: UUID | None
    payload: dict[str, object] | None
    review_status: str
    review_note: str | None
    created_at: datetime | None
    reviewed_at: datetime | None


class QualityOverviewResponse(BaseModel):
    new_count: int
    reviewed_count: int
    ignored_count: int
    by_kind: dict[str, int]


class QualityIssueMetric(BaseModel):
    key: str
    label: str
    count: int


class QualityInsightsResponse(BaseModel):
    reviewed_candidate_count: int
    topic_issues: list[QualityIssueMetric]
    source_issues: list[QualityIssueMetric]


class QualityExportResponse(BaseModel):
    candidate_count: int
    filename: str
    download_url: str


class TopicProgressPayload(BaseModel):
    completed_step_ids: list[str] = Field(default_factory=list, max_length=10_000)
    review_plans: list[dict[str, object]] = Field(default_factory=list, max_length=10_000)
    last_step_id: str | None = Field(default=None, max_length=64)
    last_visited_at: int = Field(default=0, ge=0)
    quiz_attempts: int = Field(default=0, ge=0)
    last_quiz_score: int | None = Field(default=None, ge=0, le=100)
    last_quiz_at: int = Field(default=0, ge=0)


class ProfileBootstrapRequest(BaseModel):
    client_id: str = Field(min_length=8, max_length=64)
    # İlk açılışta eski tarayıcı ilerlemesi güvenle SQLite'a aktarılır.
    local_progress: dict[str, TopicProgressPayload] = Field(default_factory=dict, max_length=2_000)


class ProfileUpdateRequest(BaseModel):
    client_id: str = Field(min_length=8, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)


class LearnerProfileResponse(BaseModel):
    id: UUID
    client_id: str
    display_name: str
    created_at: datetime | None
    updated_at: datetime | None
    progress: dict[str, TopicProgressPayload] = Field(default_factory=dict)


class TopicProgressUpdateRequest(TopicProgressPayload):
    client_id: str = Field(min_length=8, max_length=64)


class DashboardReviewResponse(BaseModel):
    topic_id: int
    topic_title: str
    step_id: str
    heading: str
    due_at: int
    overdue: bool


class DashboardTopicResponse(BaseModel):
    topic_id: int
    title: str
    completed_steps: int
    total_steps: int
    completion_percent: int
    review_count: int
    last_quiz_score: int | None
    last_quiz_at: int
    is_complete: bool
    is_in_progress: bool


class LearnerDashboardResponse(BaseModel):
    display_name: str
    total_topics: int
    completed_topics: int
    in_progress_topics: int
    total_completed_steps: int
    total_steps: int
    today_reviews: list[DashboardReviewResponse]
    in_progress: list[DashboardTopicResponse]
    recent_quizzes: list[DashboardTopicResponse]


class AuthRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=10, max_length=256)
    profile_id: UUID | None = None
    client_id: str | None = Field(default=None, min_length=8, max_length=64)


class AuthLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=256)
    profile_id: UUID | None = None
    client_id: str | None = Field(default=None, min_length=8, max_length=64)


class AuthSessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: UUID
    username: str
    display_name: str


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    topic_id: int | None
    created_at: datetime | None
    updated_at: datetime | None


class ConversationMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sequence: int
    role: str
    content: str
    model: str | None
    created_at: datetime | None
    sources: list[ChatSource] = Field(default_factory=list)


class ConversationDetail(ConversationSummary):
    messages: list[ConversationMessageResponse]
