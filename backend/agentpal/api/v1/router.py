from fastapi import APIRouter

from agentpal.api.v1.endpoints import agent, channel, config, cron, dashboard, memory, notifications, providers, session, skills, sub_agents, tasks, tools, workspace

router = APIRouter()
router.include_router(agent.router, prefix="/agent", tags=["agent"])
router.include_router(session.router, prefix="/sessions", tags=["sessions"])
router.include_router(channel.router, prefix="/channels", tags=["channels"])
router.include_router(tools.router, prefix="/tools", tags=["tools"])
router.include_router(skills.router, prefix="/skills", tags=["skills"])
router.include_router(workspace.router, prefix="/workspace", tags=["workspace"])
router.include_router(config.router, prefix="/config", tags=["config"])
router.include_router(providers.router, prefix="/providers", tags=["providers"])
router.include_router(sub_agents.router, prefix="/sub-agents", tags=["sub-agents"])
router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
router.include_router(cron.router, prefix="/cron", tags=["cron"])
router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
router.include_router(memory.router, prefix="/memory", tags=["memory"])
router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
