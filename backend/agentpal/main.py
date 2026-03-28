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


def _setup_llm_debug_logging() -> None:
    """开发模式下启用 httpx/openai 请求级日志，桥接到 loguru。"""
    import logging

    class _LoguruHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno
            logger.opt(depth=6, exception=record.exc_info).log(
                level, record.getMessage(),
            )

    # httpx 请求/响应日志
    for name in ("httpx", "openai", "agentpal.providers"):
        log = logging.getLogger(name)
        log.handlers = [_LoguruHandler()]
        log.setLevel(logging.DEBUG)
        log.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期钩子。"""
    settings = get_settings()
    logger.info(f"AgentPal 启动中 (env={settings.app_env})")

    if settings.is_dev:
        _setup_llm_debug_logging()
        logger.info("已启用 LLM 请求调试日志 (httpx/openai/providers)")
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

    # Cron 调度器现在由 Scheduler 进程管理（CronDaemon），不再在主进程启动
    logger.info("Cron 调度由 Scheduler 进程管理 ✅")

    # 启动 ZMQ AgentDaemonManager → 替换为 SchedulerClient（独立进程模式）
    from agentpal.scheduler.client import SchedulerClient
    from agentpal.scheduler.config import SchedulerConfig

    sched_config = SchedulerConfig(
        router_addr=settings.scheduler_router_addr,
        events_addr=settings.scheduler_events_addr,
        pa_idle_timeout=settings.scheduler_pa_idle_timeout,
        sub_idle_timeout=settings.scheduler_sub_idle_timeout,
        health_check_interval=settings.scheduler_health_check_interval,
        process_start_timeout=settings.scheduler_process_start_timeout,
    )
    scheduler = SchedulerClient(sched_config)
    await scheduler.start()
    app.state.zmq_manager = scheduler   # 兼容旧引用
    app.state.scheduler = scheduler
    logger.info("SchedulerClient 已启动（Scheduler / PA / Cron 独立进程）✅")

    # 启动 DingTalk Stream 客户端（dingtalk_enabled=True 时才真正启动）
    from agentpal.channels.dingtalk_stream_worker import dingtalk_stream_worker

    await dingtalk_stream_worker.start()

    yield

    # 停止 DingTalk Stream 客户端
    await dingtalk_stream_worker.stop()

    # 停止 SchedulerClient（级联关闭 Scheduler / PA / Cron / SubAgent 子进程）
    if hasattr(app, "state") and hasattr(app.state, "scheduler"):
        await app.state.scheduler.stop()
        logger.info("SchedulerClient 已停止（所有子进程已关闭）")

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
