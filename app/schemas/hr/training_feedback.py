"""HR Training & Development — Training feedback schemas."""
from datetime import datetime
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TrainingFeedbackCreate(BaseModel):
    program_id: Optional[UUID] = None
    assignment_id: Optional[UUID] = None
    trainer_id: Optional[UUID] = None
    rating: int
    content_rating: Optional[int] = None
    trainer_rating: Optional[int] = None
    relevance_rating: Optional[int] = None
    comments: Optional[str] = None
    would_recommend: Optional[bool] = None
    is_anonymous: bool = False


class TrainingFeedbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    program_id: Optional[UUID] = None
    program_name: Optional[str] = None
    assignment_id: Optional[UUID] = None
    trainer_id: Optional[UUID] = None
    trainer_name: Optional[str] = None
    employee_id: UUID
    employee_name: Optional[str] = None
    rating: int
    content_rating: Optional[int] = None
    trainer_rating: Optional[int] = None
    relevance_rating: Optional[int] = None
    comments: Optional[str] = None
    would_recommend: Optional[bool] = None
    is_anonymous: bool
    created_at: datetime


class FeedbackSummaryRow(BaseModel):
    program_id: Optional[UUID] = None
    program_name: Optional[str] = None
    avg_rating: float
    avg_content: Optional[float] = None
    avg_trainer: Optional[float] = None
    avg_relevance: Optional[float] = None
    response_count: int
    recommend_rate: Optional[float] = None


class FeedbackSummaryResponse(BaseModel):
    overall_avg: Optional[float] = None
    total_responses: int
    by_program: List[FeedbackSummaryRow] = []
