import asyncio
import logging
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

import anyio
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException

from agenticiot import __version__
from agenticiot.access.api import router as access_router
from agenticiot.config import Settings
from agenticiot.database import build_engine, probe_database
from agenticiot.events import router as events_router
from agenticiot.limits import RequestLimits
from agenticiot.nodes.channel import Hub
from agenticiot.nodes.channel import router as node_router
from agenticiot.registry.api import router as registry_router
from agenticiot.runtime.api import router as runtime_router
from agenticiot.services.api import router as services_router
from agenticiot.tools import router as tools_router

logger = logging.getLogger(__name__)
TRACEPARENT = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-(00|01)")


class Health(BaseModel):
    status: Literal["ok", "ready"]
    version: str


class Problem(BaseModel):
    type: str
    title: str
    status: int
    code: str
    trace_id: str


def problem(request: Request, status: int, title: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content=Problem(
            type=f"urn:agenticiot:problem:{code}",
            title=title,
            status=status,
            code=code,
            trace_id=getattr(request.state, "trace_id", uuid4().hex),
        ).model_dump(),
    )


def create_app(
    settings: Settings | None = None,
    *,
    readiness_probe: Callable[[], bool] | None = None,
    legacy_test_channel: bool = False,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = None
        app.state.settings = settings or Settings()
        if readiness_probe is None:
            engine = build_engine(app.state.settings)
            app.state.readiness_probe = lambda: probe_database(engine)
        else:
            app.state.readiness_probe = readiness_probe
        app.state.engine = engine
        app.state.node_hub = Hub()

        async def sweep():
            from agenticiot.services.api import expire_metadata

            while True:
                if engine is not None:
                    try:
                        await anyio.to_thread.run_sync(expire_metadata, app)
                    except SQLAlchemyError:
                        logger.warning("Metadata maintenance deferred: database not ready")
                await asyncio.sleep(60)

        maintenance = asyncio.create_task(sweep())
        try:
            yield
        finally:
            await app.state.node_hub.close()
            maintenance.cancel()
            await asyncio.gather(maintenance, return_exceptions=True)
            if engine is not None:
                engine.dispose()

    app = FastAPI(
        redirect_slashes=False,
        title="AgenticIoT Platform — implemented endpoints",
        version=__version__,
        description=(
            "Device registry: immutable model versions, validated capabilities, "
            "domain-scoped discovery and durable virtual/MQTT-demo light execution. "
            "All execution in this slice is simulated."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(RequestLimits)

    implemented_router = APIRouter()
    implemented_router.include_router(registry_router)
    implemented_router.include_router(runtime_router)
    if legacy_test_channel:
        # Regression harness only; never mounted by the production app factory default.
        from agenticiot.runtime.api import legacy_router

        implemented_router.include_router(legacy_router)
    implemented_router.include_router(access_router)
    implemented_router.include_router(node_router)
    implemented_router.include_router(services_router)
    implemented_router.include_router(events_router)
    implemented_router.include_router(tools_router)
    app.include_router(
        implemented_router,
        responses={
            status: {
                "description": description,
                "content": {"application/problem+json": {"schema": Problem.model_json_schema()}},
            }
            for status, description in {
                400: "Invalid pagination cursor",
                401: "Missing or invalid API credential",
                403: "Operation or domain outside granted scope",
                404: "Resource not found in granted domain",
                409: "Registry uniqueness or reference conflict",
                412: "Revision changed; reload before editing",
                422: "Request validation failed",
                428: "Conditional edit requires If-Match",
                501: "Synchronous fresh reads are not supported",
                503: "Registry database unavailable or migrations missing",
            }.items()
        },
    )

    @app.middleware("http")
    async def correlate(request: Request, call_next):
        match = TRACEPARENT.fullmatch(request.headers.get("traceparent", ""))
        valid = match and int(match[1], 16) != 0 and int(match[2], 16) != 0
        request.state.trace_id = match[1] if valid else uuid4().hex
        if request.app.state.node_hub.draining and request.url.path != "/health/live":
            response = problem(request, 503, "Platform is draining", "platform_draining")
        else:
            response = await call_next(request)
        response.headers["X-Trace-ID"] = request.state.trace_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        result = problem(
            request,
            exc.status_code,
            str(exc.detail),
            getattr(exc, "code", f"http_{exc.status_code}"),
        )
        if exc.headers:
            result.headers.update(exc.headers)
        return result

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError):
        return problem(request, 422, "Request validation failed", "invalid_request")

    @app.exception_handler(IntegrityError)
    async def conflict_error(request: Request, _exc: IntegrityError):
        return problem(
            request,
            409,
            "Resource already exists or violates a registry constraint",
            "resource_conflict",
        )

    @app.exception_handler(StaleDataError)
    async def stale_error(request: Request, _exc: StaleDataError):
        return problem(request, 412, "Resource changed; reload before editing", "revision_conflict")

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, _exc: SQLAlchemyError):
        logger.warning(
            "Registry database operation failed: %s sqlstate=%s",
            type(_exc).__name__,
            getattr(getattr(_exc, "orig", None), "sqlstate", None),
        )
        return problem(request, 503, "Database is not ready", "database_not_ready")

    @app.get("/health/live", response_model=Health, tags=["Health"], operation_id="liveness")
    async def live() -> Health:
        return Health(status="ok", version=__version__)

    @app.get(
        "/health/ready",
        response_model=Health,
        tags=["Health"],
        operation_id="readiness",
        responses={
            503: {
                "description": "Database unavailable or migrations not applied",
                "content": {"application/problem+json": {"schema": Problem.model_json_schema()}},
            }
        },
    )
    def ready(request: Request) -> Health | Response:
        try:
            is_ready = request.app.state.readiness_probe()
        except Exception:
            # Driver exceptions can embed DSNs, credentials or queries. Never return/log them.
            logger.warning("Database readiness probe failed")
            is_ready = False
        if not is_ready:
            return problem(request, 503, "Database is not ready", "database_not_ready")
        return Health(status="ready", version=__version__)

    return app


app = create_app()
