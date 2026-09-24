from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
if settings.database_url.startswith("sqlite"):
    database_path = make_url(settings.database_url).database
    if database_path and database_path != ":memory:":
        Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def migrate_schema() -> None:
    """Küçük SQLite şema güncellemelerini mevcut yerel veriyi silmeden uygular."""
    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    analysis_columns = {column["name"] for column in inspector.get_columns("document_analyses")}
    analysis_additions = {
        "stage": "VARCHAR(32) NOT NULL DEFAULT 'queued'",
        "progress": "INTEGER NOT NULL DEFAULT 0",
        "message": "TEXT",
        "error_message": "TEXT",
        "approval_payload": "JSON",
        "suggested_lesson_title": "VARCHAR(255)",
    }
    document_columns = {column["name"] for column in inspector.get_columns("source_documents")}
    feedback_columns = {column["name"] for column in inspector.get_columns("learner_feedback")}
    progress_columns = {column["name"] for column in inspector.get_columns("learner_topic_progress")}
    conversation_columns = {column["name"] for column in inspector.get_columns("conversations")}
    profile_columns = {column["name"] for column in inspector.get_columns("learner_profiles")}
    asset_columns = {column["name"] for column in inspector.get_columns("document_assets")}
    with engine.begin() as connection:
        for name, definition in analysis_additions.items():
            if name not in analysis_columns:
                connection.execute(text(f"ALTER TABLE document_analyses ADD COLUMN {name} {definition}"))
        if "lesson_title" not in document_columns:
            connection.execute(text("ALTER TABLE source_documents ADD COLUMN lesson_title VARCHAR(255)"))
        if "profile_id" not in feedback_columns:
            connection.execute(text("ALTER TABLE learner_feedback ADD COLUMN profile_id CHAR(32)"))
        if "review_status" not in feedback_columns:
            connection.execute(text("ALTER TABLE learner_feedback ADD COLUMN review_status VARCHAR(16) NOT NULL DEFAULT 'new'"))
        if "review_note" not in feedback_columns:
            connection.execute(text("ALTER TABLE learner_feedback ADD COLUMN review_note TEXT"))
        if "reviewed_at" not in feedback_columns:
            connection.execute(text("ALTER TABLE learner_feedback ADD COLUMN reviewed_at DATETIME"))
        if "last_quiz_at" not in progress_columns:
            connection.execute(text("ALTER TABLE learner_topic_progress ADD COLUMN last_quiz_at INTEGER NOT NULL DEFAULT 0"))
        if "user_id" not in conversation_columns:
            connection.execute(text("ALTER TABLE conversations ADD COLUMN user_id CHAR(32)"))
        if "user_id" not in profile_columns:
            connection.execute(text("ALTER TABLE learner_profiles ADD COLUMN user_id CHAR(32)"))
        asset_additions = {
            "structured_markdown": "TEXT",
            "row_count": "INTEGER",
            "column_count": "INTEGER",
            "analysis_status": "VARCHAR(16) NOT NULL DEFAULT 'pending'",
            "model": "VARCHAR(128)",
            "message": "TEXT",
            "error_message": "TEXT",
            "analyzed_at": "DATETIME",
            "needs_review": "BOOLEAN NOT NULL DEFAULT 0",
            "inclusion_status": "VARCHAR(16) NOT NULL DEFAULT 'pending'",
        }
        for name, definition in asset_additions.items():
            if name not in asset_columns:
                connection.execute(text(f"ALTER TABLE document_assets ADD COLUMN {name} {definition}"))


def get_db() -> Generator[Session, None, None]:
    database = SessionLocal()
    try:
        yield database
    finally:
        database.close()
