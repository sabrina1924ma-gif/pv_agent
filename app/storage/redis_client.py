"""
Redis 客户端封装。

为智能体系统提供异步 Redis 操作接口，覆盖三种缓存使用模式：

    热存储 (Hot, TTL=24h):
        - 会话聊天历史 (chat_history:{session_id}) → List
        - 已生成报告缓存 (report:{session_id}) → String

    温存储 (Warm, TTL=5min):
        - 工具调用结果缓存 (tool_cache:{session_id}:{tool_name}) → String

    瞬态存储 (Transient):
        - 流式输出的 token buffer (stream:{session_id}) → String (TTL=60s)

设计决策:
    - 用 List 存聊天历史而非 Hash: 消息有严格时序，RPUSH + LRANGE 天然保序
    - 用 Pipeline 绑定 TTL 续期: 每次追加消息时刷新过期时间，活跃会话不被清
    - 单例 + 惰性连接: 不在 import 阶段连 Redis，由 lifespan 显式 connect()
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import redis.asyncio as aioredis

from app.config import get_settings
from loguru import logger


class RedisClient:
    """
    异步 Redis 客户端，基于 aioredis 连接池。

    用法:
        client = RedisClient()
        await client.connect()
        history = await client.get_history("session-123")
        await client.disconnect()
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: aioredis.Redis | None = None

    # ------------------------------------------------------------------
    # 连接生命周期
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """建立连接池。重复调用安全（幂等）。"""
        if self._client is not None:
            return

        self._client = aioredis.Redis(
            host=self._settings.redis_host,
            port=self._settings.redis_port,
            db=self._settings.redis_db,
            password=self._settings.redis_password or None,
            decode_responses=True,
            max_connections=20,
            socket_connect_timeout=5,
            socket_keepalive=True,
        )

        await self._client.ping()
        logger.info(
            f"Redis connected: {self._settings.redis_host}:{self._settings.redis_port}"
        )

    async def disconnect(self) -> None:
        """关闭连接池，释放资源。"""
        if self._client is not None:
            await self._client.close()
            self._client = None
            logger.info("Redis disconnected")

    async def health_check(self) -> bool:
        """连接健康检查，供 /health 端点使用。"""
        try:
            if self._client is None:
                return False
            await self._client.ping()
            return True
        except Exception:
            return False

    @property
    def client(self) -> aioredis.Redis:
        """获取底层 aioredis 实例。未连接时抛出 RuntimeError。"""
        if self._client is None:
            raise RuntimeError("Redis 未连接，请先调用 await client.connect()")
        return self._client

    # ------------------------------------------------------------------
    # 会话聊天历史 (List, TTL=24h)
    # ------------------------------------------------------------------

    async def save_message(
        self, session_id: str, role: str, content: str, **extra: Any
    ) -> None:
        """
        向会话历史末尾追加一条消息，同时续期 TTL。

        Args:
            session_id: 会话 ID。
            role: 消息角色 (user / assistant / system / tool)。
            content: 消息文本内容。
            **extra: 额外字段，如 intent, station_id 等，会一并序列化。
        """
        key = f"chat_history:{session_id}"

        message = {"role": role, "content": content, **extra}
        serialized = json.dumps(message, ensure_ascii=False)

        # Pipeline: 追加 + 续期，两次命令一次往返
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.rpush(key, serialized)
            pipe.expire(key, self._settings.redis_session_ttl)
            await pipe.execute()

    async def get_history(
        self, session_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """
        获取会话最近 N 条消息，旧→新顺序。

        LRANGE 用负索引取末尾：-20 -1 表示最近 20 条。
        返回顺序保持插入顺序（最早在前，最新在后），方便 LLM 按时间线阅读。
        """
        key = f"chat_history:{session_id}"
        raw_list = await self.client.lrange(key, -limit, -1)
        return [json.loads(msg) for msg in raw_list]

    async def delete_history(self, session_id: str) -> None:
        """删除会话的全部聊天记录。"""
        await self.client.delete(f"chat_history:{session_id}")
        logger.debug(f"Deleted chat history for session {session_id}")

    # ------------------------------------------------------------------
    # 工具调用结果缓存 (String, TTL=5min)
    # ------------------------------------------------------------------

    async def cache_tool_result(
        self, session_id: str, tool_name: str, result: dict[str, Any]
    ) -> None:
        """
        缓存一次工具调用的结果。短期有效，避免同一轮内重复调用。

        Key 设计: tool_cache:{session}:{tool} — 同一会话同一工具只保留最新结果。
        """
        key = f"tool_cache:{session_id}:{tool_name}"
        await self.client.setex(
            key,
            self._settings.redis_tool_cache_ttl,
            json.dumps(result, ensure_ascii=False),
        )

    async def get_cached_tool_result(
        self, session_id: str, tool_name: str
    ) -> dict[str, Any] | None:
        """获取缓存的工具结果，不存在或已过期返回 None。"""
        key = f"tool_cache:{session_id}:{tool_name}"
        raw = await self.client.get(key)
        return json.loads(raw) if raw else None

    # ------------------------------------------------------------------
    # 报告缓存 (String, TTL=24h)
    # ------------------------------------------------------------------

    async def cache_report(self, session_id: str, report_md: str) -> None:
        """缓存已生成的 Markdown 报告。"""
        key = f"report:{session_id}"
        await self.client.setex(key, self._settings.redis_session_ttl, report_md)

    async def get_report(self, session_id: str) -> str | None:
        """获取缓存的报告，不存在返回 None。"""
        key = f"report:{session_id}"
        return await self.client.get(key)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # 流式输出 Token Buffer (String, TTL=60s)
    # ------------------------------------------------------------------

    async def append_stream_token(self, session_id: str, token: str) -> None:
        """向流式缓冲区追加一个 token。"""
        key = f"stream:{session_id}"
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.append(key, token)
            pipe.expire(key, 60)
            await pipe.execute()

    async def get_stream_content(self, session_id: str) -> str:
        """获取当前流式缓冲区的全部内容并清空。"""
        key = f"stream:{session_id}"
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.get(key)
            pipe.delete(key)
            content, _ = await pipe.execute()
        return content or ""


# ============================================================================
# 单例访问器
# ============================================================================

@lru_cache(maxsize=1)
def get_redis_client() -> RedisClient:
    """
    获取 RedisClient 单例。

    @lru_cache(maxsize=1) 在无参函数上等价于模块级单例，
    但更简洁——不需要 global 变量和手动判空。
    """
    return RedisClient()
