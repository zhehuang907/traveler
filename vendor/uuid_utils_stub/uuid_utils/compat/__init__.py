"""``uuid_utils.compat`` 的纯 Python 实现。

为什么需要这个子包
------------------
部分框架（Django、pydantic 的 UUID 字段等）要求传入的必须是**标准库**
``uuid.UUID`` 实例，而不是第三方库的子类。官方 ``uuid_utils.UUID`` 是独立的
Rust 类，因此官方另提供 ``compat``：内部仍走高性能实现，但对外一律返回
标准库 ``uuid.UUID``。

本项目两个依赖方都在**模块级**导入本模块：

* ``langchain_core/utils/uuid.py`` → ``from uuid_utils.compat import uuid7``
* ``langsmith/_internal/_uuid.py`` → 同上

官方 ``compat/__init__.py`` 内部同样``import uuid_utils``（因而同样依赖原生
扩展），所以「只装 compat」并不能规避拦截——必须整个包一起替身。

与官方实现的差异
----------------
官方 ``compat`` 用 ``object.__new__(UUID)`` + ``object.__setattr__`` 绕过
``UUID.__init__`` 来构造标准库实例（比 ``UUID(int=...)`` 快，因为免去校验）。
本替身直接用 ``_stdlib_uuid.UUID(int=...)``：性能不是本项目的关切
（每次调用只生成一个 trace id），而 ``__init__`` 路径能保证所有不变量
（``is_safe`` 等属性）都被正确初始化，不会因手工填字段而留下隐患。
"""

from __future__ import annotations

import uuid as _stdlib_uuid
from typing import Final
from uuid import (
    NAMESPACE_DNS,
    NAMESPACE_OID,
    NAMESPACE_URL,
    NAMESPACE_X500,
    RESERVED_FUTURE,
    RESERVED_MICROSOFT,
    RESERVED_NCS,
    RFC_4122,
    UUID,
    SafeUUID,
    getnode,
)

import uuid_utils
from uuid_utils import _uuid4_int, _uuid7_int

NIL: Final = UUID("00000000-0000-0000-0000-000000000000")
MAX: Final = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")

__version__: Final = uuid_utils.__version__


def uuid1(node: int | None = None, clock_seq: int | None = None) -> UUID:
    """v1，返回标准库 ``UUID`` 实例。"""
    return UUID(int=uuid_utils.uuid1(node, clock_seq).int)


def uuid3(namespace: UUID, name: str | bytes) -> UUID:
    """v3（命名空间 + MD5），返回标准库 ``UUID`` 实例。"""
    ns = uuid_utils.UUID(namespace.hex) if namespace else namespace
    return UUID(int=uuid_utils.uuid3(ns, name).int)


def uuid4() -> UUID:
    """v4，返回标准库 ``UUID`` 实例。"""
    return UUID(int=_uuid4_int())


def uuid5(namespace: UUID, name: str | bytes) -> UUID:
    """v5（命名空间 + SHA-1），返回标准库 ``UUID`` 实例。"""
    ns = uuid_utils.UUID(namespace.hex) if namespace else namespace
    return UUID(int=uuid_utils.uuid5(ns, name).int)


def uuid6(node: int | None = None, timestamp: int | None = None) -> UUID:
    """v6（时间有序），返回标准库 ``UUID`` 实例。

    注意参数名与 ``uuid_utils.uuid6`` 略有差异：官方 ``compat`` 的签名只有
    ``(node, timestamp)``（不含 ``nanos``），此处保持一致以免签名不匹配。
    """
    return UUID(int=uuid_utils.uuid6(node, timestamp).int)


def uuid7(timestamp: int | None = None, nanos: int | None = None) -> UUID:
    """v7（Unix 时间有序），返回标准库 ``UUID`` 实例。

    ``timestamp`` 单位为**秒**，``nanos`` 为秒内亚秒纳秒数——这是两个依赖方
    的实际传参方式（``divmod(nanoseconds, 1e9)``），务必不要按毫秒理解。

    这是 ``langchain_core`` / ``langsmith`` 唯一用到的函数。
    """
    return UUID(int=_uuid7_int(timestamp, nanos))


def uuid8(bytes: bytes) -> UUID:
    """v8（完全自定义），返回标准库 ``UUID`` 实例。"""
    return UUID(int=uuid_utils.uuid8(bytes).int)


__all__ = [
    "MAX",
    "NAMESPACE_DNS",
    "NAMESPACE_OID",
    "NAMESPACE_URL",
    "NAMESPACE_X500",
    "NIL",
    "RESERVED_FUTURE",
    "RESERVED_MICROSOFT",
    "RESERVED_NCS",
    "RFC_4122",
    "UUID",
    "__version__",
    "getnode",
    "uuid1",
    "uuid3",
    "uuid4",
    "uuid5",
    "uuid6",
    "uuid7",
    "uuid8",
]

# ``SafeUUID`` 与 ``_stdlib_uuid`` 在本模块被引用（供下游 ``from ... import``），
# 显式标注避免 linter 判为未使用。
_ = SafeUUID, _stdlib_uuid
