# agent/main.py
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from agent.config import settings
from agent.api.v1 import approvals
from agent.api.v1 import audit
from agent.api.v1 import alerts
from agent.api.v1 import executions
from agent.api.v1 import incidents
from agent.api.v1 import reports
from agent.db.crud import ensure_phase2_schema, ensure_phase3_schema, ensure_phase4_schema, migrate_retry_data
from agent.middleware.auth import rbac_middleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ops-agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Ops Agent 启动中 {settings.agent_host}:{settings.agent_port}")
    await ensure_phase2_schema()
    await ensure_phase3_schema()
    await ensure_phase4_schema()
    await migrate_retry_data()
    yield
    logger.info("Ops Agent 正在关闭")


app = FastAPI(
    title="Ops AI Agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(alerts.router)
app.include_router(audit.router)
app.include_router(approvals.router)
app.include_router(incidents.router)
app.include_router(executions.router)
app.include_router(reports.router)
app.middleware("http")(rbac_middleware)


@app.get("/health")
async def health():
    return {"status": "ok"}


WEB_DIR = Path(__file__).resolve().parents[1] / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
