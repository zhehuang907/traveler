"""命令行入口。

uv run travel-agent doctor [--online]   环境自检
uv run travel-agent serve [--reload]    启动 Web 服务
uv run travel-agent plan "成都4天"       命令行规划（阶段三交付）
uv run travel-agent rollback <plan_id> <version>  回滚行程版本（阶段四交付）
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from typing import TYPE_CHECKING

from travel_agent import __version__
from travel_agent import doctor as doctor_module
from travel_agent.config import Settings, get_settings

if TYPE_CHECKING:
    from travel_agent.agent.state import TravelState
    from travel_agent.services.llm import TokenUsage

__all__ = ["main", "run"]


def _cmd_doctor(args: argparse.Namespace) -> int:
    # 显式传 []：doctor.main 的 argv=None 会回落到 sys.argv，嵌套调用时会误读父进程参数
    return doctor_module.main(["--online"] if args.online else [])


def _cmd_serve(args: argparse.Namespace) -> int:
    # 惰性导入：仅真正启动服务时才加载 uvicorn（其导入链会拉起 multiprocessing）
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "travel_agent.main:app",
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=args.reload,
        log_config=None,  # 日志统一由 logging_conf 管理，避免 uvicorn 覆盖
    )
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_run_plan(args.query))
    except KeyboardInterrupt:  # pragma: no cover - 交互取消
        print("已取消")
        return 130


def _cmd_rollback(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_run_rollback(args.plan_id, args.version))
    except KeyboardInterrupt:  # pragma: no cover
        print("已取消")
        return 130


async def _run_plan(query: str) -> int:
    # 惰性导入：doctor/help 不应被 langgraph/数据库链拖累
    from langchain_core.messages import HumanMessage

    from travel_agent.agent.checkpointer import open_checkpointer
    from travel_agent.agent.graph import build_graph
    from travel_agent.agent.tools.registry import ToolRegistry
    from travel_agent.logging_conf import configure_logging
    from travel_agent.services.llm import LLMError, build_structured_llm

    settings = get_settings()
    configure_logging(settings.log_level, settings.effective_log_json)
    settings.ensure_dirs()
    llm = build_structured_llm(settings)
    if llm is None:
        print("未配置 LLM_API_KEY，请在 .env 填写 DeepSeek Key 后重试（参考 docs/SETUP.md）")
        return 2

    ctx = ToolRegistry(settings)
    thread_id = uuid.uuid4().hex
    try:
        async with open_checkpointer(settings) as checkpointer:
            graph = build_graph(settings=settings, llm=llm, ctx=ctx, checkpointer=checkpointer)
            result: TravelState = await graph.ainvoke(
                {"messages": [HumanMessage(content=query)]},
                config={"configurable": {"thread_id": thread_id}},
            )
    except LLMError as exc:
        print(f"LLM 调用失败：{exc}")
        return 1

    await _persist_run(settings, thread_id, query, result)
    _print_report(result, llm.usage_snapshot())
    return 0


async def _persist_run(settings: Settings, thread_id: str, query: str, result: TravelState) -> None:
    from travel_agent.db import (
        MessageRepository,
        PlanRepository,
        PreferenceRepository,
        create_engine,
        create_schema,
        make_session_factory,
        session_scope,
    )

    engine = create_engine(settings)
    await create_schema(engine)
    factory = make_session_factory(engine)
    try:
        async with session_scope(factory) as session:
            messages = MessageRepository(session)
            await messages.add(thread_id, "user", query)
            reply = result.get("reply")
            if reply:
                await messages.add(thread_id, "assistant", reply)
            plan = result.get("plan")
            diff = result.get("plan_diff")
            if plan is not None:
                await PlanRepository(session).save_snapshot(
                    plan,
                    thread_id,
                    result.get("plan_version", 1),
                    diff_json=diff.model_dump_json() if diff else None,
                    trigger_message_id=query[:64],
                )
            # 保存长期偏好（brief 中的偏好字段）
            brief = result.get("brief")
            if brief is not None:
                prefs = PreferenceRepository(session)
                await prefs.save(
                    thread_id,
                    {
                        "preferences": brief.preferences,
                        "dietary": brief.dietary,
                        "must_visit": brief.must_visit,
                        "avoid": brief.avoid,
                        "pace": brief.pace,
                    },
                )
    finally:
        await engine.dispose()


async def _run_rollback(plan_id: str, version: int) -> int:
    """回滚到指定版本：以新版本写入旧快照，不删除历史。"""
    from travel_agent.db import (
        PlanRepository,
        create_engine,
        create_schema,
        make_session_factory,
        session_scope,
    )

    settings = get_settings()
    settings.ensure_dirs()
    engine = create_engine(settings)
    await create_schema(engine)
    factory = make_session_factory(engine)
    try:
        async with session_scope(factory) as session:
            repo = PlanRepository(session)
            plan = await repo.get_version(plan_id, version)
            if plan is None:
                print(f"版本 {version} 不存在（plan_id={plan_id}）")
                return 1
            thread_id = await repo.get_thread_id(plan_id) or "unknown"
            new_version = await repo.latest_version_number(plan_id) + 1
            await repo.save_snapshot(
                plan,
                thread_id,
                new_version,
                diff_json=None,
                trigger_message_id=f"rollback→v{version}",
            )
            print(f"已回滚到版本 {version}，新版本号 {new_version}（plan_id={plan_id}）")
            return 0
    finally:
        await engine.dispose()


def _print_report(result: TravelState, usage: TokenUsage) -> None:
    plan = result.get("plan")
    print(result.get("reply", ""))
    if plan is not None:
        print("\n--- 逐日行程 ---")
        for day in plan.days:
            print(f"第 {day.day_index} 天 · {day.date}")
            for item in day.items:
                clock = f"{item.start_time:%H:%M}" if item.start_time else "  ·  "
                print(f"  {clock}  {item.title}")
        if plan.budget_cny is not None:
            budget = f"¥{plan.total_cost_cny:g} / 预算 ¥{plan.budget_cny:g}"
        else:
            budget = f"¥{plan.total_cost_cny:g} / 预算未设"
        print(f"\n合计花费：{budget}（人均约 ¥{plan.per_person_cost_cny:g}）")
    diff = result.get("plan_diff")
    if diff is not None and not diff.is_empty:
        print("\n--- 变更说明 ---")
        if diff.reason:
            print(f"修改范围：{diff.reason}")
        for entry in diff.added:
            print(f"+ 新增 第{entry.day}天 {entry.title}")
        for entry in diff.removed:
            print(f"- 删除 第{entry.day}天 {entry.title}")
        for change in diff.changed:
            print(
                f"~ 变更 第{change.day}天 {change.item_id} "
                f"{change.field}: {change.before} → {change.after}"
            )
    warnings = result.get("hydration_warnings", [])
    if warnings:
        print("\n编排提示：")
        for warning in warnings:
            print(f"- {warning}")
    unresolved = result.get("reflections", [])
    if unresolved:
        print("\n仍需人工确认的问题：")
        for issue in unresolved:
            print(f"- {issue}")
    traces = result.get("traces", [])
    degraded = [trace for trace in traces if trace.degraded]
    tail = f"，降级 {len(degraded)} 次" if degraded else ""
    print(f"\n工具调用：{len(traces)} 次{tail}")
    print(
        "Token："
        f"prompt={usage.prompt_tokens} completion={usage.completion_tokens} "
        f"total={usage.total_tokens} calls={usage.calls}"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="travel-agent", description="智能旅游规划 Agent")
    parser.add_argument("--version", action="version", version=f"travel-agent {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    doctor_parser = subparsers.add_parser("doctor", help="环境自检")
    doctor_parser.add_argument("--online", action="store_true", help="检查外部服务连通性")
    doctor_parser.set_defaults(handler=_cmd_doctor)

    serve_parser = subparsers.add_parser("serve", help="启动 Web 服务")
    serve_parser.add_argument("--host", default=None, help="监听地址，默认读 HOST")
    serve_parser.add_argument("--port", type=int, default=None, help="端口，默认读 PORT")
    serve_parser.add_argument("--reload", action="store_true", help="开发热重载")
    serve_parser.set_defaults(handler=_cmd_serve)

    plan_parser = subparsers.add_parser("plan", help="命令行跑一次旅行规划（无前端调试）")
    plan_parser.add_argument("query", help="自然语言需求，如：成都4天 预算5000 爱吃辣")
    plan_parser.set_defaults(handler=_cmd_plan)

    rollback_parser = subparsers.add_parser("rollback", help="回滚行程到指定版本")
    rollback_parser.add_argument("plan_id", help="行程 ID")
    rollback_parser.add_argument("version", type=int, help="目标版本号")
    rollback_parser.set_defaults(handler=_cmd_rollback)
    return parser


def run(argv: list[str] | None = None) -> int:
    """解析并执行命令，返回进程退出码。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 0
    handler = args.handler
    return int(handler(args))


def main() -> None:
    """控制台脚本入口（pyproject project.scripts）。"""
    sys.exit(run())


if __name__ == "__main__":
    main()
