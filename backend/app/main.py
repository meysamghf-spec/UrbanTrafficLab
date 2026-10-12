"""FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app import __version__
from app.api.router import router
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import init_db
from app.domain.redact import redact

logger = logging.getLogger("urbantrafficlab")


class BodyLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > get_settings().max_upload_bytes:
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "payload_too_large",
                        "message": "Request body exceeds the configured limit.",
                        "details": [],
                    }
                },
            )
        return await call_next(request)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    init_db()
    logger.info(
        "UrbanTrafficLab %s ready (llm_configured=%s)",
        __version__,
        settings.llm_configured,
    )
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="UrbanTrafficLab API",
        version=__version__,
        description=(
            "Versioned API for study areas, road-network scenarios, SUMO or "
            "explicitly labeled analytical estimates, comparison, and export. "
            "The service has no built-in user authentication."
        ),
        lifespan=lifespan,
    )
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["http://localhost:3000"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(BodyLimitMiddleware)
    app.include_router(router, prefix="/api/v1")

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "urbantrafficlab", "docs": "/docs", "health": "/api/v1/health"}

    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for item in exc.errors():
            details.append({"loc": [str(part) for part in item.get("loc", [])], "msg": item.get("msg", "")})
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                    "details": details,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error: %s", redact(str(exc), [settings.llm_api_key]))
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "The server could not complete the request.",
                    "details": [],
                }
            },
        )

    return app


app = create_app()
