"""对话接口：POST /api/chat（SSE 流式）、GET history。

需登录。会话内记忆：把该 (user, thread) 累计的 brief 回灌进图（支持分批补充
时间/地点/人数），本轮结束再把新 brief 写回；仅当成功生成行程时才落库 plans。
chitchat / 开放问答不入库（不写 messages）。
"""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from travel_agent.api.deps import get_current_user, get_settings
from travel_agent.api.errors import AppError, ErrorCode
from travel_agent.api.sse import sse_stream
from travel_agent.config import Settings
from travel_agent.db.models import UserRow

__all__ = ["router"]

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    thread_id: str | None = None
    message: str = Field(min_length=1, max_length=2000)
    client_ts: datetime | None = None


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: datetime | None = None


@router.post("")
async def chat(
    req: ChatRequest,
    request: Request,
    user: UserRow = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    if not settings.llm_api_key or not settings.llm_api_key.get_secret_value():
        raise AppError(
            ErrorCode.LLM_NOT_CONFIGURED,
            "未配置 LLM_API_KEY，无法发起对话",
            status_code=503,
        )
    thread_id = req.thread_id or uuid.uuid4().hex

    async def stream() -> AsyncIterator[str]:
        try:
            async for frame in _run_and_stream(request, settings, user, thread_id, req.message):
                yield frame
        except Exception as exc:
            import json

            error_data = json.dumps(
                {"code": "UPSTREAM_ERROR", "message": str(exc), "retryable": True},
                ensure_ascii=False,
            )
            yield f"event: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _load_context(request: Request, user_id: int, thread_id: str) -> object | None:
    """读取会话内累计 brief（若存在）。"""
    from travel_agent.db import ConversationContextRepository, session_scope

    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        return await ConversationContextRepository(session).load(user_id, thread_id)


async def _run_and_stream(
    request: Request,
    settings: Settings,
    user: UserRow,
    thread_id: str,
    message: str,
) -> AsyncIterator[str]:
    from travel_agent.agent.graph import build_graph
    from travel_agent.agent.tools.registry import ToolRegistry
    from travel_agent.services.llm import DeepSeekLLM

    loaded_brief = await _load_context(request, user.id, thread_id)

    llm = DeepSeekLLM(settings)
    ctx = ToolRegistry(settings)
    graph = build_graph(settings=settings, llm=llm, ctx=ctx)

    from langchain_core.messages import HumanMessage

    config = {"configurable": {"thread_id": thread_id}}
    yield f'event: status\ndata: {{"thread_id": "{thread_id}"}}\n\n'

    final: dict[str, object] = {}
    initial: dict[str, object] = {"messages": [HumanMessage(content=message)]}
    if loaded_brief is not None:
        initial["brief"] = loaded_brief
    chunks = graph.astream(initial, config=config)

    async for frame in sse_stream(chunks, thread_id=thread_id, final=final):
        yield frame

    # 流结束后落库：会话内记忆 + 成功行程留存（失败仅降级记日志）
    await _persist(request, user.id, thread_id, final)


async def _persist(
    request: Request, user_id: int, thread_id: str, final: dict[str, object]
) -> None:
    from travel_agent.logging_conf import get_logger

    log = get_logger(component="chat_persist")
    from typing import cast

    from travel_agent.db import (
        ConversationContextRepository,
        PlanRepository,
        session_scope,
    )
    from travel_agent.domain.brief import TravelBrief
    from travel_agent.domain.diff import PlanDiff
    from travel_agent.domain.plan import TripPlan

    factory = request.app.state.session_factory
    try:
        async with session_scope(factory) as session:
            # 会话内记忆：写回（仅当 brief 存在；chitchat 不改则 touch updated_at）
            brief = final.get("brief")
            if brief is not None:
                await ConversationContextRepository(session).save(
                    user_id, thread_id, cast(TravelBrief, brief)
                )
            # 成功行程留存：仅当本轮实际生成了计划
            plan = final.get("plan")
            if plan is not None:
                plan_version = final.get("plan_version", 1)
                version_int = plan_version if isinstance(plan_version, int) else 1
                diff = cast(PlanDiff | None, final.get("plan_diff"))
                await PlanRepository(session).save_snapshot(
                    cast(TripPlan, plan),
                    thread_id,
                    version_int,
                    diff_json=(diff.model_dump_json() if diff is not None else None),
                    trigger_message_id=thread_id[:64],
                    user_id=user_id,
                )
    except Exception:
        log.exception("chat_persist_failed", thread_id=thread_id)


@router.get("/{thread_id}/history", response_model=list[MessageOut])
async def history(
    thread_id: str,
    settings: Settings = Depends(get_settings),
    user: UserRow = Depends(get_current_user),
) -> list[MessageOut]:
    # web 流不落 messages；历史端点仅返回会话内累计记忆（无对话留痕）。
    return []
