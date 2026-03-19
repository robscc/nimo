"""ZMQ 消息总线 — Agent 间实时通信基础设施。

核心组件：
- Envelope / MessageType: 统一消息协议（msgpack 序列化）
- AgentDaemon: Agent 守护进程基类（DEALER + PUB + FIFO Queue）
- AgentDaemonManager: 中心路由 + daemon 生命周期管理
- EventSubscriber: ZMQ SUB → async iterator（供 SSE 使用）
- PersonalAssistantDaemon / SubAgentDaemon / CronDaemon: 具体 daemon 实现
"""

from agentpal.zmq_bus.protocol import Envelope, MessageType

# 延迟导入避免循环依赖（需要时通过 from agentpal.zmq_bus.xxx import Xxx）
__all__ = [
    "Envelope",
    "MessageType",
    # 以下组件通过各自模块导入：
    # from agentpal.zmq_bus.daemon import AgentDaemon
    # from agentpal.zmq_bus.manager import AgentDaemonManager
    # from agentpal.zmq_bus.event_subscriber import EventSubscriber
    # from agentpal.zmq_bus.pa_daemon import PersonalAssistantDaemon
    # from agentpal.zmq_bus.sub_daemon import SubAgentDaemon
    # from agentpal.zmq_bus.cron_daemon import CronDaemon
]
