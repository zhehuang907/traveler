"""对话接口：POST /api/chat（SSE 流式）、GET history。

需登录。会话内记忆：把该 (user, thread) 累计的 brief 回灌进图（支持分批补充
时间/地点/人数），本轮结束再把新 brief 写回；仅当成功生成行程时才落库 plans。
对话历史：每轮 user / assistant 消息都写入 messages（按用户隔离），
供刷新/重开后恢复会话；chitchat / 开放问答同样留痕，但不落 plans。
"""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import cast

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from travel_agent.agent.graph import build_graph
from travel_agent.agent.prompts import render_pair
from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.api.deps import get_current_user, get_db, get_llm, get_settings
from travel_agent.api.errors import AppError, ErrorCode
from travel_agent.api.sse import sse_stream
from travel_agent.config import Settings
from travel_agent.db import (
    ConversationContextRepository,
    MessageRepository,
    PlanRepository,
    session_scope,
)
from travel_agent.db.models import UserRow
from travel_agent.domain.brief import TravelBrief
from travel_agent.domain.diff import PlanDiff
from travel_agent.domain.plan import TripPlan
from travel_agent.logging_conf import get_logger
from travel_agent.services.llm import DeepSeekLLM, StructuredLLM
from travel_agent.services.plan_import import ImportError_, extract_text

__all__ = ["router"]

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    thread_id: str | None = None
    # 上限放宽以容纳聊天窗内上传的文档内容（见 POST /api/chat/upload）
    message: str = Field(min_length=1, max_length=13_000)
    client_ts: datetime | None = None


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: datetime | None = None


