"""
FastAPI main application entry
Integrates all routes, middleware and static file serving
"""
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

# Ensure project root is in Python path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware
import json

Response.media_type = "application/json; charset=utf-8"
JSONResponse.media_type = "application/json; charset=utf-8"

from config import config
from service.http_encoding import with_utf8_charset
from service.logger import get_logger
from service.i18n import _, get_lang_from_request
from service.monitor import monitor
from service.scheduler import init_scheduled_tasks, stop_scheduler
from service.rate_limiter import limiter, rate_limit_exceeded_handler, is_exempt

logger = get_logger('api.api')


class UTF8Middleware(BaseHTTPMiddleware):
    """Force UTF-8 charset on JSON/text responses that would otherwise omit it.

    Uses the public ``response.headers`` mutable-mapping API (instead of reaching
    into the private ``response._headers`` tuple list), which is stable across
    Starlette versions. The actual decision logic lives in
    ``service.http_encoding.with_utf8_charset`` so it is unit-testable and shared.
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        original = response.headers.get('content-type', '')
        amended = with_utf8_charset(original)
        if amended != original:
            response.headers['content-type'] = amended
        return response


class UTF8JSONResponse(JSONResponse):
    """Custom JSON response class, forces UTF-8 encoding"""
    
    def render(self, content: dict) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")


def create_app() -> FastAPI:
    """Create FastAPI application"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifecycle management"""
        # Initialize on startup
        logger.info("=" * 60)
        logger.info(_('api.app_starting', None, config.get('system.app_name'), config.get('system.version')))
        env = 'DEBUG' if config.get('system.debug') else 'PRODUCTION'
        logger.info(_('api.app_env', None, env))
        logger.info("=" * 60)

        # Ensure storage directories exist
        storage_dirs = [
            'storage/vectordb',
            'storage/documents',
            'storage/logs',
            'storage/tasks',
            'storage/monitor',
            'storage/traces'
        ]
        for d in storage_dirs:
            Path(d).mkdir(parents=True, exist_ok=True)

        # Initialize LLM pipeline
        try:
            from core.llm_pipeline import LLMPipeline
            from api.routes.knowledge import get_retriever
            from api.routes.chat import set_llm_pipeline

            retriever = get_retriever()
            llm_pipeline = LLMPipeline(retriever)
            set_llm_pipeline(llm_pipeline)

            # Check LLM health status
            health = llm_pipeline.check_health()
            if health.get('healthy'):
                logger.info(_('api.llm_ready', None, health.get('llm_model')))
            else:
                logger.warning(_('api.llm_unavailable_detail', None, health.get('error', 'Unknown error')))

        except Exception as e:
            logger.error(_('api.pipeline_init_failed', None, str(e)), exc_info=True)

        # Initialize scheduled tasks
        try:
            init_scheduled_tasks()
        except Exception as e:
            logger.error(_('api.scheduler_init_failed', None, str(e)), exc_info=True)

        logger.info(_('api.app_started'))

        yield

        # Cleanup on shutdown
        logger.info(_('api.app_shutdown'))
        monitor.save_snapshot()
        stop_scheduler()
        # Rule engine: stop background flush thread and perform final flush to avoid buffered data loss
        try:
            from core.rule_engine import rule_engine
            rule_engine.stop()
        except Exception as e:
            logger.error(f" Rule engine shutdown failed: {e}")  
        logger.info(_('api.app_shutdown_done'))

    app = FastAPI(
        title=config.get('system.app_name', 'whiteBoxRAG'),
        version=config.get('system.version', '1.0.0'),
        description=_('api.app_description'),
        lifespan=lifespan,
        default_response_class=UTF8JSONResponse
    )

    # UTF-8 encoding middleware (added first)
    app.add_middleware(UTF8Middleware)

    # CORS middleware
    if config.get('api.cors_enabled', True):
        origins = config.get('api.cors_origins', ['*'])
        # A wildcard origin must never be combined with credentials: browsers reject that
        # combination, and "Access-Control-Allow-Origin: *" lets any website talk to this API.
        # Credentials are therefore only enabled when an explicit origin list is configured.
        allow_credentials = '*' not in origins
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=allow_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # ---- API rate limiting ----
    # Register global rate limiter and 429 exception handler to prevent malicious API flooding.
    # Default limit is api.rate_limit/minute (per client IP); static files and health checks are exempt.
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    class ExemptableSlowAPIMiddleware(SlowAPIMiddleware):
        """Skip rate limiting for static files, health checks and other paths"""
        async def dispatch(self, request, call_next):
            if is_exempt(request.url.path):
                return await call_next(request)
            return await super().dispatch(request, call_next)

    app.add_middleware(ExemptableSlowAPIMiddleware)
    logger.info(f" API rate limiting enabled: {config.get('api.rate_limit', 60)} requests/minute (per IP)")

    # Request logging middleware
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        import time
        start_time = time.time()

        # Log request
        logger.info(_('api.request_log', None, request.method, request.url.path))

        try:
            response = await call_next(request)
            duration = time.time() - start_time

            # Record performance (exclude static files)
            if not request.url.path.startswith('/static'):
                monitor.record_request(
                    endpoint=request.url.path,
                    duration=duration,
                    error=response.status_code >= 500
                )

            response.headers["X-Response-Time"] = str(duration)
            return response

        except Exception as e:
            duration = time.time() - start_time
            monitor.record_request(request.url.path, duration, error=True)
            logger.error(_('api.request_error', None, request.method, request.url.path, str(e)), exc_info=True)
            raise

    # Global exception handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(_('api.uncaught_exception', None, str(exc)), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": _('api.server_error', get_lang_from_request(request)),
                "detail": str(exc) if config.get('system.debug') else None
            }
        )

    # Register routes
    from api.routes.knowledge import router as knowledge_router
    from api.routes.chat import router as chat_router
    from api.routes.monitor import router as monitor_router
    from api.routes.scenario import router as scenario_router
    from api.routes.evaluation import router as evaluation_router
    from api.routes.document_optimizer import router as document_optimizer_router

    app.include_router(knowledge_router)
    app.include_router(chat_router)
    app.include_router(monitor_router)
    app.include_router(scenario_router)
    app.include_router(evaluation_router)
    app.include_router(document_optimizer_router)

    # Static file serving
    static_dir = project_root / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Redirect root path to frontend
    @app.get("/", include_in_schema=False)
    async def root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/static/index.html")

    # Health check endpoint
    @app.get("/api/health", tags=[_('api.health_tag')])
    async def health_check():
        """System health check"""
        return {
            "status": "ok",
            "app_name": config.get('system.app_name'),
            "version": config.get('system.version')
        }

    return app


# Create application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.api:app",
        host=config.get('system.host', '0.0.0.0'),
        port=config.get('system.port', 8080),
        reload=config.get('system.debug', False),
        workers=1  # Single worker to avoid multi-process state inconsistency
    )
