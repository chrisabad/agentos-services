"""FastAPI router for the Reports Service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from services.reports.db import get_db
from services.reports.models import (
    FeedbackWrite,
    JunoReviewWrite,
    Report,
    ReportCreate,
    ReportPatch,
    ReportRead,
    valid_transition,
)

router = APIRouter(prefix="/reports", tags=["reports"])
DbDep = Annotated[Session, Depends(get_db)]


@router.post("", response_model=ReportRead, status_code=201)
def create_report(req: ReportCreate, db: DbDep):
    report = Report(
        source=req.source,
        topic_class=req.topic_class,
        storage_type=req.storage_type,
        storage_url=req.storage_url,
        storage_doc_id=req.storage_doc_id,
        sources_cited=req.sources_cited,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return ReportRead.model_validate(report)


@router.get("", response_model=list[ReportRead])
def list_reports(
    db: DbDep,
    state: str | None = Query(default=None),
    topic_class: str | None = Query(default=None),
    source: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    q = db.query(Report)
    if state:
        q = q.filter(Report.state == state)
    if topic_class:
        q = q.filter(Report.topic_class == topic_class)
    if source:
        q = q.filter(Report.source == source)
    rows = q.order_by(Report.created_at.desc()).limit(limit).all()
    return [ReportRead.model_validate(r) for r in rows]


@router.patch("/{report_id}", response_model=ReportRead)
def update_report(report_id: uuid.UUID, req: ReportPatch, db: DbDep):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"report {report_id} not found")

    if not valid_transition(report.state, req.state):
        raise HTTPException(
            status_code=422,
            detail=f"invalid transition: {report.state} → {req.state}",
        )

    report.state = req.state
    report.updated_at = datetime.now(tz=timezone.utc)

    if req.storage_url is not None:
        report.storage_url = req.storage_url
    if req.storage_doc_id is not None:
        report.storage_doc_id = req.storage_doc_id

    if req.state == "published":
        report.published_at = datetime.now(tz=timezone.utc)
        report.published_version = (report.published_version or 0) + 1
    if req.state == "reviewed":
        report.draft_version = report.draft_version + 1

    db.commit()
    db.refresh(report)
    return ReportRead.model_validate(report)


@router.post("/{report_id}/juno-review", response_model=ReportRead)
def write_juno_review(report_id: uuid.UUID, req: JunoReviewWrite, db: DbDep):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"report {report_id} not found")

    report.juno_review = {
        "reviewed_by": req.reviewed_by,
        "edits_summary": req.edits_summary,
        "kicked_back_to": req.kicked_back_to,
        "reviewed_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    if report.state == "drafted":
        report.state = "reviewed"
    report.updated_at = datetime.now(tz=timezone.utc)
    db.commit()
    db.refresh(report)
    return ReportRead.model_validate(report)


@router.post("/{report_id}/feedback", response_model=ReportRead)
def write_feedback(report_id: uuid.UUID, req: FeedbackWrite, db: DbDep):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"report {report_id} not found")

    report.feedback = {
        "sentiment": req.sentiment,
        "reason": req.reason,
        "notes": req.notes,
        "reactions": req.reactions,
        "responded_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    report.updated_at = datetime.now(tz=timezone.utc)
    db.commit()
    db.refresh(report)
    return ReportRead.model_validate(report)
