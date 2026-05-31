# agent/main.py
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent.config import settings
from agent.api.v1 import alerts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ops-agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Ops Agent starting on {settings.agent_host}:{settings.agent_port}")
    yield
    logger.info("Ops Agent shutting down")


app = FastAPI(
    title="Ops AI Agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(alerts.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
