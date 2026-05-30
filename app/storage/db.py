"""
PostgreSQL 数据库管理器。

提供异步 SQLAlchemy 引擎和 session 工厂，负责:
    - 连接池生命周期管理 (connect / disconnect)
    - Session 生成 (供 FastAPI Depends 或 agent node 手动使用)
    - 建表操作 (create_all)
    - 健康检查 (health_check)

设计决策:
    - 用 NullPool 在开发环境: 避免连接池在频繁重启时泄漏
    - 用 QueuePool 在生产环境: 复用连接，减少 PostgreSQL 压力
    - expire_on_commit=False: 提交后不使对象过期，避免延迟加载问题
    - autoflush=False: 手动控制 flush 时机，避免隐式数据库往返
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any, AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, QueuePool

from app.config import get_settings
from app.storage.models import Base
from loguru import logger

from sqlalchemy import Connection, event
from sqlalchemy.exc import ProgrammingError


def _create_all_safe(connection: Connection) -> None:
    """
    调用 Base.metadata.create_all，同时忽略重复索引/列错误。

    当数据库中已有同名索引或约束时（某次初始化中断），
    标准 create_all 会因 DuplicateTableError 直接崩溃。
    使用此函数包装后可安全幂等执行。
    """
    try:
        Base.metadata.create_all(bind=connection, checkfirst=True)
    except ProgrammingError as exc:
        # 如果因为索引/约束已存在而失败，仅记录警告并继续
        error_msg = str(exc)
        if "already exists" in error_msg or "Duplicate" in error_msg:
            logger.warning(f"Ignored existing index/constraint during create_all")
            # 不带 checkfirst 重试——跳过已知存在的索引
            _create_all_ignore_duplicates(connection)
        else:
            raise


def _create_all_ignore_duplicates(connection: Connection) -> None:
    """
    当 create_all 因 DuplicateTableError 中断时，逐个创建表并忽略已存在的。
    这是第二层兜底策略。
    """
    for table in Base.metadata.sorted_tables:
        try:
            if not connection.dialect.has_table(connection, table.name):
                table.create(bind=connection, checkfirst=True)
            else:
                # 表已存在，尝试创建缺失的列和索引
                _ensure_indexes(connection, table)
        except ProgrammingError as exc:
            if "already exists" in str(exc) or "Duplicate" in str(exc):
                logger.debug(f"Index already exists for table '{table.name}', skipped")
            else:
                raise


def _ensure_indexes(connection: Connection, table) -> None:
    """为已存在的表创建缺失的索引（已存在的忽略）。"""
    from sqlalchemy import inspect

    inspector = inspect(connection)
    existing_idxs = {
        idx["name"] for idx in inspector.get_indexes(table.name)
    }
    for idx in table.indexes:
        if idx.name not in existing_idxs:
            try:
                idx.create(bind=connection)
            except ProgrammingError as exc:
                if "already exists" in str(exc) or "Duplicate" in str(exc):
                    logger.debug(f"Index '{idx.name}' already exists, skipped")
                else:
                    raise


class DatabaseManager:
    """
    异步数据库管理器 — 管理 engine 和 session 的完整生命周期。

    标准用法（agent node / 手动脚本）:
        db = DatabaseManager()
        await db.connect()
        async with db.session_context() as session:
            result = await session.execute(...)
        await db.disconnect()

    FastAPI 依赖注入用法:
        db = DatabaseManager()
        await db.connect()

        async def endpoint(session: Annotated[AsyncSession, Depends(db.get_session)]):
            ...
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    # ------------------------------------------------------------------
    # 连接生命周期
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """
        创建异步 engine 和 session 工厂。重复调用安全。

        开发环境用 NullPool（每次请求新连接，避免连接泄漏），
        生产环境用 QueuePool（复用连接，减少 PostgreSQL 压力）。
        """
        if self._engine is not None:
            return

        is_dev = self._settings.environment == "development"
        pool_class = NullPool if is_dev else QueuePool

        # NullPool 不接受 pool_size / max_overflow / pool_timeout，只在 QueuePool 时传入
        engine_kwargs: dict[str, Any] = dict(
            echo=self._settings.db_echo,
            poolclass=pool_class,
            pool_pre_ping=True,
        )
        if not is_dev:
            engine_kwargs.update(
                pool_size=self._settings.db_pool_size,
                max_overflow=self._settings.db_max_overflow,
                pool_timeout=self._settings.db_pool_timeout,
            )

        self._engine = create_async_engine(
            self._settings.postgres_url,
            **engine_kwargs,
        )

        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,  # commit 后对象仍可用，避免延迟加载
            autoflush=False,         # 手动控制 flush，避免隐式 SQL
        )

        # 验证连接可用
        async with self._engine.begin() as conn:
            await conn.execute(text("SELECT 1"))

        logger.info(
            f"PostgreSQL connected: {self._settings.postgres_host}:{self._settings.postgres_port}"
        )

    async def disconnect(self) -> None:
        """释放连接池，关闭所有连接。"""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("PostgreSQL disconnected")

    async def health_check(self) -> bool:
        """数据库连接健康检查。"""
        if self._engine is None:
            return False
        try:
            async with self._engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 建表
    # ------------------------------------------------------------------

    async def create_all(self) -> None:
        """
        根据 ORM 模型自动创建所有表。

        仅用于开发和首次部署。生产环境应使用 Alembic 迁移。
        幂等操作：表已存在则跳过，索引冲突自动忽略。
        """
        if self._engine is None:
            raise RuntimeError("数据库未连接，请先调用 connect()")

        async with self._engine.begin() as conn:
            await conn.run_sync(_create_all_safe)

        table_names = ", ".join(Base.metadata.tables.keys())
        logger.info(f"Tables ensured: {table_names}")

    # ------------------------------------------------------------------
    # Session 获取
    # ------------------------------------------------------------------

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        获取一个 AsyncSession，作为 FastAPI 依赖项使用。

        用法:
            async def endpoint(
                session: Annotated[AsyncSession, Depends(db.get_session)],
            ) -> dict:
                repo = ChatHistoryRepository(session)
                return await repo.get_by_session(...)

        session 结束时自动 commit；异常时自动 rollback。
        """
        if self._session_factory is None:
            raise RuntimeError("数据库未连接，请先调用 connect()")

        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @asynccontextmanager
    async def session_context(self) -> AsyncGenerator[AsyncSession, None]:
        """
        作为 async context manager 获取 session（适合 agent node 内手动使用）。

        用法:
            async with db.session_context() as session:
                repo = ChatHistoryRepository(session)
                await repo.create(...)
        """
        if self._session_factory is None:
            raise RuntimeError("数据库未连接，请先调用 connect()")

        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def engine(self) -> AsyncEngine:
        """底层 AsyncEngine。未连接抛出 RuntimeError。"""
        if self._engine is None:
            raise RuntimeError("数据库未连接，请先调用 connect()")
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Session 工厂。未连接抛出 RuntimeError。"""
        if self._session_factory is None:
            raise RuntimeError("数据库未连接，请先调用 connect()")
        return self._session_factory


# ============================================================================
# 单例访问器
# ============================================================================

@lru_cache(maxsize=1)
def get_db_manager() -> DatabaseManager:
    """
    获取 DatabaseManager 单例。

    @lru_cache(maxsize=1) 在无参函数上 = 模块级单例。
    连接仍需在应用 lifespan 中显式调用 await db.connect()。
    """
    return DatabaseManager()
