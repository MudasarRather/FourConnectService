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

router = APIRouter()
router.include_router(_dashboard_router)
router.include_router(_employees_router)
router.include_router(_departments_router)
router.include_router(_designations_router)
router.include_router(_grades_router)
router.include_router(_locations_router)
