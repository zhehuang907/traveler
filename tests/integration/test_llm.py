"""DeepSeekLLM 结构化输出封装测试（Fake ChatModel，不触网）。"""

from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel, SecretStr

from travel_agent.config import Settings
from travel_agent.services.llm import DeepSeekLLM, LLMError, TokenUsage


class _Sample(BaseModel):
    answer: str


class _FakeRunnable:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.calls.append(messages)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeChat:
    def __init__(
        self, structured_outcomes: list[Any] | None = None, replies: list[Any] | None = None
    ):
        self._structured_outcomes = structured_outcomes or []
        self.replies = replies or []
        self.runnable: _FakeRunnable | None = None
        self.structured_method: str | None = None

    def with_structured_output(
        self,
        schema: type[BaseModel],
        *,
        method: str = "json_schema",
        include_raw: bool = False,
    ) -> _FakeRunnable:
        self.structured_method = method
        self.runnable = _FakeRunnable(self._structured_outcomes)
        return self.runnable

    async def ainvoke(self, messages: list[Any]) -> Any:
        outcome = self.replies.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _make_llm(fake_chat: _FakeChat) -> DeepSeekLLM:
    llm = DeepSeekLLM.__new__(DeepSeekLLM)
    # 绕过 ChatOpenAI 构造（测试不触网）：直接注入替身与用量桶
    object.__setattr__(llm, "_chat", fake_chat)
    object.__setattr__(llm, "_usage", TokenUsage())
    return llm


def _raw(parsed: Any, *, error: Any = None) -> dict[str, Any]:
    # include_raw=True 时 langchain 实际返回 dict（与生产 _parse_once 的分支一致）
    message = AIMessage(
        content="x", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    )
    return {"raw": message, "parsed": parsed, "parsing_error": error}


async def test_aparse_success_accumulates_usage() -> None:
    chat = _FakeChat([_raw(_Sample(answer="ok"))])
    llm = _make_llm(chat)
    result = await llm.aparse(_Sample, system="s", user="u")
    assert result.answer == "ok"
    usage = llm.usage_snapshot()
    assert usage.calls == 1 and usage.total_tokens == 15


async def test_aparse_uses_function_calling_method() -> None:
    # DeepSeek 等兼容端点不支持 json_schema 模式的 response_format，必须走 function_calling
    chat = _FakeChat([_raw(_Sample(answer="ok"))])
    llm = _make_llm(chat)
    await llm.aparse(_Sample, system="s", user="u")
    assert chat.structured_method == "function_calling"


async def test_aparse_retry_feeds_error_back_then_succeeds() -> None:
    chat = _FakeChat([_raw(None, error=ValueError("坏 JSON")), _raw(_Sample(answer="fixed"))])
    llm = _make_llm(chat)
    result = await llm.aparse(_Sample, system="s", user="u")
    assert result.answer == "fixed"
    assert chat.runnable is not None
    assert len(chat.runnable.calls) == 2
    assert "坏 JSON" in str(chat.runnable.calls[1][-1].content)


async def test_aparse_wrong_type_twice_raises() -> None:
    chat = _FakeChat([_raw(object()), _raw(None, error=RuntimeError("again"))])
    llm = _make_llm(chat)
    with pytest.raises(LLMError):
        await llm.aparse(_Sample, system="s", user="u")


async def test_acomplete_text_and_failure() -> None:
    good = AIMessage(
        content="你好", usage_metadata={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
    )
    chat = _FakeChat(replies=[good, RuntimeError("网络挂了")])
    llm = _make_llm(chat)
    assert await llm.acomplete(system="s", user="u") == "你好"
    with pytest.raises(LLMError, match="文本生成失败"):
        await llm.acomplete(system="s", user="u")


def test_init_wires_llm_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # LLM 超时必须取 LLM_TIMEOUT（长文本生成实测 9-24s），不得复用 EXTERNAL_TIMEOUT
    captured: dict[str, Any] = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("travel_agent.services.llm.ChatOpenAI", _FakeChatOpenAI)
    settings = Settings(
        app_env="test",
        llm_api_key=SecretStr("sk-x"),
        llm_timeout=99.0,
        external_timeout=10.0,
    )
    DeepSeekLLM(settings)
    assert captured["timeout"] == 99.0
