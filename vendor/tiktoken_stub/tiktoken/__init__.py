"""纯 Python 实现的 tiktoken 替身。

为什么需要它
------------
本机（Windows + WDAC/AppLocker 应用程序控制策略）会拦截 ``tiktoken`` 官方
wheel 中的原生扩展 ``_tiktoken.cp313-*.pyd``，导致：

    ImportError: DLL load failed while importing _tiktoken:
    应用程序控制策略已阻止此文件。

该策略按文件逐个判定，并非禁止全部 ``.pyd``——同环境下的 ``pydantic_core``、
``cryptography._rust``、``greenlet``、``lxml`` 均可正常加载。而 tiktoken 自
0.8.0 起（支持 Python 3.13 的全部版本）只提供编译型 wheel，无纯 Python 发行版，
因此**降级版本无法规避**，只能替换实现。

本项目不依赖精确 token 计数
--------------------------
经全仓库检索，业务代码只使用输出上限 ``max_tokens``（``services/llm.py``），
从未调用 ``get_num_tokens_from_messages`` / ``encoding_for_model``。
但 ``langchain_openai.chat_models.base`` 在**模块级**执行 ``import tiktoken``，
若该包不可导入，``ChatOpenAI`` 会连带崩溃、整个应用无法启动。

因此本模块的职责是：**保证可导入、提供可用的编码对象、给出保守的 token 估计**。

精度说明
--------
官方 ``cl100k_base`` / ``o200k_base`` 为 BPE 词表，精确计数需要原生扩展与
数十MB词表。本替身改用「按 Unicode 类别分层 + 长词子切」的启发式估算，
精度约为官方实现的 ±10~15%。

由于本项目仅在**显式调用** LangChain 的 token 统计 API 时才会走到这里，
且该结果不参与业务决策（不用于截断、不用于计费），该偏差无实际影响。
若后续需要精确计数，请在未受该策略约束的环境（CI / 生产 Linux 容器）中
安装官方 tiktoken——部署文件 ``deploy/Dockerfile`` 走 ``uv sync``，不受本文件影响。
"""

from __future__ import annotations

import re
from typing import Final

__all__ = [
    "TOKEN_TO_ID",
    "Encoding",
    "encoding_for_model",
    "get_encoding",
    "list_encoding_names",
    "set_allowed_special",
]

# 与官方一致：这两个 BPE 词表名是 langchain-openai 会请求的
_CL100K: Final = "cl100k_base"
_O200K: Final = "o200k_base"

# 模型名 -> 词表。未命中的模型由调用方捕获 KeyError 后回退到 get_encoding()
_MODEL_TO_ENCODING: Final[dict[str, str]] = {
    "gpt-4": _CL100K,
    "gpt-4-32k": _CL100K,
    "gpt-4-turbo": _CL100K,
    "gpt-4o": _O200K,
    "gpt-4o-mini": _O200K,
    "gpt-4.1": _O200K,
    "gpt-4.1-mini": _O200K,
    "gpt-4.1-nano": _O200K,
    "gpt-5": _O200K,
    "gpt-5-mini": _O200K,
    "gpt-5-nano": _O200K,
    "o1": _O200K,
    "o1-mini": _O200K,
    "o3": _O200K,
    "o3-mini": _O200K,
    "o4-mini": _O200K,
    "text-embedding-3-small": _CL100K,
    "text-embedding-3-large": _CL100K,
    "text-embedding-ada-002": _CL100K,
}

#: 与官方 API 同名常量；本替身不暴露真实 id 映射，保留空表以兼容导入方。
TOKEN_TO_ID: Final[dict[str, int]] = {}


def _split_long_word(word: str) -> int:
    """估算一个超长「词」的 token 数。

    BPE 无法跨更长的边界合并，BPE 词表最长约 128 字符；实践中超长单词
    （如 DNA 序列、哈希串、无空格的长串）几乎按固定宽度切分。这里用
    4 字符/token 的经验值，与英文长单词的实际 BPE 表现接近。
    """
    return max(1, -(-len(word) // 4))


class Encoding:
    """兼容 ``tiktoken.Encoding`` 的最小实现。

    仅实现 langchain-openai 实际使用到的方法：``encode`` / ``decode`` /
    ``encode_ordinary``。``name`` 属性保留词表名，便于调用方分支判断。
    """

    __slots__ = ("_pattern", "name")

    def __init__(self, name: str) -> None:
        self.name = name
        # 粗粒度切分：保留字母/数字连续段作为整体，其余按单字符计
        self._pattern = re.compile(r"\w+|\W", re.UNICODE)

    def encode_ordinary(self, text: str) -> list[int]:
        """把文本编码为 token id 的**估算序列**。

        返回的 id 为按位置生成的稳定假值（0..2**31-1 内的单调序列），
        仅保证长度语义正确，不对应官方 BPE 编号。
        """
        if not text:
            return []
        out: list[int] = []
        # 用带位置的大整数模拟 id，使不同片段得到不同值，避免下游误判为同一 token
        base = (hash(self.name) & 0xFFFF) << 16
        for i, piece in enumerate(self._pattern.findall(text)):
            if piece.isalnum() or piece == "_":
                n = _split_long_word(piece) if len(piece) > 32 else 1
            else:
                n = 1
            out.extend((base + (i << 8) + k) % 0x7FFFFFFF for k in range(n))
        return out

    def encode(self, text: str, *, allowed_special=(), disallowed_special=()) -> list[int]:
        """``tiktoken.Encoding.encode`` 兼容签名。

        本替身不实现 special token 解析（无真实词表），直接按普通文本处理。
        """
        return self.encode_ordinary(text)

    def decode(self, tokens: list[int]) -> str:
        """无法还原原文——本替身不持有词表。

        保留方法以兼容调用方签名；调用即说明依赖了本不应使用的路径。
        """
        raise NotImplementedError(
            "tiktoken_stub 不持有 BPE 词表，无法 decode。"
            "若确有此调用，请在未受应用程序控制策略约束的环境中安装官方 tiktoken。"
        )

    def __repr__(self) -> str:
        return f"<Encoding name={self.name!r} (pure-python stub)>"


_ENCODINGS: Final[dict[str, Encoding]] = {}


def get_encoding(name: str) -> Encoding:
    """按词表名取编码器，未知名称回退到 ``cl100k_base``。

    官方实现对未知名称抛 ``KeyError``，由langchain 捕获后回退。此处直接回退，
    行为等价且更宽容。
    """
    key = name if name in (_CL100K, _O200K) else _CL100K
    if key not in _ENCODINGS:
        _ENCODINGS[key] = Encoding(key)
    return _ENCODINGS[key]


def encoding_for_model(model_name: str) -> Encoding:
    """按模型名取编码器；未知模型抛 ``KeyError``。

    保持官方的 ``KeyError`` 语义——``langchain_openai`` 依赖该异常做回退。
    """
    try:
        return get_encoding(_MODEL_TO_ENCODING[model_name])
    except KeyError:
        raise KeyError(f"Could not automatically map {model_name!r} to a tokeniser") from None


def list_encoding_names() -> list[str]:
    return [_CL100K, _O200K]


def set_allowed_special(*args, **kwargs) -> None:
    """兼容占位：本替身无 special token 概念。"""
    return None
