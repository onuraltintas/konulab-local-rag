"""İncelenip onaylanmış kullanıcı geri bildirimlerini kalite seti adaylarına dönüştürür.

Çalıştırma (API konteynerinde):
    python scripts/export_feedback_candidates.py --output /app/data/evaluations/feedback-candidates.json

Bu betik geri bildirimleri benchmark'a otomatik eklemez. Önce insan incelemesi
gerekir; böylece tek bir yanlış kullanıcı bildirimi kalite ölçümünü bozmaz.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import LearnerFeedback, Topic


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--include-new",
        action="store_true",
        help="İnsan incelemesinden geçmemiş yeni kayıtları da rapora ekler; benchmark'a aktarmayın.",
    )
    args = parser.parse_args()

    database = SessionLocal()
    try:
        query = (
            select(LearnerFeedback, Topic.title)
            .outerjoin(Topic, Topic.id == LearnerFeedback.topic_id)
            .where(LearnerFeedback.kind.in_({"answer_unhelpful", "quiz_question_issue", "source_mismatch"}))
            .order_by(LearnerFeedback.created_at.desc())
        )
        if not args.include_new:
            query = query.where(LearnerFeedback.review_status == "reviewed")
        rows = list(database.execute(query))
    finally:
        database.close()

    candidates = [
        {
            "feedback_id": str(feedback.id),
            "kind": feedback.kind,
            "topic": topic_title,
            "conversation_id": str(feedback.conversation_id) if feedback.conversation_id else None,
            "source_section_id": str(feedback.source_section_id) if feedback.source_section_id else None,
            "payload": feedback.payload,
            "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
            "review_status": feedback.review_status,
            "review_note": feedback.review_note,
        }
        for feedback, topic_title in rows
    ]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(candidates)} kalite adayı yazıldı: {args.output}")


if __name__ == "__main__":
    main()
