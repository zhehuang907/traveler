"""SSE 事件适配器：把 LangGraph 状态增量转为 text/event-stream 帧。"""

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

__all__ = ["sse_stream"]


def _event(event: str, data: dict[str, Any]) -> str:
    """格式化一个 SSE 帧。"""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


async def sse_stream(
    chunks: AsyncIterator[Mapping[str, Mapping[str, Any]]],
    *,
    thread_id: str,
    final: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """把图的节点完成增量转为 SSE 事件流。

    LangGraph ``astream``（updates 模式）逐个 yield 单键 dict：
    ``{node_name: state_update}``。

    当传入 ``final`` 时，途中持续捕获最后的 ``brief / plan / plan_version /
    plan_diff``，供流结束后落库使用（成功行程留存；chitchat 无 plan 则不入库）。
    """
    plan_version = 1
    async for chunk in chunks:
        node_name, state = next(iter(chunk.items()))

        # 捕获可用于"会话内记忆 + 成功行程留存"的最终状态
        if final is not None:
            # 任一节点更新过 brief 都记录（parse_intent 之后的值即本轮合并结果）
            if state.get("brief") is not None:
                final["brief"] = state.get("brief")
            if (
                node_name in ("compose_plan", "revise_plan", "patch_plan")
                and state.get("plan") is not None
            ):
                final["plan"] = state.get("plan")
                final["plan_version"] = state.get("plan_version", plan_version)
                final["plan_diff"] = state.get("plan_diff")
            # 回复文本：供对话历史落库（clarify 的问题与 respond 的回答）
            if node_name in ("clarify_brief", "respond") and state.get("reply"):
                final["reply"] = state.get("reply")

        if node_name == "parse_intent":
            continue

        if node_name == "clarify_brief":
            yield _event(
                "clarify",
                {
                    "question": state.get("reply", ""),
                    "slots": state.get("target_scope", []),
                },
            )
            continue

        if node_name in ("search", "answer_info"):
            for trace in state.get("traces", []):
                yield _event("tool_start", {"tool": trace.tool})
                yield _event(
                    "tool_end",
                    {
                        "tool": trace.tool,
                        "ok": trace.ok,
                        "degraded": trace.degraded,
                    },
                )

        if node_name in ("compose_plan", "revise_plan", "patch_plan"):
            plan = state.get("plan")
            if plan is not None:
                plan_version = state.get("plan_version", plan_version)
                diff = state.get("plan_diff")
                if diff is not None and not diff.is_empty:
                    yield _event("plan_patch", diff.model_dump())
                yield _event("plan", json.loads(plan.model_dump_json()))
            continue

        if node_name == "respond":
            reply = state.get("reply", "")
            if reply:
                yield _event("token", {"text": reply})
            for warning in state.get("hydration_warnings", []):
                yield _event("warning", {"type": "hydration", "message": warning})
            for reflection in state.get("reflections", []):
                yield _event(
                    "warning",
                    {"type": "loop_cap", "message": reflection},
                )
            continue

    yield _event("done", {"thread_id": thread_id, "plan_version": plan_version})
