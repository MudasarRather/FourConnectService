from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
import traceback
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings

settings = get_settings()

from app.database import engine, Base
# Create FastAPI application
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.API_VERSION,
    description="Internal employee management application for Fourreck",
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json"
)

# Create tables on startup (if not exist)
@app.on_event("startup")
def startup_db():
    Base.metadata.create_all(bind=engine)

# Configure CORS - allow all local origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global exception handler — ensures 500 errors are logged and include CORS headers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback as tb
    with open("crash.log", "w") as f:
        f.write(f"Exception happened: {type(exc).__name__}: {str(exc)}\n")
        f.write(tb.format_exc())
    tb.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
        headers={
            "Access-Control-Allow-Origin": "http://localhost:5173",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*",
        }
    )

from app.routers import (
    auth,
    dashboard,
    projects,
    admin_employees,
    settings as settings_router,
    uploads,
    notifications,
    team,
    milestone,
    activity,
    financials,
    expenses,
    tasks,
    sla,
    handover,
    dpr,
    notes,
    drive,
    archive,
    documents_hub,
    hr,
)


app.include_router(financials.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(admin_employees.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(uploads.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(team.router, prefix="/api")
app.include_router(milestone.router, prefix="/api")
app.include_router(activity.router, prefix="/api")
app.include_router(expenses.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(notes.router, prefix="/api")
app.include_router(sla.router, prefix="/api")
app.include_router(handover.router, prefix="/api")
app.include_router(dpr.router, prefix="/api")
app.include_router(drive.router, prefix="/api")
app.include_router(archive.router, prefix="/api")
app.include_router(documents_hub.router, prefix="/api")
app.include_router(hr.router, prefix="/api")

# Root endpoint
@app.get("/")
def read_root():
    """Root endpoint"""
    return {
        "message": f"Welcome to {settings.PROJECT_NAME} API",
        "version": settings.API_VERSION,
        "docs": "/api/docs"
    }

# Mount uploads directory
# Mount uploads and storage directories
from fastapi.staticfiles import StaticFiles
import os

os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

os.makedirs("storage", exist_ok=True)
app.mount("/storage", StaticFiles(directory="storage"), name="storage")

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


# Custom Swagger UI and ReDoc with Fourconnect favicon
@app.get("/api/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{settings.PROJECT_NAME} - Swagger UI",
        swagger_favicon_url="/static/favicon.svg",
    )


@app.get("/api/redoc", include_in_schema=False)
async def custom_redoc_html():
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=f"{settings.PROJECT_NAME} - ReDoc",
        redoc_favicon_url="/static/favicon.svg",
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("static/favicon.svg", media_type="image/svg+xml")


# Health check endpoint
@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    # Use string import for reload to work
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
    