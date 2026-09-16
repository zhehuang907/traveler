"""CLI 入口测试。"""

from datetime import date

import pytest

from travel_agent.agent.state import TravelState
from travel_agent.cli import _persist_run, _print_report, run
from travel_agent.config import Settings
from travel_agent.db import (
    MessageRepository,
    PlanRepository,
    create_engine,
    make_session_factory,
    session_scope,
)
from travel_agent.domain.plan import PlanDay, PlanItem, TripPlan
from travel_agent.services.llm import LLMError, TokenUsage


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert run([]) == 0
    assert "travel-agent" in capsys.readouterr().out


def test_version_exits() -> None:
    with pytest.raises(SystemExit):
        run(["--version"])


def test_doctor_command() -> None:
    assert run(["doctor"]) == 0


def test_serve_passes_uvicorn_args(monkeypatch: pytest.MonkeyPatch) -> None:
    # 部分受应用控制策略限制的 Windows 机器会禁止加载 multiprocessing（uvicorn 依赖）；
    # pytest 9 的 importorskip 默认只跳过 ModuleNotFoundError，需显式放宽到 ImportError
    uvicorn = pytest.importorskip("uvicorn", exc_type=ImportError)
    captured: dict[str, object] = {}

    def fake_run(target: str, **kwargs: object) -> None:
        captured["target"] = target
        captured.update(kwargs)

    # cli 内部引用的是同一个 uvicorn 模块对象，直接 patch 模块属性即可生效
    monkeypatch.setattr(uvicorn, "run", fake_run)
    code = run(["serve", "--host", "0.0.0.0", "--port", "9999"])
    assert code == 0
    assert captured["target"] == "travel_agent.main:app"
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9999
    assert captured["log_config"] is None
    assert captured["reload"] is False


def test_plan_without_api_key_returns_2(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("travel_agent.cli.get_settings", lambda: settings)
    code = run(["plan", "成都4天"])
    assert code == 2
    assert "LLM_API_KEY" in capsys.readouterr().out


def test_plan_llm_failure_returns_1(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _BoomLLM:
        async def aparse(self, *args: object, **kwargs: object) -> object:
            raise LLMError("boom")

        def usage_snapshot(self) -> TokenUsage:
            return TokenUsage()

    monkeypatch.setattr("travel_agent.cli.get_settings", lambda: settings)
    monkeypatch.setattr("travel_agent.services.llm.build_structured_llm", lambda _: _BoomLLM())
    assert run(["plan", "成都4天"]) == 1
    assert "LLM 调用失败" in capsys.readouterr().out


async def test_persist_run_saves_messages_and_plan(settings: Settings) -> None:
    plan = TripPlan(
        plan_id="cli-1",
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 1),
        travelers=2,
        budget_cny=5000,
        days=[
            PlanDay(
                day_index=1,
                date=date(2026, 10, 1),
                items=[PlanItem(item_id="d1-1", title="武侯祠", category="attraction")],
            )
        ],
    )
    result: TravelState = {
        "reply": "行程已生成",
        "plan": plan,
        "plan_version": 1,
        "traces": [],
    }
    await _persist_run(settings, "thread-cli", "成都4天", result)

    engine = create_engine(settings)
    try:
        async with session_scope(make_session_factory(engine)) as session:
            messages = await MessageRepository(session).list("thread-cli")
            assert [m.role for m in messages] == ["user", "assistant"]
            loaded = await PlanRepository(session).get_plan("cli-1")
            assert loaded is not None and loaded.days[0].items[0].title == "武侯祠"
    finally:
        await engine.dispose()


def test_print_report_renders_sections(capsys: pytest.CaptureFixture[str]) -> None:
    plan = TripPlan(
        plan_id="cli-2",
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 1),
        travelers=2,
        budget_cny=5000,
        days=[
            PlanDay(
                day_index=1,
                date=date(2026, 10, 1),
                items=[
                    PlanItem(
                        item_id="d1-1",
                        title="锦里",
                        category="attraction",
                        cost_cny=0,
                    )
                ],
            )
        ],
    )
    _print_report(
        {"reply": "完成", "plan": plan, "hydration_warnings": [], "reflections": [], "traces": []},
        TokenUsage(calls=1, total_tokens=42),
    )
    out = capsys.readouterr().out
    assert "逐日行程" in out and "锦里" in out and "calls=1" in out
