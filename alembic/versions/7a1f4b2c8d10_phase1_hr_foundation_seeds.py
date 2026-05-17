"""phase1 hr foundation seeds

Revision ID: 7a1f4b2c8d10
Revises: bc3e8a5da561
Create Date: 2026-05-15 12:00:00.000000

Phase 1.0 HR foundation: creates the employee_id sequence and seeds reference
data (Departments / Designations / Grades / WorkLocations). The HR tables
themselves are created by ``Base.metadata.create_all`` at app startup
(see app/main.py), matching the existing project pattern. This migration
therefore deals only with the SEQUENCE and DEFAULT SEED ROWS that
``create_all`` doesn't cover.

Idempotent: re-runs use ``ON CONFLICT DO NOTHING`` so seeds aren't duplicated.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7a1f4b2c8d10"
down_revision: Union[str, None] = "bc3e8a5da561"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1) Employee ID sequence (EMP0001 → EMP9999+) ---------------------
    op.execute("CREATE SEQUENCE IF NOT EXISTS hr_employee_id_seq START WITH 1 INCREMENT BY 1")

    # --- 2) Seed reference data ------------------------------------------
    # Ensure the HR tables exist before we try to seed them. They are created
    # by Base.metadata.create_all at app boot; this guard makes the migration
    # safe to run before the first app start too.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("hr_departments"):
        return  # tables not created yet; rerun upgrade after first app boot

    op.execute("""
        INSERT INTO hr_departments (id, name, code, is_deleted, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'Engineering', 'ENG', false, NOW(), NOW()),
            (gen_random_uuid(), 'Product',     'PRD', false, NOW(), NOW()),
            (gen_random_uuid(), 'Design',      'DES', false, NOW(), NOW()),
            (gen_random_uuid(), 'Sales',       'SAL', false, NOW(), NOW()),
            (gen_random_uuid(), 'Marketing',   'MKT', false, NOW(), NOW()),
            (gen_random_uuid(), 'Human Resources', 'HR',  false, NOW(), NOW()),
            (gen_random_uuid(), 'Finance',     'FIN', false, NOW(), NOW()),
            (gen_random_uuid(), 'Operations',  'OPS', false, NOW(), NOW())
        ON CONFLICT (code) DO NOTHING
    """)

    op.execute("""
        INSERT INTO hr_grades (id, name, code, band, level, min_ctc, max_ctc, is_deleted, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'Level 1', 'L1', 'IC', 1,  300000,  500000, false, NOW(), NOW()),
            (gen_random_uuid(), 'Level 2', 'L2', 'IC', 2,  500000,  900000, false, NOW(), NOW()),
            (gen_random_uuid(), 'Level 3', 'L3', 'IC', 3,  900000, 1500000, false, NOW(), NOW()),
            (gen_random_uuid(), 'Level 4', 'L4', 'IC', 4, 1500000, 2500000, false, NOW(), NOW()),
            (gen_random_uuid(), 'Level 5', 'L5', 'IC', 5, 2500000, 4000000, false, NOW(), NOW()),
            (gen_random_uuid(), 'Manager 1','M1', 'Mgr', 6, 2200000, 3500000, false, NOW(), NOW()),
            (gen_random_uuid(), 'Manager 2','M2', 'Mgr', 7, 3500000, 6000000, false, NOW(), NOW()),
            (gen_random_uuid(), 'Manager 3','M3', 'Mgr', 8, 6000000, 12000000, false, NOW(), NOW())
        ON CONFLICT (code) DO NOTHING
    """)

    op.execute("""
        INSERT INTO hr_designations (id, name, code, level, is_deleted, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'Junior Engineer',     'DES-JE',   1, false, NOW(), NOW()),
            (gen_random_uuid(), 'Engineer',            'DES-ENG',  2, false, NOW(), NOW()),
            (gen_random_uuid(), 'Senior Engineer',     'DES-SE',   3, false, NOW(), NOW()),
            (gen_random_uuid(), 'Tech Lead',           'DES-TL',   4, false, NOW(), NOW()),
            (gen_random_uuid(), 'Engineering Manager', 'DES-EM',   5, false, NOW(), NOW()),
            (gen_random_uuid(), 'Principal Engineer',  'DES-PE',   5, false, NOW(), NOW()),
            (gen_random_uuid(), 'Product Manager',     'DES-PM',   4, false, NOW(), NOW()),
            (gen_random_uuid(), 'Designer',            'DES-DSGN', 2, false, NOW(), NOW()),
            (gen_random_uuid(), 'Senior Designer',     'DES-SDSGN',3, false, NOW(), NOW()),
            (gen_random_uuid(), 'Sales Executive',     'DES-SX',   2, false, NOW(), NOW()),
            (gen_random_uuid(), 'Sales Manager',       'DES-SM',   4, false, NOW(), NOW()),
            (gen_random_uuid(), 'HR Executive',        'DES-HRX',  2, false, NOW(), NOW()),
            (gen_random_uuid(), 'HR Manager',          'DES-HRM',  4, false, NOW(), NOW()),
            (gen_random_uuid(), 'Accountant',          'DES-ACC',  2, false, NOW(), NOW()),
            (gen_random_uuid(), 'Operations Lead',     'DES-OPL',  4, false, NOW(), NOW())
        ON CONFLICT (code) DO NOTHING
    """)

    op.execute("""
        INSERT INTO hr_work_locations (id, name, address, city, state, country, type, is_deleted, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'HQ — Bengaluru', NULL, 'Bengaluru', 'Karnataka',   'India', 'HQ',     false, NOW(), NOW()),
            (gen_random_uuid(), 'HQ — Mumbai',    NULL, 'Mumbai',    'Maharashtra', 'India', 'BRANCH', false, NOW(), NOW()),
            (gen_random_uuid(), 'Remote — India', NULL, NULL,        NULL,          'India', 'REMOTE', false, NOW(), NOW())
        ON CONFLICT (name) DO NOTHING
    """)


def downgrade() -> None:
    # Drop seeds first (best-effort), then the sequence.
    op.execute("DELETE FROM hr_work_locations WHERE name IN ('HQ — Bengaluru', 'HQ — Mumbai', 'Remote — India')")
    op.execute("""
        DELETE FROM hr_designations WHERE code IN (
            'DES-JE','DES-ENG','DES-SE','DES-TL','DES-EM','DES-PE','DES-PM',
            'DES-DSGN','DES-SDSGN','DES-SX','DES-SM','DES-HRX','DES-HRM',
            'DES-ACC','DES-OPL'
        )
    """)
    op.execute("DELETE FROM hr_grades WHERE code IN ('L1','L2','L3','L4','L5','M1','M2','M3')")
    op.execute("DELETE FROM hr_departments WHERE code IN ('ENG','PRD','DES','SAL','MKT','HR','FIN','OPS')")
    op.execute("DROP SEQUENCE IF EXISTS hr_employee_id_seq")
