from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import settings
from app.db import Base, engine


logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auto_create_agent_tables:
        logger.warning(
            "Agent table auto-create is enabled "
            "(environment=%s, database=%s).",
            settings.app_env,
            settings.database_backend,
        )
        Base.metadata.create_all(bind=engine)
        logger.info("Agent table auto-create completed.")
    else:
        logger.info(
            "Agent table auto-create skipped "
            "(environment=%s, database=%s).",
            settings.app_env,
            settings.database_backend,
        )

    yield

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Extensible AI agent-powered Accounts Payable "
        "reference project."
    ),
    lifespan=lifespan,
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
