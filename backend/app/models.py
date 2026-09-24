import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class DocumentStatus(str, enum.Enum):
    PROCESSING = "processing"
    ACTIVE = "active"
    REPLACED = "replaced"
    FAILED = "failed"


class EmbeddingStatus(str, enum.Enum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class AnalysisStatus(str, enum.Enum):
    # PENDING is retained only to read analyses created by older local installs.
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    APPROVED = "approved"
    FAILED = "failed"


class ConversionStatus(str, enum.Enum):
    """Kaynağın Markdown inceleme/yayın yaşam döngüsü.

    ``READY`` yalnızca dönüşümün tamamlandığını ifade eder. Bu durumdaki bir
    kaynak henüz konu analizine ya da Qdrant indeksine dahil edilmez.
    """

    PROCESSING = "processing"
    READY = "ready"
    PUBLISHED = "published"
    FAILED = "failed"


class AssetAnalysisStatus(str, enum.Enum):
    """Görselin isteğe bağlı VLM açıklama yaşam döngüsü."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class LLMProviderType(str, enum.Enum):
    """Yerel metin üretim sağlayıcılarının sabit, doğrulanabilir kimlikleri."""

    OLLAMA = "ollama"
    VLLM = "vllm"


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class RuntimeModelSettings(Base):
    """Tek makinedeki sistem-geneli model seçimi.

    Gizli anahtar saklamaz. Sunucu adresleri ve model adları, ancak bağlantı
    denemesinden sonra bu kayda yazılır. Embedding ayarı burada bilinçli olarak
    yoktur: onun değişimi tüm Qdrant indeksinin yeniden kurulmasını gerektirir.
    """

    __tablename__ = "runtime_model_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    text_provider: Mapped[LLMProviderType] = mapped_column(Enum(LLMProviderType), default=LLMProviderType.OLLAMA)
    ollama_base_url: Mapped[str] = mapped_column(String(512))
    ollama_model: Mapped[str] = mapped_column(String(255))
    vllm_base_url: Mapped[str] = mapped_column(String(512), default="http://127.0.0.1:8001")
    vllm_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vision_provider: Mapped[LLMProviderType] = mapped_column(Enum(LLMProviderType), default=LLMProviderType.OLLAMA)
    vision_model: Mapped[str] = mapped_column(String(255))
    context_length: Mapped[int] = mapped_column(Integer, default=32768)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    order_index: Mapped[int] = mapped_column(default=0)

    parent: Mapped["Topic | None"] = relationship(remote_side="Topic.id", back_populates="children")
    children: Mapped[list["Topic"]] = relationship(back_populates="parent")


class TopicPrerequisite(Base):
    """Konu sırasını belirleyen, kullanıcı onaylı ön koşul bağı."""

    __tablename__ = "topic_prerequisites"
    __table_args__ = (UniqueConstraint("topic_id", "prerequisite_topic_id", name="uq_topic_prerequisite"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), index=True)
    prerequisite_topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), index=True)

    topic: Mapped["Topic"] = relationship(foreign_keys=[topic_id])
    prerequisite: Mapped["Topic"] = relationship(foreign_keys=[prerequisite_topic_id])


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("logical_key", "version", name="uq_document_logical_key_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    logical_key: Mapped[str] = mapped_column(String(255), index=True)
    source_name: Mapped[str] = mapped_column(String(255))
    # Kaynak dosyasının adı korunur; kullanıcıya gösterilen ders adı ise analiz
    # sonrasında onaylanan bu ayrı alanda tutulur.
    lesson_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_path: Mapped[str] = mapped_column(String(1024))
    media_type: Mapped[str] = mapped_column(String(100))
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus), default=DocumentStatus.PROCESSING, index=True
    )
    byte_size: Mapped[int] = mapped_column(Integer)
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    sections: Mapped[list["DocumentSection"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    conversion: Mapped["DocumentConversion | None"] = relationship(
        back_populates="document", cascade="all, delete-orphan", uselist=False
    )


class DocumentConversion(Base):
    """Orijinal dosyadan üretilmiş, kullanıcı onayını bekleyen Markdown."""

    __tablename__ = "document_conversions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_documents.id"), unique=True, index=True
    )
    status: Mapped[ConversionStatus] = mapped_column(
        Enum(ConversionStatus), default=ConversionStatus.PROCESSING, index=True
    )
    markdown_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    markdown_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    converter: Mapped[str | None] = mapped_column(String(128), nullable=True)
    converter_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ocr_used: Mapped[bool] = mapped_column(default=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    table_count: Mapped[int] = mapped_column(Integer, default=0)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped[SourceDocument] = relationship(back_populates="conversion")
    assets: Mapped[list["DocumentAsset"]] = relationship(
        back_populates="conversion", cascade="all, delete-orphan"
    )
    revisions: Mapped[list["DocumentMarkdownRevision"]] = relationship(
        back_populates="conversion", cascade="all, delete-orphan"
    )


class DocumentMarkdownRevision(Base):
    """Kullanıcının onay öncesinde düzenlediği Markdown'ın değişmez sürümü."""

    __tablename__ = "document_markdown_revisions"
    __table_args__ = (UniqueConstraint("conversion_id", "revision_number", name="uq_conversion_markdown_revision"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_conversions.id"), index=True)
    revision_number: Mapped[int] = mapped_column(Integer)
    content_path: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(64), default="conversion")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversion: Mapped[DocumentConversion] = relationship(back_populates="revisions")


class DocumentAsset(Base):
    """Sayfa görseli, tablo veya diyagram gibi dönüşüm sırasında korunacak varlık."""

    __tablename__ = "document_assets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_conversions.id"), index=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    asset_path: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64))
    structured_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    column_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    needs_review: Mapped[bool] = mapped_column(default=False)
    inclusion_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    analysis_status: Mapped[AssetAnalysisStatus] = mapped_column(
        Enum(AssetAnalysisStatus), default=AssetAnalysisStatus.PENDING, index=True
    )
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversion: Mapped[DocumentConversion] = relationship(back_populates="assets")


