"""Skill 数据模型 — 已安装技能的持久化记录。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, String

from agentpal.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SkillRecord(Base):
    """已安装的 Skill 记录。

    Attributes:
        name:         唯一名称（即 skill.json 中的 name）
        version:      语义化版本号
        description:  一句话描述
        author:       作者
        source:       来源类型 — local / url / clawhub / skills.sh
        source_url:   安装来源 URL（可为空）
        enabled:      是否启用
        install_path: 解压后的绝对路径
        meta:         完整 skill.json 内容（JSON）
        created_at:   安装时间
        updated_at:   最后更新时间
    """

    __tablename__ = "skills"

    name = Column(String, primary_key=True)
    version = Column(String, nullable=False, default="0.0.0")
    description = Column(String, default="")
    author = Column(String, default="")
    source = Column(String, default="local")
    source_url = Column(String, nullable=True)
    enabled = Column(Boolean, default=True, nullable=False)
    install_path = Column(String, nullable=False)
    meta = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
