"""HR Training & Development — Training feedback (admin read + summary).

Feedback is captured by employees via the self-service endpoint; HR reads and
aggregates it here.
"""
from __future__ import annotations

from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import func, cast, Integer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.training import TrainingProgram
from app.models.hr.trainer import Trainer
from app.models.hr.training_feedback import TrainingFeedback
from app.schemas.hr.training_feedback import (
    TrainingFeedbackResponse, FeedbackSummaryResponse, FeedbackSummaryRow,
)
from app.utils.hr.training.service import emp_display
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/training", tags=["HR — Training Feedback"])


def _resp(db: Session, f: TrainingFeedback) -> TrainingFeedbackResponse:
    disp = {} if f.is_anonymous else emp_display(db, f.employee_id)
    pname = None
    if f.program_id:
        r = db.query(TrainingProgram.name).filter(TrainingProgram.id == f.program_id).first()
        pname = r[0] if r else None
    tname = None
    if f.trainer_id:
        r = db.query(Trainer.name).filter(Trainer.id == f.trainer_id).first()
        tname = r[0] if r else None
    return TrainingFeedbackResponse(
        id=f.id, program_id=f.program_id, program_name=pname, assignment_id=f.assignment_id,
        trainer_id=f.trainer_id, trainer_name=tname, employee_id=f.employee_id,
        employee_name=("Anonymous" if f.is_anonymous else disp.get("name")),
        rating=f.rating, content_rating=f.content_rating, trainer_rating=f.trainer_rating,
        relevance_rating=f.relevance_rating, comments=f.comments,
        would_recommend=f.would_recommend, is_anonymous=f.is_anonymous, created_at=f.created_at,
    )


@router.get("/feedback", response_model=List[TrainingFeedbackResponse])
def list_feedback(
    program_id: Optional[UUID] = None,
    trainer_id: Optional[UUID] = None,
    assignment_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(TrainingFeedback)
    if program_id:
        q = q.filter(TrainingFeedback.program_id == program_id)
    if trainer_id:
        q = q.filter(TrainingFeedback.trainer_id == trainer_id)
    if assignment_id:
        q = q.filter(TrainingFeedback.assignment_id == assignment_id)
    rows = q.order_by(TrainingFeedback.created_at.desc()).limit(500).all()
    return [_resp(db, f) for f in rows]


@router.get("/feedback/summary", response_model=FeedbackSummaryResponse)
def feedback_summary(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    overall = db.query(func.avg(TrainingFeedback.rating), func.count(TrainingFeedback.id)).first()
    overall_avg = round(float(overall[0]), 2) if overall and overall[0] is not None else None
    total = int(overall[1] or 0) if overall else 0

    rows = (
        db.query(
            TrainingFeedback.program_id,
            func.avg(TrainingFeedback.rating),
            func.avg(TrainingFeedback.content_rating),
            func.avg(TrainingFeedback.trainer_rating),
            func.avg(TrainingFeedback.relevance_rating),
            func.count(TrainingFeedback.id),
            func.avg(cast(TrainingFeedback.would_recommend, Integer)),
        )
        .group_by(TrainingFeedback.program_id)
        .all()
    )
    by_program: List[FeedbackSummaryRow] = []
    for pid, avg_r, avg_c, avg_t, avg_rel, cnt, rec in rows:
        pname = None
        if pid:
            r = db.query(TrainingProgram.name).filter(TrainingProgram.id == pid).first()
            pname = r[0] if r else None
        by_program.append(FeedbackSummaryRow(
            program_id=pid, program_name=pname,
            avg_rating=round(float(avg_r), 2) if avg_r is not None else 0.0,
            avg_content=round(float(avg_c), 2) if avg_c is not None else None,
            avg_trainer=round(float(avg_t), 2) if avg_t is not None else None,
            avg_relevance=round(float(avg_rel), 2) if avg_rel is not None else None,
            response_count=int(cnt or 0),
            recommend_rate=round(float(rec) * 100, 1) if rec is not None else None,
        ))
    return FeedbackSummaryResponse(overall_avg=overall_avg, total_responses=total, by_program=by_program)
