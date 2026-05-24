"""phase 2 onboarding

Revision ID: b3c6dafe1c20
Revises: a2f5b9c7d3e4
Create Date: 2026-05-20

Adds the full Phase-2 HR Onboarding spine:
  - hr_onboarding_processes (1:1 with hr_employees)
  - hr_onboarding_checklist_templates + items
  - hr_onboarding_documents
  - hr_joining_approvals
  - hr_onboarding_tasks
  - hr_employee_identities (1:1)
  - hr_welcome_kit_templates + hr_welcome_kits
  - hr_assets + hr_asset_allocations
  - hr_training_programs + hr_training_assignments
  - hr_induction_sessions + hr_induction_attendance
  - hr_account_provisioning

Also adds drive_documents.employee_id (nullable) so existing drive uploads can
be back-linked to an employee.

Seeds: 10 default checklist templates + 1 default Welcome Kit template.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b3c6dafe1c20"
down_revision = "a2f5b9c7d3e4"
branch_labels = None
depends_on = None


# ──────────────────────────────────────────────────────────────────────────────
# Enums (created once, reused everywhere)
# ──────────────────────────────────────────────────────────────────────────────
_ENUMS = {
    "hr_onboarding_status": ["NOT_STARTED", "IN_PROGRESS", "ON_HOLD", "COMPLETED", "CANCELLED"],
    "hr_onboarding_stage": ["PRE_JOIN", "APPROVAL", "DOCS", "IDENTITY", "ASSETS", "TRAINING", "ACTIVE"],
    "hr_onb_checklist_category": ["HR", "IT", "ADMIN", "FINANCE", "SECURITY", "DEPARTMENT"],
    "hr_onb_checklist_item_status": ["PENDING", "IN_PROGRESS", "COMPLETED", "BLOCKED", "WAIVED"],
    "hr_onb_doc_status": ["PENDING", "UPLOADED", "VERIFIED", "REJECTED", "EXPIRED"],
    "hr_joining_approver_role": ["HR_HEAD", "DEPT_HEAD", "FINANCE", "IT", "SECURITY", "OTHER"],
    "hr_joining_approval_status": ["PENDING", "APPROVED", "REJECTED", "WAIVED"],
    "hr_onb_task_status": ["TODO", "IN_PROGRESS", "BLOCKED", "DONE", "CANCELLED"],
    "hr_onb_task_priority": ["LOW", "MEDIUM", "HIGH", "URGENT"],
    "hr_employee_identity_status": ["PENDING", "ISSUED", "REVOKED"],
    "hr_welcome_kit_status": ["PENDING", "PACKED", "DISPATCHED", "DELIVERED"],
    "hr_asset_type": [
        "LAPTOP", "DESKTOP", "MOBILE", "SIM", "RFID_CARD", "ID_CARD",
        "HEADSET", "MONITOR", "KEYBOARD", "MOUSE", "VEHICLE", "KEYS", "OTHER",
    ],
    "hr_asset_condition": ["NEW", "GOOD", "FAIR", "POOR", "RETIRED"],
    "hr_asset_status": ["AVAILABLE", "ALLOCATED", "RESERVED", "MAINTENANCE", "RETIRED"],
    "hr_asset_allocation_status": ["ALLOCATED", "RETURNED", "LOST", "DAMAGED"],
    "hr_training_type": [
        "HR_ORIENTATION", "SECURITY", "SOFTWARE", "COMPLIANCE", "SAFETY", "DEPARTMENT", "OTHER",
    ],
    "hr_training_assignment_status": ["NOT_STARTED", "IN_PROGRESS", "COMPLETED", "FAILED", "WAIVED"],
    "hr_induction_type": [
        "WELCOME", "DEPT_ORIENTATION", "POLICY", "COMPLIANCE", "TEAM_INTRO", "SAFETY", "OTHER",
    ],
    "hr_induction_attendance_status": ["INVITED", "CONFIRMED", "ATTENDED", "MISSED", "EXCUSED"],
    "hr_account_type": [
        "ERP", "EMAIL", "VPN", "BIOMETRIC", "ATTENDANCE", "RFID_SYSTEM", "GIT", "SLACK", "DRIVE", "OTHER",
    ],
    "hr_account_provisioning_status": ["PENDING", "REQUESTED", "ACTIVE", "REVOKED", "FAILED"],
}


def _enum(name: str):
    return postgresql.ENUM(*_ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()

    # 0. drive_documents.employee_id
    op.add_column(
        "drive_documents",
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_drive_documents_employee_id", "drive_documents", ["employee_id"], unique=False)

    # 1. Create all enums on the connection.
    for name, values in _ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    # 2. hr_onboarding_processes ────────────────────────────────────────────────
    op.create_table(
        "hr_onboarding_processes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_rec_offers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", _enum("hr_onboarding_status"), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("current_stage", _enum("hr_onboarding_stage"), nullable=False, server_default="PRE_JOIN"),
        sa.Column("progress_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target_joining_date", sa.Date(), nullable=True),
        sa.Column("actual_joining_date", sa.Date(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("on_hold_reason", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("last_updated_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_hr_onb_status_stage", "hr_onboarding_processes", ["status", "current_stage"])
    op.create_index("ix_hr_onboarding_processes_employee_id", "hr_onboarding_processes", ["employee_id"], unique=True)
    op.create_index("ix_hr_onboarding_processes_target_joining_date", "hr_onboarding_processes", ["target_joining_date"])

    # 3. hr_onboarding_checklist_templates
    op.create_table(
        "hr_onboarding_checklist_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("category", _enum("hr_onb_checklist_category"), nullable=False),
        sa.Column("task_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_assignee_role", sa.String(60), nullable=True),
        sa.Column("default_due_offset_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 4. hr_onboarding_checklist_items
    op.create_table(
        "hr_onboarding_checklist_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_onboarding_processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_onboarding_checklist_templates.id"), nullable=True),
        sa.Column("category", _enum("hr_onb_checklist_category"), nullable=False),
        sa.Column("task_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("assigned_to_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", _enum("hr_onb_checklist_item_status"), nullable=False, server_default="PENDING"),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("completed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hr_onb_checklist_items_process_id", "hr_onboarding_checklist_items", ["process_id"])
    op.create_index("ix_hr_onb_checklist_items_category", "hr_onboarding_checklist_items", ["category"])
    op.create_index("ix_hr_onb_checklist_items_status", "hr_onboarding_checklist_items", ["status"])

    # 5. hr_onboarding_documents
    op.create_table(
        "hr_onboarding_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_onboarding_processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("doc_type_key", sa.String(60), nullable=False),
        sa.Column("doc_type_label", sa.String(160), nullable=False),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("drive_document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drive_documents.id"), nullable=True),
        sa.Column("status", _enum("hr_onb_doc_status"), nullable=False, server_default="PENDING"),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("ocr_data", postgresql.JSONB(), nullable=True),
        sa.Column("verified_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hr_onb_documents_process_id", "hr_onboarding_documents", ["process_id"])
    op.create_index("ix_hr_onb_documents_doc_type_key", "hr_onboarding_documents", ["doc_type_key"])
    op.create_index("ix_hr_onb_documents_status", "hr_onboarding_documents", ["status"])

    # 6. hr_joining_approvals
    op.create_table(
        "hr_joining_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_onboarding_processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("approver_role", _enum("hr_joining_approver_role"), nullable=False),
        sa.Column("approver_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", _enum("hr_joining_approval_status"), nullable=False, server_default="PENDING"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hr_joining_approvals_process_id", "hr_joining_approvals", ["process_id"])
    op.create_index("ix_hr_joining_approvals_status", "hr_joining_approvals", ["status"])

    # 7. hr_onboarding_tasks
    op.create_table(
        "hr_onboarding_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_onboarding_processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", _enum("hr_onb_checklist_category"), nullable=True),
        sa.Column("assigned_to_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", _enum("hr_onb_task_status"), nullable=False, server_default="TODO"),
        sa.Column("priority", _enum("hr_onb_task_priority"), nullable=False, server_default="MEDIUM"),
        sa.Column("depends_on_task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_onboarding_tasks.id"), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("sla_hours", sa.Integer(), nullable=True),
        sa.Column("escalation_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hr_onb_tasks_process_id", "hr_onboarding_tasks", ["process_id"])
    op.create_index("ix_hr_onb_tasks_status", "hr_onboarding_tasks", ["status"])
    op.create_index("ix_hr_onb_tasks_priority", "hr_onboarding_tasks", ["priority"])

    # 8. hr_employee_identities
    op.create_table(
        "hr_employee_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("official_email", sa.String(200), nullable=True, unique=True),
        sa.Column("biometric_id", sa.String(60), nullable=True, unique=True),
        sa.Column("rfid_card_number", sa.String(60), nullable=True, unique=True),
        sa.Column("access_card_number", sa.String(60), nullable=True),
        sa.Column("username", sa.String(120), nullable=True),
        sa.Column("photo_url", sa.String(600), nullable=True),
        sa.Column("qr_payload", sa.String(600), nullable=True),
        sa.Column("status", _enum("hr_employee_identity_status"), nullable=False, server_default="PENDING"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 9. Welcome kit templates + kits
    op.create_table(
        "hr_welcome_kit_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_items", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "hr_welcome_kits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_onboarding_processes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_welcome_kit_templates.id"), nullable=True),
        sa.Column("status", _enum("hr_welcome_kit_status"), nullable=False, server_default="PENDING"),
        sa.Column("items", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("tracking_number", sa.String(120), nullable=True),
        sa.Column("packed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("packed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hr_welcome_kits_employee_id", "hr_welcome_kits", ["employee_id"])

    # 10. Assets + allocations
    op.create_table(
        "hr_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_code", sa.String(60), nullable=False, unique=True),
        sa.Column("asset_type", _enum("hr_asset_type"), nullable=False),
        sa.Column("brand", sa.String(120), nullable=True),
        sa.Column("model", sa.String(160), nullable=True),
        sa.Column("serial_number", sa.String(120), nullable=True),
        sa.Column("purchase_date", sa.Date(), nullable=True),
        sa.Column("purchase_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("condition", _enum("hr_asset_condition"), nullable=False, server_default="NEW"),
        sa.Column("status", _enum("hr_asset_status"), nullable=False, server_default="AVAILABLE"),
        sa.Column("assigned_employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_employees.id"), nullable=True),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_work_locations.id"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("extra", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_hr_assets_type_status", "hr_assets", ["asset_type", "status"])
    op.create_index("ix_hr_assets_serial_number", "hr_assets", ["serial_number"])
    op.create_index("ix_hr_assets_assigned_employee_id", "hr_assets", ["assigned_employee_id"])
    op.create_table(
        "hr_asset_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_onboarding_processes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("allocated_date", sa.Date(), nullable=False, server_default=sa.func.current_date()),
        sa.Column("expected_return_date", sa.Date(), nullable=True),
        sa.Column("returned_date", sa.Date(), nullable=True),
        sa.Column("condition_on_issue", _enum("hr_asset_condition"), nullable=True),
        sa.Column("condition_on_return", _enum("hr_asset_condition"), nullable=True),
        sa.Column("status", _enum("hr_asset_allocation_status"), nullable=False, server_default="ALLOCATED"),
        sa.Column("acknowledged_by_employee", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("returned_to_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hr_asset_alloc_asset_id", "hr_asset_allocations", ["asset_id"])
    op.create_index("ix_hr_asset_alloc_employee_id", "hr_asset_allocations", ["employee_id"])
    op.create_index("ix_hr_asset_alloc_status", "hr_asset_allocations", ["status"])

    # 11. Training
    op.create_table(
        "hr_training_programs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("code", sa.String(40), nullable=True, unique=True),
        sa.Column("training_type", _enum("hr_training_type"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("duration_hours", sa.Numeric(6, 2), nullable=True),
        sa.Column("trainer_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("certification_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_mandatory_for_new_joiners", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("materials_url", sa.String(600), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_hr_training_programs_type", "hr_training_programs", ["training_type"])
    op.create_index("ix_hr_training_programs_mandatory", "hr_training_programs", ["is_mandatory_for_new_joiners"])
    op.create_table(
        "hr_training_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_training_programs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_onboarding_processes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assigned_date", sa.Date(), nullable=False, server_default=sa.func.current_date()),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("completion_date", sa.Date(), nullable=True),
        sa.Column("status", _enum("hr_training_assignment_status"), nullable=False, server_default="NOT_STARTED"),
        sa.Column("score", sa.Numeric(5, 2), nullable=True),
        sa.Column("certification_url", sa.String(600), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hr_training_assign_program_id", "hr_training_assignments", ["program_id"])
    op.create_index("ix_hr_training_assign_employee_id", "hr_training_assignments", ["employee_id"])
    op.create_index("ix_hr_training_assign_status", "hr_training_assignments", ["status"])

    # 12. Induction
    op.create_table(
        "hr_induction_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("session_type", _enum("hr_induction_type"), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True, server_default="60"),
        sa.Column("location", sa.String(240), nullable=True),
        sa.Column("meeting_url", sa.String(600), nullable=True),
        sa.Column("host_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("agenda", sa.Text(), nullable=True),
        sa.Column("materials_url", sa.String(600), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_hr_induction_sessions_type", "hr_induction_sessions", ["session_type"])
    op.create_index("ix_hr_induction_sessions_scheduled_at", "hr_induction_sessions", ["scheduled_at"])
    op.create_table(
        "hr_induction_attendance",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_induction_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_onboarding_processes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", _enum("hr_induction_attendance_status"), nullable=False, server_default="INVITED"),
        sa.Column("rating", sa.Numeric(3, 1), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("rsvp_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hr_induction_attendance_session_emp", "hr_induction_attendance", ["session_id", "employee_id"], unique=True)

    # 13. Account provisioning
    op.create_table(
        "hr_account_provisioning",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hr_onboarding_processes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("account_type", _enum("hr_account_type"), nullable=False),
        sa.Column("system_username", sa.String(200), nullable=True),
        sa.Column("status", _enum("hr_account_provisioning_status"), nullable=False, server_default="PENDING"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("fulfilled_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hr_account_emp_type", "hr_account_provisioning", ["employee_id", "account_type"], unique=True)

    # ────────────── Seed data ──────────────
    op.execute("""
        INSERT INTO hr_onboarding_checklist_templates
            (id, category, task_name, description, default_assignee_role, default_due_offset_days,
             is_active, is_mandatory, sort_order, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'HR',         'Verify Aadhaar',           'Verify Aadhaar last-4 against ID proof.',         'HR_OFFICER', 1, true, true, 10, NOW(), NOW()),
            (gen_random_uuid(), 'HR',         'Create Employee ID',       'Allocate EMP#### and update mirrors.',            'HR_OFFICER', 0, true, true, 20, NOW(), NOW()),
            (gen_random_uuid(), 'IT',         'Create Email Account',     'Provision official @company email.',              'IT_ADMIN',   1, true, true, 30, NOW(), NOW()),
            (gen_random_uuid(), 'IT',         'Allocate Laptop',          'Issue laptop + accessories.',                     'IT_ADMIN',   2, true, true, 40, NOW(), NOW()),
            (gen_random_uuid(), 'ADMIN',      'Issue RFID Card',          'Issue building access RFID + photo capture.',     'ADMIN',      2, true, true, 50, NOW(), NOW()),
            (gen_random_uuid(), 'FINANCE',    'Salary Account Verify',    'Verify bank account + IFSC for payroll.',         'FINANCE',    3, true, true, 60, NOW(), NOW()),
            (gen_random_uuid(), 'HR',         'PF/ESI Registration',      'Statutory registration + UAN linkage.',           'HR_OFFICER', 7, true, true, 70, NOW(), NOW()),
            (gen_random_uuid(), 'DEPARTMENT', 'Department Orientation',   'Reporting manager runs intro session.',           'DEPT_HEAD',  3, true, true, 80, NOW(), NOW()),
            (gen_random_uuid(), 'HR',         'NDA Signing',              'E-signed NDA + acknowledgement.',                 'HR_OFFICER', 1, true, true, 90, NOW(), NOW()),
            (gen_random_uuid(), 'ADMIN',      'Attendance Enrollment',    'Biometric enrolment + attendance system entry.',  'ADMIN',      2, true, true, 100, NOW(), NOW())
    """)

    op.execute("""
        INSERT INTO hr_welcome_kit_templates (id, name, description, default_items, is_active, is_deleted, created_at, updated_at)
        VALUES (
            gen_random_uuid(),
            'Standard New Joiner Kit',
            'Default welcome kit for new full-time joiners.',
            '[
              {"item_name": "Welcome letter", "qty": 1, "included": true},
              {"item_name": "Company T-shirt", "qty": 1, "included": true},
              {"item_name": "Notebook & pen", "qty": 1, "included": true},
              {"item_name": "Sticker pack", "qty": 1, "included": true},
              {"item_name": "Coffee mug", "qty": 1, "included": true}
            ]'::jsonb,
            true, false, NOW(), NOW()
        )
    """)


def downgrade() -> None:
    op.drop_index("ix_hr_account_emp_type", table_name="hr_account_provisioning")
    op.drop_table("hr_account_provisioning")

    op.drop_index("ix_hr_induction_attendance_session_emp", table_name="hr_induction_attendance")
    op.drop_table("hr_induction_attendance")
    op.drop_index("ix_hr_induction_sessions_scheduled_at", table_name="hr_induction_sessions")
    op.drop_index("ix_hr_induction_sessions_type", table_name="hr_induction_sessions")
    op.drop_table("hr_induction_sessions")

    op.drop_index("ix_hr_training_assign_status", table_name="hr_training_assignments")
    op.drop_index("ix_hr_training_assign_employee_id", table_name="hr_training_assignments")
    op.drop_index("ix_hr_training_assign_program_id", table_name="hr_training_assignments")
    op.drop_table("hr_training_assignments")
    op.drop_index("ix_hr_training_programs_mandatory", table_name="hr_training_programs")
    op.drop_index("ix_hr_training_programs_type", table_name="hr_training_programs")
    op.drop_table("hr_training_programs")

    op.drop_index("ix_hr_asset_alloc_status", table_name="hr_asset_allocations")
    op.drop_index("ix_hr_asset_alloc_employee_id", table_name="hr_asset_allocations")
    op.drop_index("ix_hr_asset_alloc_asset_id", table_name="hr_asset_allocations")
    op.drop_table("hr_asset_allocations")
    op.drop_index("ix_hr_assets_assigned_employee_id", table_name="hr_assets")
    op.drop_index("ix_hr_assets_serial_number", table_name="hr_assets")
    op.drop_index("ix_hr_assets_type_status", table_name="hr_assets")
    op.drop_table("hr_assets")

    op.drop_index("ix_hr_welcome_kits_employee_id", table_name="hr_welcome_kits")
    op.drop_table("hr_welcome_kits")
    op.drop_table("hr_welcome_kit_templates")

    op.drop_table("hr_employee_identities")

    op.drop_index("ix_hr_onb_tasks_priority", table_name="hr_onboarding_tasks")
    op.drop_index("ix_hr_onb_tasks_status", table_name="hr_onboarding_tasks")
    op.drop_index("ix_hr_onb_tasks_process_id", table_name="hr_onboarding_tasks")
    op.drop_table("hr_onboarding_tasks")

    op.drop_index("ix_hr_joining_approvals_status", table_name="hr_joining_approvals")
    op.drop_index("ix_hr_joining_approvals_process_id", table_name="hr_joining_approvals")
    op.drop_table("hr_joining_approvals")

    op.drop_index("ix_hr_onb_documents_status", table_name="hr_onboarding_documents")
    op.drop_index("ix_hr_onb_documents_doc_type_key", table_name="hr_onboarding_documents")
    op.drop_index("ix_hr_onb_documents_process_id", table_name="hr_onboarding_documents")
    op.drop_table("hr_onboarding_documents")

    op.drop_index("ix_hr_onb_checklist_items_status", table_name="hr_onboarding_checklist_items")
    op.drop_index("ix_hr_onb_checklist_items_category", table_name="hr_onboarding_checklist_items")
    op.drop_index("ix_hr_onb_checklist_items_process_id", table_name="hr_onboarding_checklist_items")
    op.drop_table("hr_onboarding_checklist_items")
    op.drop_table("hr_onboarding_checklist_templates")

    op.drop_index("ix_hr_onboarding_processes_target_joining_date", table_name="hr_onboarding_processes")
    op.drop_index("ix_hr_onboarding_processes_employee_id", table_name="hr_onboarding_processes")
    op.drop_index("ix_hr_onb_status_stage", table_name="hr_onboarding_processes")
    op.drop_table("hr_onboarding_processes")

    op.drop_index("ix_drive_documents_employee_id", table_name="drive_documents")
    op.drop_column("drive_documents", "employee_id")

    bind = op.get_bind()
    for name in reversed(list(_ENUMS.keys())):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
