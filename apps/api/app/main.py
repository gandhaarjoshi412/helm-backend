from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apps.api.app.config import settings
from apps.api.app.database import init_db
from apps.api.app.redis_client import redis_manager
from apps.api.app.routers import approvals, changes, context, events, projects, system, tasks
from apps.api.app.worker import worker
from packages.shared.errors import HELMError
from packages.shared.logging import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing HELM API Server...")
    await init_db()
    await redis_manager.connect()

    # Start background worker task
    worker_task = asyncio.create_task(worker.start())

    yield

    # Shutdown
    logger.info("Shutting down HELM API Server...")
    worker.stop()
    worker_task.cancel()
    await redis_manager.close()


from apps.api.app.security.auth import verify_api_key
from apps.api.app.security.middleware import SecurityHeadersMiddleware

app = FastAPI(
    title="HELM API",
    description="Autonomous Software Engineering Platform Control Plane",
    version="0.1.0",
    lifespan=lifespan,
)

# Configure Security Headers & Rate Limiting Middleware
app.add_middleware(SecurityHeadersMiddleware)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Structured Error Handlers
@app.exception_handler(HELMError)
async def helm_error_handler(request: Request, exc: HELMError):
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": str(exc),
                "details": {},
            }
        },
    )


# Health Check
@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "service": "helm-api",
        "env": settings.APP_ENV,
        "version": "0.1.0",
    }


# Mount Routers (Protected with API Key if configured)
from fastapi import Depends
auth_dep = [Depends(verify_api_key)]

app.include_router(projects.router, dependencies=auth_dep)
app.include_router(tasks.router, dependencies=auth_dep)
app.include_router(events.router, dependencies=auth_dep)
app.include_router(approvals.router, dependencies=auth_dep)
app.include_router(changes.router, dependencies=auth_dep)
app.include_router(context.router, dependencies=auth_dep)
app.include_router(system.router, dependencies=auth_dep)
