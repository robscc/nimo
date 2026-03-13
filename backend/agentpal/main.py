"""FastAPI 应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
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

    # 初始化 ~/.nimo/config.yaml（幂等）
    from agentpal.services.config_file import ConfigFileManager

    cfg_mgr = ConfigFileManager(settings.workspace_dir)
    if cfg_mgr.save_defaults():
        logger.info(f"已创建默认配置文件: {cfg_mgr.config_path}")
    else:
        logger.info(f"配置文件已存在: {cfg_mgr.config_path}")

    # 初始化默认 SubAgent 定义
    from agentpal.agents.registry import SubAgentRegistry
    from agentpal.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        registry = SubAgentRegistry(db)
        await registry.ensure_defaults()
        await db.commit()
    logger.info("SubAgent 默认角色初始化完成 ✅")

    # 启动 Cron 调度器
    from agentpal.services.cron_scheduler import cron_scheduler

    await cron_scheduler.start()
    logger.info("Cron 调度器已启动 ✅")

    yield

    # 停止 Cron 调度器
    await cron_scheduler.stop()
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

    # ── 静态文件（/uploads）────────────────────────────────
    _uploads_dir = Path("uploads")
    _uploads_dir.mkdir(exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(_uploads_dir)), name="uploads")

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
