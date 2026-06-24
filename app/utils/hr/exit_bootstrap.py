"""Bootstrap helper — seed the exit spine when a separation is ACCEPTED.

Mirrors ``onboarding_bootstrap.py``. Called from the exit-case ``accept`` handler
inside the same transaction. Seeds the clearance checklist (from the resolved
policy template or the built-in default), a draft F&F settlement (with an initial
compute), an interview slot, and the two letter document stubs.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.hr.employee import Employee
from app.models.hr.employee_document import DocTemplateType
from app.models.hr.exit_case import ExitCase
from app.models.hr.exit_clearance import ExitClearanceItem
from app.models.hr.exit_interview import ExitInterview
from app.models.hr.exit_settlement import ExitSettlement
from app.models.hr.exit_document import ExitDocument
from app.models.hr.exit_type import (
    ClearanceDepartment, ClearanceItemStatus, InterviewStatus, ExitDocStatus,
)
from app.utils.hr.exit_management.service import (
    DEFAULT_CLEARANCE_TEMPLATE, generate_settlement_number, recompute_clearance_progress,
)
from app.utils.hr.exit_management.settlement_engine import compute_settlement


def bootstrap_exit(db: Session, case: ExitCase, actor_id: Optional[UUID]) -> None:
    """Seed clearance + settlement + interview + document stubs. Caller commits."""
    policy = case.policy

    # ── Former-employee document portal token ──
    # Minted now (at acceptance) so the employee can bookmark their permanent
    # document link from self-service WHILE they still have access — it later
    # surfaces the relieving/experience letters even after ERP login is revoked.
    from app.utils.hr.exit_management.service import ensure_public_token
    ensure_public_token(db, case)

    # ── Clearance checklist ──
    template: List[dict] = []
    if policy and policy.clearance_template:
        template = list(policy.clearance_template)
    if not template:
        template = DEFAULT_CLEARANCE_TEMPLATE

    # Avoid duplicate seeding if the case already has items.
    existing = db.query(ExitClearanceItem.id).filter(ExitClearanceItem.exit_case_id == case.id).first()
    if not existing:
        for t in template:
            try:
                dept = ClearanceDepartment[str(t.get("department"))]
            except Exception:
                continue
            db.add(ExitClearanceItem(
                exit_case_id=case.id,
                department=dept,
                item_key=str(t.get("item_key", ""))[:60] or "item",
                title=str(t.get("title", "Clearance item"))[:200],
                description=t.get("description"),
                is_mandatory=bool(t.get("is_mandatory", True)),
                status=ClearanceItemStatus.PENDING,
                sort_order=int(t.get("sort_order", 0) or 0),
            ))
        db.flush()
        recompute_clearance_progress(db, case)

    # ── Settlement draft + initial compute ──
    if case.settlement is None:
        settlement = ExitSettlement(
            settlement_number=generate_settlement_number(db),
            exit_case_id=case.id,
            employee_id=case.employee_id,
            created_by_id=actor_id,
        )
        db.add(settlement)
        db.flush()
        try:
            compute_settlement(db, case, settlement)
        except Exception:
            import traceback
            traceback.print_exc()

    # ── Interview slot ──
    # The slot is only *reserved* on acceptance — it is NOT scheduled yet. HR must
    # explicitly schedule/invite it from the Exit Interviews workspace, which is what
    # surfaces the appointment (or self-service survey) to the employee. Seeding it as
    # PENDING with no mode closes the loophole where the employee could complete a
    # survey before HR had scheduled anything.
    if case.interview is None:
        db.add(ExitInterview(
            exit_case_id=case.id,
            status=InterviewStatus.PENDING,
            mode=None,
            responses=[],
            ratings={},
        ))
        db.flush()

    # ── Letter document stubs ──
    have = {d.doc_type for d in (case.documents or [])}
    for dt in (DocTemplateType.EXPERIENCE_LETTER, DocTemplateType.RELIEVING_LETTER):
        if dt not in have:
            db.add(ExitDocument(
                exit_case_id=case.id,
                doc_type=dt,
                status=ExitDocStatus.NOT_GENERATED,
            ))
    db.flush()