class DocDigest(BaseModel):
    """文档提炼结果：仅返回与行程规划相关的要点，不携带全文。"""

    text: str


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
            # 对外只给通用文案，异常详情进日志（防止泄露上游密钥/内部地址等敏感信息）
            get_logger(component="chat").exception(
                "chat_stream_failed", thread_id=thread_id, exc_type=type(exc).__name__
            )
            error_data = json.dumps(
                {
                    "code": "UPSTREAM_ERROR",
                    "message": "服务暂时不可用，请稍后重试",
                    "retryable": True,
                },
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
    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        return await ConversationContextRepository(session).load(user_id, thread_id)


async def _load_history_messages(
    request: Request,
    user_id: int,
    thread_id: str,
    max_messages: int = 30,
    max_chars: int = 20_000,
) -> list[BaseMessage]:
    """把该会话历史（业务库权威）转成 LangChain 消息，供图回灌上下文。

    图每次重新编译（无跨进程 checkpointer），不回灌历史会使助手对之前
    的对话“失忆”；这里从最近往回收且受条数/字符预算约束，防止 token 溢出。
    """
    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        rows = await MessageRepository(session).list(thread_id, limit=max_messages, user_id=user_id)
    messages: list[BaseMessage] = []
    budget = max_chars
    for row in reversed(rows):
        content = str(row.content or "")
        if messages and budget - len(content) < 0:
            break
        messages.append(
            AIMessage(content=content) if row.role == "assistant" else HumanMessage(content=content)
        )
        budget -= len(content)
    messages.reverse()
    return messages


async def _run_and_stream(
    request: Request,
    settings: Settings,
    user: UserRow,
    thread_id: str,
    message: str,
) -> AsyncIterator[str]:
    loaded_brief = await _load_context(request, user.id, thread_id)

    llm = DeepSeekLLM(settings)
    ctx = ToolRegistry(settings)
    graph = build_graph(settings=settings, llm=llm, ctx=ctx)

    config = {"configurable": {"thread_id": thread_id}}
    yield f'event: status\ndata: {{"thread_id": "{thread_id}"}}\n\n'

    final: dict[str, object] = {}
    history = await _load_history_messages(request, user.id, thread_id)
    initial: dict[str, object] = {"messages": [*history, HumanMessage(content=message)]}
    if loaded_brief is not None:
        initial["brief"] = loaded_brief
    chunks = graph.astream(initial, config=config)

    async for frame in sse_stream(chunks, thread_id=thread_id, final=final):
        yield frame

    # 流结束后落库：会话内记忆 + 成功行程留存 + 对话消息（失败仅降级记日志）
    await _persist(request, user.id, thread_id, final, message)


async def _persist(
    request: Request, user_id: int, thread_id: str, final: dict[str, object], message: str
) -> None:
    """流结束后落库：对话消息 / 会话内记忆 / 成功行程，三类各用独立事务。

    拆开事务是关键：行程档案保存（save_snapshot 去重/外键最易抛错）失败时，
    绝不能让已发生的对话消息一起回滚——否则刷新页面就看不到上一轮对话。
    """
    log = get_logger(component="chat_persist")

    factory = request.app.state.session_factory

    # 1) 对话历史：user 消息 + assistant 回复（最高优先级，先独立提交）
    try:
        async with session_scope(factory) as session:
            messages = MessageRepository(session)
            await messages.add(thread_id, "user", message, user_id=user_id)
            reply = final.get("reply")
            if reply:
                await messages.add(thread_id, "assistant", str(reply), user_id=user_id)
    except Exception:
        log.exception("chat_messages_persist_failed", thread_id=thread_id)

    # 2) 会话内记忆：写回（仅当 brief 存在；chitchat 不改则 touch updated_at）
    brief = final.get("brief")
    if brief is not None:
        try:
            async with session_scope(factory) as session:
                await ConversationContextRepository(session).save(
                    user_id, thread_id, cast(TravelBrief, brief)
                )
        except Exception:
            log.exception("chat_brief_persist_failed", thread_id=thread_id)

    # 3) 成功行程留存：仅当本轮实际生成了计划
    plan = final.get("plan")
    if plan is not None:
        try:
            async with session_scope(factory) as session:
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
            log.exception("chat_plan_persist_failed", thread_id=thread_id)


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    user: UserRow = Depends(get_current_user),
    llm: StructuredLLM | None = Depends(get_llm),
) -> dict[str, str]:
    """上传行程文档（Word/PDF/文本）：解析后仅提炼行程要点，不返回全文。"""
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise AppError(ErrorCode.BAD_REQUEST, "文件不能超过 5MB", status_code=413)
    try:
        text = extract_text(file.filename or "", content)
    except ImportError_ as exc:
        raise AppError(ErrorCode.BAD_REQUEST, str(exc), status_code=400) from exc
    if not text.strip():
        raise AppError(
            ErrorCode.BAD_REQUEST,
            "文件中没有可读文本（可能是扫描件，请改用文字版）",
            status_code=400,
        )
    if llm is None:
        raise AppError(
            ErrorCode.LLM_NOT_CONFIGURED,
            "未配置 LLM_API_KEY，无法提炼文档内容",
            status_code=503,
        )
    # 只提炼与行程规划相关的要点，避免把整份文档灌入对话
    system, user_prompt = render_pair(
        "digest", filename=file.filename or "", document=text[:12_000]
    )
    digest = await llm.aparse(DocDigest, system=system, user=user_prompt)
    return {"filename": file.filename or "", "text": digest.text}


class ThreadSummary(BaseModel):
    thread_id: str
    last_message_at: datetime
    preview: str


@router.get("/threads", response_model=list[ThreadSummary])
async def list_threads(
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[ThreadSummary]:
    """我的会话列表（按最近消息倒序，preview 取最近一条 assistant 消息摘要）。"""
    repo = MessageRepository(session)
    threads = await repo.list_threads(user.id)
    return [
        ThreadSummary(thread_id=tid, last_message_at=ts, preview=preview)
        for tid, ts, preview in threads
    ]


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(
    thread_id: str,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    """删除我的某会话全部消息（按用户隔离）。"""
    from travel_agent.api.errors import AppError, ErrorCode

    repo = MessageRepository(session)
    removed = await repo.delete_thread(thread_id, user.id)
    if removed == 0:
        raise AppError(ErrorCode.NOT_FOUND, "会话不存在", status_code=404)


@router.get("/{thread_id}/history", response_model=list[MessageOut])
async def history(
    thread_id: str,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    """返回该会话的对话历史（按当前用户隔离，无记录时为空列表）。"""
    repo = MessageRepository(session)
    rows = await repo.list(thread_id, user_id=user.id)
    return [
        MessageOut(role=row.role, content=row.content, created_at=row.created_at) for row in rows
    ]
