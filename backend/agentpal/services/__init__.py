# Lazy imports — avoid circular dependency when config.py loads config_file.py
# during database.py module initialization.
#
# The eager imports previously here caused:
#   database.py → config.py → services.config_file → services/__init__.py
#   → cron_scheduler → models.cron → database.Base (still initializing) → crash
#
# Consumers should import directly from the submodule, e.g.:
#   from agentpal.services.config_file import ConfigFileManager

__all__ = [
    "ConfigFileManager",
    "cron_scheduler",
    "notification_bus",
    "session_event_bus",
    "skill_event_bus",
    "task_event_bus",
]


def __getattr__(name: str):
    if name == "ConfigFileManager":
        from agentpal.services.config_file import ConfigFileManager
        return ConfigFileManager
    if name == "cron_scheduler":
        from agentpal.services.cron_scheduler import cron_scheduler
        return cron_scheduler
    if name == "notification_bus":
        from agentpal.services.notification_bus import notification_bus
        return notification_bus
    if name == "session_event_bus":
        from agentpal.services.session_event_bus import session_event_bus
        return session_event_bus
    if name == "skill_event_bus":
        from agentpal.services.skill_event_bus import skill_event_bus
        return skill_event_bus
    if name == "task_event_bus":
        from agentpal.services.task_event_bus import task_event_bus
        return task_event_bus
    raise AttributeError(f"module 'agentpal.services' has no attribute {name!r}")
