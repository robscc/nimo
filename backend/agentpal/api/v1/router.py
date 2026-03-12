from fastapi import APIRouter

from agentpal.api.v1.endpoints import agent, channel, session, skills, tools, workspace

router = APIRouter()
router.include_router(agent.router, prefix="/agent", tags=["agent"])
router.include_router(session.router, prefix="/sessions", tags=["sessions"])
router.include_router(channel.router, prefix="/channels", tags=["channels"])
router.include_router(tools.router, prefix="/tools", tags=["tools"])
router.include_router(skills.router, prefix="/skills", tags=["skills"])
router.include_router(workspace.router, prefix="/workspace", tags=["workspace"])
