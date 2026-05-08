from .auth import router as auth_router
from .projects import router as projects_router
from .dashboard import router as dashboard_router
from .settings import router as settings_router

__all__ = ["auth_router", "projects_router", "dashboard_router", "settings_router"]
