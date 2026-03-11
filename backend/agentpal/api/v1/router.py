from fastapi import APIRouter

from agentpal.api.v1.endpoints import agent, channel, session, tools

router = APIRouter()
router.include_router(agent.router, prefix="/agent", tags=["agent"])
router.include_router(session.router, prefix="/sessions", tags=["sessions"])
router.include_router(channel.router, prefix="/channels", tags=["channels"])
router.include_router(tools.router, prefix="/tools", tags=["tools"])
