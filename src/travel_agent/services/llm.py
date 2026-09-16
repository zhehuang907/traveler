"""LLM 工厂与结构化输出封装（DeepSeek / OpenAI 兼容协议；ADR-008）。

纪律：
- 仅通过 langchain-openai 的 OpenAI 兼容客户端接入，不绑定厂商私有参数；
- 结构化输出失败自动重试一次，并把校验错误原文回灌给模型；
- token 用量在实例上累计，供 CLI/API 输出成本统计；
- 传输层重试不在这里（客户端 max_retries=0），失败语义对上层显式可见。
"""

from typing import Any, TypeVar, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from travel_agent.config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """LLM 调用或结构化输出失败（两次尝试均未通过）。"""


class TokenUsage(BaseModel):
    """一次会话内的累计 token 用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0

    def add(self, usage: dict[str, int] | None) -> None:
        if not usage:
            return
        self.prompt_tokens += int(usage.get("input_tokens", 0))
        self.completion_tokens += int(usage.get("output_tokens", 0))
        self.total_tokens += int(usage.get("total_tokens", 0))
        self.calls += 1


class StructuredLLM:
    """结构化 LLM 协议（生产实现 + 测试替身都满足）。"""

    async def aparse(
        self,
        schema: type[T],
        *,
        system: str,
        user: str,
    ) -> T:
        """按 Pydantic schema 输出；校验失败回灌错误重试一次。"""
        raise NotImplementedError

    async def acomplete(self, *, system: str, user: str) -> str:
        """自由文本补全（聚合追问/总结用）。"""
        raise NotImplementedError

    def usage_snapshot(self) -> TokenUsage:
        raise NotImplementedError


class DeepSeekLLM(StructuredLLM):
    """DeepSeek（或任意 OpenAI 兼容端点）实现。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._chat = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=settings.llm_base_url,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            # 长文本生成（行程草稿数千 token）实测 9-24s，必须用 LLM_TIMEOUT；
            # 复用 EXTERNAL_TIMEOUT（10s）会在高峰期误杀每次调用
            timeout=settings.llm_timeout,
            max_retries=0,
        )
        self._usage = TokenUsage()

    async def aparse(
        self,
        schema: type[T],
        *,
        system: str,
        user: str,
    ) -> T:
        # 多数 OpenAI 兼容端点（含 DeepSeek）不支持 json_schema 模式的 response_format
        # （langchain 默认值），改用 function_calling：schema 经 tools 下发，兼容面最广
        runnable = self._chat.with_structured_output(
            schema, method="function_calling", include_raw=True
        )
        messages: list[BaseMessage] = [SystemMessage(content=system), HumanMessage(content=user)]
        try:
            return await self._parse_once(runnable, messages, schema)
        except Exception as first_error:  # 错误文本需回灌模型再试一次
            retry_messages = [
                *messages,
                HumanMessage(
                    content=f"上次输出未通过结构校验：{first_error}。请严格按要求重新输出。"
                ),
            ]
            try:
                return await self._parse_once(runnable, retry_messages, schema)
            except Exception as second_error:
                raise LLMError(f"结构化输出两次均失败：{second_error}") from second_error

    async def _parse_once(
        self,
        runnable: Any,
        messages: list[BaseMessage],
        schema: type[T],
    ) -> T:
        response = await runnable.ainvoke(messages)
        # include_raw=True 时 langchain 返回 {"raw": ..., "parsed": ..., "parsing_error": ...}
        if isinstance(response, dict):
            parsed: object = response.get("parsed")
            raw: object = response.get("raw")
            parsing_error: object = response.get("parsing_error")
        else:  # 兼容对象形态的历史行为
            parsed = getattr(response, "parsed", None)
            raw = getattr(response, "raw", None)
            parsing_error = getattr(response, "parsing_error", None)
        if isinstance(raw, AIMessage):
            self._usage.add(cast("dict[str, int] | None", raw.usage_metadata))
        if parsing_error is not None:
            raise LLMError(str(parsing_error))
        if not isinstance(parsed, schema):
            raise LLMError(f"输出类型不是 {schema.__name__}")
        return parsed

    async def acomplete(self, *, system: str, user: str) -> str:
        try:
            message = await self._chat.ainvoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )
        except Exception as exc:
            raise LLMError(f"文本生成失败：{exc}") from exc
        metadata = cast("dict[str, int] | None", getattr(message, "usage_metadata", None))
        self._usage.add(metadata)
        content = cast(object, message.content)
        return content if isinstance(content, str) else str(content)

    def usage_snapshot(self) -> TokenUsage:
        return self._usage.model_copy(deep=True)


def build_structured_llm(settings: Settings) -> StructuredLLM | None:
    """未配置 Key 时返回 None（由上层给出明确错误，而不是在调用点炸栈）。"""
    if not settings.llm_configured:
        return None
    return DeepSeekLLM(settings)