class DocumentSection(Base):
    __tablename__ = "document_sections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_documents.id"), index=True)
    parent_section_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_sections.id"), nullable=True, index=True
    )
    heading_path: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(32), default="explanation")
    sequence: Mapped[int] = mapped_column(Integer)
    line_start: Mapped[int] = mapped_column(Integer)
    line_end: Mapped[int] = mapped_column(Integer)
    embedding_status: Mapped[EmbeddingStatus] = mapped_column(
        Enum(EmbeddingStatus), default=EmbeddingStatus.PENDING, index=True
    )

    document: Mapped[SourceDocument] = relationship(back_populates="sections")
    topics: Mapped[list["TopicSource"]] = relationship(back_populates="section", cascade="all, delete-orphan")
    proposed_topics: Mapped[list["SectionTopicProposal"]] = relationship(
        back_populates="section", cascade="all, delete-orphan"
    )


class TopicSource(Base):
    __tablename__ = "topic_sources"
    __table_args__ = (UniqueConstraint("topic_id", "section_id", name="uq_topic_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), index=True)
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_sections.id"), index=True)
    relevance: Mapped[int] = mapped_column(default=50)
    approved: Mapped[bool] = mapped_column(default=False)

    topic: Mapped[Topic] = relationship()
    section: Mapped[DocumentSection] = relationship(back_populates="topics")


class DocumentAnalysis(Base):
    __tablename__ = "document_analyses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_documents.id"), unique=True, index=True)
    status: Mapped[AnalysisStatus] = mapped_column(
        Enum(AnalysisStatus), default=AnalysisStatus.PROCESSING, index=True
    )
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    suggested_lesson_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_payload: Mapped[list[dict[str, str | None]] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped[SourceDocument] = relationship()
    topic_proposals: Mapped[list["TopicProposal"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", order_by="TopicProposal.order_index"
    )


class TopicProposal(Base):
    __tablename__ = "topic_proposals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    analysis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_analyses.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    existing_topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    analysis: Mapped[DocumentAnalysis] = relationship(back_populates="topic_proposals")
    existing_topic: Mapped[Topic | None] = relationship()
    section_proposals: Mapped[list["SectionTopicProposal"]] = relationship(
        back_populates="topic_proposal", cascade="all, delete-orphan"
    )


class SectionTopicProposal(Base):
    __tablename__ = "section_topic_proposals"
    __table_args__ = (UniqueConstraint("section_id", "topic_proposal_id", name="uq_section_topic_proposal"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_sections.id"), index=True)
    topic_proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topic_proposals.id"), index=True)
    content_type: Mapped[str] = mapped_column(String(32), default="explanation")
    confidence: Mapped[int] = mapped_column(Integer, default=50)

    section: Mapped[DocumentSection] = relationship(back_populates="proposed_topics")
    topic_proposal: Mapped[TopicProposal] = relationship(back_populates="section_proposals")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255))
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_accounts.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True
    )

    topic: Mapped["Topic | None"] = relationship()
    user: Mapped["UserAccount | None"] = relationship()
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="ConversationMessage.sequence"
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "sequence", name="uq_conversation_message_sequence"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_snapshot: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class UserAccount(Base):
    __tablename__ = "user_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["UserAccount"] = relationship()


class LearnerProfile(Base):
    """Yerel kurulumdaki, oturum açma gerektirmeyen öğrenci profili.

    ``client_id`` tarayıcının rastgele ve yerel kimliğidir. Bu bir kimlik
    doğrulama mekanizması değildir; aynı cihazdaki ilerleme verisini kalıcı
    tutmak ve eski localStorage verisini bir kez taşımak içindir.
    """

    __tablename__ = "learner_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80), default="Yerel öğrenci")
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_accounts.id"), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    topic_progress: Mapped[list["LearnerTopicProgress"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    feedback: Mapped[list["LearnerFeedback"]] = relationship(back_populates="profile")
    user: Mapped["UserAccount | None"] = relationship()


class LearnerTopicProgress(Base):
    """Bir profilin bir konudaki kalıcı öğrenme ve tekrar planı durumu."""

    __tablename__ = "learner_topic_progress"
    __table_args__ = (UniqueConstraint("profile_id", "topic_id", name="uq_profile_topic_progress"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("learner_profiles.id"), index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), index=True)
    completed_step_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_plans: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    last_step_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_visited_at: Mapped[int] = mapped_column(Integer, default=0)
    quiz_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_quiz_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_quiz_at: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    profile: Mapped["LearnerProfile"] = relationship(back_populates="topic_progress")
    topic: Mapped["Topic"] = relationship()


class LearnerFeedback(Base):
    """Hesap gerektirmeyen yerel istemci geri bildirimi."""

    __tablename__ = "learner_feedback"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("learner_profiles.id"), nullable=True, index=True
    )
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    topic_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    source_section_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    payload: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    review_status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    profile: Mapped["LearnerProfile | None"] = relationship(back_populates="feedback")


class SystemMaintenanceState(Base):
    """Tek kurulum için bakım kilidi; geri yükleme boyunca yazmaları durdurur."""

    __tablename__ = "system_maintenance_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(default=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ApplicationErrorEvent(Base):
    """Beklenmeyen sunucu hatasının güvenli, kalıcı tanı kaydı."""

    __tablename__ = "application_error_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(512))
    error_type: Mapped[str] = mapped_column(String(128))
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
