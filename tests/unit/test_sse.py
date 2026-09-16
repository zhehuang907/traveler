"""sse_stream 适配器测试：LangGraph updates 模式的单键 dict chunk → SSE 帧。"""

import json
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from travel_agent.api.sse import sse_stream
from travel_agent.domain.plan import PlanDay, PlanItem, TripPlan, new_plan_id


def _parse_frames(raw_frames: list[str]) -> list[tuple[str, dict[str, Any]]]:
    frames: list[tuple[str, dict[str, Any]]] = []
    for raw in raw_frames:
        event_line, data_line = raw.strip().split("\n", 1)
        frames.append((event_line[len("event: ") :], json.loads(data_line[len("data: ") :])))
    return frames


async def _collect(
    chunks: list[dict[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    async def _agen() -> AsyncIterator[dict[str, dict[str, Any]]]:
        for chunk in chunks:
            yield chunk

    raw_frames: list[str] = []
    async for frame in sse_stream(_agen(), thread_id="t1"):
        raw_frames.append(frame)
    return _parse_frames(raw_frames)


def _sample_plan() -> TripPlan:
    return TripPlan(
        plan_id=new_plan_id(),
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 1),
        travelers=2,
        days=[
            PlanDay(
                day_index=1,
                date=date(2026, 10, 1),
                items=[
                    PlanItem(item_id="d1-1", title="武侯祠", category="attraction", cost_cny=50)
                ],
            )
        ],
    )


async def test_updates_dict_chunks_produce_events() -> None:
    """保真回归：chunk 是 {node_name: state_update} 单键 dict，不是 (name, state) 元组。"""
    chunks: list[dict[str, dict[str, Any]]] = [
        {"parse_intent": {"intent": "new_plan"}},
        {"search": {"traces": []}},
        {"compose_plan": {"plan": _sample_plan(), "plan_version": 1}},
        {"respond": {"reply": "行程已生成", "hydration_warnings": ["酒店信息待确认"]}},
    ]
    frames = await _collect(chunks)
    events = [event for event, _ in frames]
    assert "plan" in events
    assert "token" in events
    assert "warning" in events
    assert events[-1] == "done"
    plan_data = next(data for event, data in frames if event == "plan")
    assert plan_data["destination"] == "成都"
    token_data = next(data for event, data in frames if event == "token")
    assert token_data["text"] == "行程已生成"
    done_data = frames[-1][1]
    assert done_data["thread_id"] == "t1"
    assert done_data["plan_version"] == 1


async def test_parse_intent_chunk_is_skipped() -> None:
    frames = await _collect([{"parse_intent": {"intent": "chitchat"}}])
    assert [event for event, _ in frames] == ["done"]
