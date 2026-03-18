"""记忆存储迁移脚本。

将已有的 session 数据迁移到新的记忆存储结构：
1. 为 memory_records 表中现有记录回填 user_id 和 channel
   （从关联的 SessionRecord 或 session_id 格式推导）
2. 创建新的索引以支持跨 session 查询

使用方式：
    cd backend
    python -m agentpal.migrations.migrate_memory

或在代码中调用：
    from agentpal.migrations.migrate_memory import run_migration
    await run_migration()
"""

from __future__ import annotations

import asyncio
import sys

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def run_migration(db: AsyncSession | None = None) -> dict[str, int]:
    """执行记忆存储迁移。

    步骤：
    1. 确保新列已存在（依赖 database.run_migrations）
    2. 从 session_id 格式推导 channel（如 "web:uuid" → "web"）
    3. 从 sessions 表关联回填 user_id
    4. 创建索引（如果不存在）

    Args:
        db: 可选的 AsyncSession。如果不传，会自己创建连接。

    Returns:
        迁移统计：{"updated_channel": N, "updated_user_id": N}
    """
    stats = {"updated_channel": 0, "updated_user_id": 0, "total_records": 0}

    should_close = False
    if db is None:
        from agentpal.database import AsyncSessionLocal
        db = AsyncSessionLocal()
        should_close = True

    try:
        # 统计总记录数
        result = await db.execute(text("SELECT COUNT(*) FROM memory_records"))
        stats["total_records"] = result.scalar_one()
        logger.info(f"记忆记录总数: {stats['total_records']}")

        if stats["total_records"] == 0:
            logger.info("无需迁移：memory_records 表为空")
            return stats

        # Step 1: 从 session_id 格式推导 channel
        # session_id 格式通常是 "channel:uuid"，如 "web:abc-123"
        result = await db.execute(
            text(
                """
                UPDATE memory_records
                SET channel = SUBSTR(session_id, 1, INSTR(session_id, ':') - 1)
                WHERE channel IS NULL
                  AND INSTR(session_id, ':') > 0
                """
            )
        )
        stats["updated_channel"] = result.rowcount
        logger.info(f"从 session_id 推导 channel: 更新 {result.rowcount} 条记录")

        # Step 2: 从 sessions 表关联回填 user_id
        result = await db.execute(
            text(
                """
                UPDATE memory_records
                SET user_id = (
                    SELECT s.user_id
                    FROM sessions s
                    WHERE s.id = memory_records.session_id
                      AND s.user_id IS NOT NULL
                )
                WHERE memory_records.user_id IS NULL
                  AND EXISTS (
                    SELECT 1 FROM sessions s
                    WHERE s.id = memory_records.session_id
                      AND s.user_id IS NOT NULL
                  )
                """
            )
        )
        stats["updated_user_id"] = result.rowcount
        logger.info(f"从 sessions 表回填 user_id: 更新 {result.rowcount} 条记录")

        # Step 3: 尝试创建新索引（如果不存在）
        for idx_name, idx_sql in [
            (
                "ix_memory_user_time",
                "CREATE INDEX IF NOT EXISTS ix_memory_user_time ON memory_records(user_id, created_at)",
            ),
            (
                "ix_memory_channel_time",
                "CREATE INDEX IF NOT EXISTS ix_memory_channel_time ON memory_records(channel, created_at)",
            ),
        ]:
            try:
                await db.execute(text(idx_sql))
                logger.info(f"索引 {idx_name} 已创建/确认")
            except Exception as exc:
                logger.warning(f"创建索引 {idx_name} 失败（可能已存在）: {exc}")

        await db.commit()
        logger.info(f"迁移完成! 统计: {stats}")

    except Exception as exc:
        logger.error(f"迁移失败: {exc}")
        await db.rollback()
        raise
    finally:
        if should_close:
            await db.close()

    return stats


async def _main() -> None:
    """CLI 入口。"""
    from agentpal.database import init_db, run_migrations

    logger.info("开始记忆存储迁移...")

    # 确保表结构是最新的
    await init_db()
    await run_migrations()

    # 执行数据迁移
    stats = await run_migration()

    logger.info(f"迁移完成: {stats}")


if __name__ == "__main__":
    asyncio.run(_main())
