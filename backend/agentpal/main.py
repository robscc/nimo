"""FastAPI 应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from agentpal.api.v1.router import router as v1_router
from agentpal.config import get_settings
from agentpal.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期钩子。"""
    settings = get_settings()
    logger.info(f"AgentPal 启动中 (env={settings.app_env})")
    await init_db()
    logger.info("数据库初始化完成 ✅")
    yield
    logger.info("AgentPal 已关闭")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="AgentPal API",
        description="个人 AI 助手平台 API",
        version="0.1.0",
        docs_url="/docs" if settings.is_dev else None,
        redoc_url="/redoc" if settings.is_dev else None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── 路由 ──────────────────────────────────────────────
    app.include_router(v1_router, prefix="/api/v1")

    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "agentpal.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.is_dev,
    )
