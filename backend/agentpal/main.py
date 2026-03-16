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
from agentpal.database import init_db, run_migrations


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期钩子。"""
    settings = get_settings()
    logger.info(f"AgentPal 启动中 (env={settings.app_env})")
    await init_db()
    await run_migrations()
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

    # 清理上次意外中断的 running 任务（服务重启后不可能自动完成）
    from sqlalchemy import update

    from agentpal.models.session import SubAgentTask, TaskStatus

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(SubAgentTask)
            .where(SubAgentTask.status.in_([TaskStatus.RUNNING, TaskStatus.PENDING]))
            .values(status=TaskStatus.FAILED, error="服务重启，任务中断")
        )
        if result.rowcount:
            logger.warning(f"清理了 {result.rowcount} 个中断任务（running/pending → failed）")
        await db.commit()

    # 启动 Cron 调度器
    from agentpal.services.cron_scheduler import cron_scheduler

    await cron_scheduler.start()
    logger.info("Cron 调度器已启动 ✅")

    # 启动 DingTalk Stream 客户端（dingtalk_enabled=True 时才真正启动）
    from agentpal.channels.dingtalk_stream_worker import dingtalk_stream_worker

    await dingtalk_stream_worker.start()

    yield

    # 停止 DingTalk Stream 客户端
    await dingtalk_stream_worker.stop()

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

    # ── 生产环境：前端 SPA 静态文件（必须放在所有路由之后）──
    _static_dir = Path(__file__).resolve().parent.parent / "static"
    if not settings.is_dev and _static_dir.is_dir():
        # 静态资源（JS/CSS/图片等）
        _assets_dir = _static_dir / "assets"
        if _assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

        # SPA fallback：非 API/非静态文件的路径一律返回 index.html
        _index_html = (_static_dir / "index.html").read_text()

        from fastapi.responses import HTMLResponse

        @app.get("/{path:path}", response_class=HTMLResponse, include_in_schema=False)
        async def spa_fallback(path: str):
            # 已有的静态文件直接返回
            file_path = _static_dir / path
            if file_path.is_file():
                from starlette.responses import FileResponse

                return FileResponse(str(file_path))
            return HTMLResponse(_index_html)

        logger.info(f"已挂载前端静态文件: {_static_dir}")

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
