from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router
from app.config import settings
from app.db import Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Extensible AI agent-powered Accounts Payable "
        "reference project."
    ),
)
app.include_router(router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.app_env,
        "llm_provider": settings.llm_provider,
        "smtp_dry_run": settings.smtp_dry_run,
    }
