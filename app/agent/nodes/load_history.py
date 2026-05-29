"""
Node: load_history

从 Redis 加载当前会话的近期聊天记录，预置到 state["messages"] 前面。
这样 LLM 在后续节点中能感知对话上下文，实现多轮对话能力。

消息格式: Redis 中存储的是 JSON 字符串 {"role": "user/assistant/system/tool", "content": "..."}
         需要反序列化为 LangChain 的 HumanMessage / AIMessage / ToolMessage / SystemMessage。
"""

from __future__ import annotations

import json

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.agent.schema import AgentState
from app.agent.events import emit, StreamEvent
from app.storage import get_redis_client
from loguru import logger


# 历史消息上限：避免上下文窗口爆炸
MAX_HISTORY_MESSAGES = 20

# Role → Message 类映射
_ROLE_MAP = {
    "user": HumanMessage,
    "assistant": AIMessage,
    "system": SystemMessage,
    "tool": ToolMessage,
}


def _deserialize(msg_dict: dict) -> BaseMessage:
    """将 Redis 中存储的 dict 反序列化为 LangChain 消息对象。"""
    role = msg_dict.get("role", "user")
    content = msg_dict.get("content", "")

    msg_cls = _ROLE_MAP.get(role, HumanMessage)

    if role == "tool":
        # ToolMessage 需要 tool_call_id
        return ToolMessage(
            content=content,
            tool_call_id=msg_dict.get("tool_call_id", "unknown"),
        )
    return msg_cls(content=content)


async def load_history(state: AgentState) -> AgentState:
    """从 Redis 加载会话历史，预置到 state["messages"] 前面。

    注意:
        - state["messages"] 可能已包含当前轮的用户消息（由 ainvoke 传入）。
        - 加载的历史消息放在当前消息之前，形成完整上下文。
        - 未连接 Redis 或会话无历史时，原样返回 state（不报错）。
    """
    await emit(StreamEvent(
        type="node_start", node="load_history",
        message="正在加载会话历史...",
    ))

    session_id = state.get("session_id", "")
    if not session_id:
        logger.warning("load_history: no session_id in state, skipping")
        await emit(StreamEvent(type="node_complete", node="load_history"))
        return state

    try:
        redis = get_redis_client()
        key = f"chat_history:{session_id}"
        raw_list = await redis.client.lrange(key, -MAX_HISTORY_MESSAGES, -1)

        if not raw_list:
            logger.debug(f"load_history: no history found for session={session_id}")
            return state

        history_messages: list[BaseMessage] = []
        for raw in raw_list:
            try:
                msg_dict = json.loads(raw)
                msg = _deserialize(msg_dict)
                history_messages.append(msg)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"load_history: skipping corrupt message: {e}")

        if history_messages:
            # 历史消息放到现有消息之前（LangGraph 的 add_messages reducer
            # 会按顺序 append，所以这里直接替换为 历史 + 当前）
            current = state.get("messages", [])
            state["messages"] = history_messages + current  # type: ignore[assignment]
            logger.info(
                f"load_history: loaded {len(history_messages)} messages "
                f"for session={session_id}"
            )

    except RuntimeError:
        # Redis 未连接（开发环境常见），降级跳过
        logger.debug("load_history: Redis not connected, skipping history load")
    except Exception:
        logger.exception("load_history: unexpected error loading history")

    await emit(StreamEvent(type="node_complete", node="load_history"))
    return state
