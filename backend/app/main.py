import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import init_db, close_db

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("NigeriaVoteWatch starting", env=settings.app_env)
    await init_db()

    from app.tasks.scheduler import setup_scheduler
    setup_scheduler()

    yield

    from app.tasks.scheduler import teardown_scheduler
    teardown_scheduler()

    await close_db()
    logger.info("NigeriaVoteWatch stopped")


app = FastAPI(
    title="NigeriaVoteWatch API",
    description="Election integrity monitoring platform for Nigeria",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception", path=request.url.path, error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/api/health")
async def health():
    return {"status": "ok", "env": settings.app_env}


# ── Routers ───────────────────────────────────────────────────────────────────

from app.routers.auth import router as auth_router
from app.routers.elections import router as elections_router
from app.routers.results import router as results_router
from app.routers.observers import router as observers_router
from app.routers.anomalies import router as anomalies_router
from app.routers.stats import router as stats_router
from app.ws.router import router as ws_router

app.include_router(auth_router,      prefix="/api/auth",      tags=["auth"])
app.include_router(elections_router, prefix="/api/elections", tags=["elections"])
app.include_router(results_router,   prefix="/api/results",   tags=["results"])
app.include_router(observers_router, prefix="/api/observers", tags=["observers"])
app.include_router(anomalies_router, prefix="/api/anomalies", tags=["anomalies"])
app.include_router(stats_router,     prefix="/api/stats",     tags=["stats"])
app.include_router(ws_router,        prefix="/ws")
