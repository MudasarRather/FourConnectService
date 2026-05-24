"""HR routers package — aggregates all HR sub-routers under a single APIRouter
so main.py can register the package with one include_router call.

Sub-routers each set their own prefix (e.g. ``/hr/employees``); this package
mounts them flat so URLs stay clean and stable.
"""
from fastapi import APIRouter

from app.routers.hr.dashboard import router as _dashboard_router
from app.routers.hr.employees import router as _employees_router
from app.routers.hr.departments import router as _departments_router
from app.routers.hr.designations import router as _designations_router
from app.routers.hr.grades import router as _grades_router
from app.routers.hr.locations import router as _locations_router
from app.routers.hr.recruitment import router as _recruitment_router
from app.routers.hr.onboarding import router as _onboarding_router
from app.routers.hr.onboarding_documents import router as _onb_documents_router
from app.routers.hr.identity import router as _identity_router
from app.routers.hr.assets import router as _assets_router
from app.routers.hr.training import router as _training_router
from app.routers.hr.induction import router as _induction_router
from app.routers.hr.account_provisioning import router as _account_provisioning_router
from app.routers.hr.welcome_kit import router as _welcome_kit_router

router = APIRouter()
router.include_router(_dashboard_router)
router.include_router(_employees_router)
router.include_router(_departments_router)
router.include_router(_designations_router)
router.include_router(_grades_router)
router.include_router(_locations_router)
router.include_router(_recruitment_router)
router.include_router(_onboarding_router)
router.include_router(_onb_documents_router)
router.include_router(_identity_router)
router.include_router(_assets_router)
router.include_router(_training_router)
router.include_router(_induction_router)
router.include_router(_account_provisioning_router)
router.include_router(_welcome_kit_router)
