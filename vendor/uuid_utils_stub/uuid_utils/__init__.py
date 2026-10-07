"""纯 Python 实现的 uuid-utils 替身。

为什么需要它
------------
本机（Windows + SmartApp Control 应用程序控制策略）会拦截 ``uuid-utils``
官方 wheel 中的原生扩展 ``_uuid_utils.cp313-*.pyd``：

    ImportError: DLL load failed while importing _uuid_utils:
    应用程序控制策略已阻止此文件。（WinError 4551）

该策略按**文件内容哈希**逐个判定（实测：把同一文件复制到新路径仍被拦），
并非禁止全部 ``.pyd``——同环境下的 ``pydantic_core``、``cryptography._rust``、
``greenlet``、``lxml`` 均可正常加载。``uuid-utils`` 官方只提供maturin 编译型
wheel，**无纯 Python 发行版**，且本项目需要 Python 3.13，因此**降级版本无法规避**。

影响面：``langchain_core.utils.uuid`` 与 ``langsmith._internal._uuid`` 都在
**模块级**执行 ``from uuid_utils.compat import uuid7``，若该包不可导入，
``ChatOpenAI`` 会连带崩溃、整个应用无法启动。

本项目实际用到什么
------------------
全仓库检索（``grep -rn uuid_utils src/ tests/``）确认业务代码**零直接引用**；
两个消费方都只用 ``uuid7``：

* ``langchain_core/utils/uuid.py`` —``uuid7(nanoseconds)``，用于 trace run_id
* ``langsmith/_internal/_uuid.py`` — ``uuid7(nanoseconds)`` 与
  ``uuid7_deterministic()``（后者自行拼字节，不经本模块）

因此本替身**完整实现**官方 API（含 uuid1/3/4/5/6/7/8 与 UUID 类），
以保证任何未预期的导入路径也不会崩；但正确性重点在 uuid7。

时间戳单位（易错点）
--------------------
``uuid7(timestamp=..., nanos=...)`` 的 ``timestamp`` 单位是**秒**，
``nanos`` 是**秒内的亚秒纳秒数**。证据：两个消费方都这样传参：

    seconds, nanos = divmod(nanoseconds, 1_000_000_000)
    _uuid_utils_uuid7(timestamp=seconds, nanos=nanos)

且从被拦的 pyd 中提取到的内置签名串为
``'uuid7(timestamp=None, nanos=None)'`` 与 ``'uuid6timestampnanos'``。
官方 pyd 内 ``__version__`` 为 ``0.17.1``，其底层``uuid`` crate 1.26.0
的 ``Timestamp::from_unix(seconds, subsec_nanos)`` 亦为「秒 + 亚秒纳秒」。

单调性说明
----------
官方 ``langchain_core`` 与 ``langsmith`` 的注释都强调「UUIDv7 objects feature
monotonicity within a millisecond」——这是它们选uuid7 的唯一理由（run_id
需要按时间有序）。本替身用 RFC 9562 §6.2 Method 1（42 位单调计数器）
如实实现该保证：同一毫秒内连续调用时，后者的 ``rand_a`` 段严格递增，
因此 ``UUID.bytes`` 的字典序与生成顺序一致。
"""

from __future__ import annotations

import os
import threading
import time
import uuid as _stdlib_uuid
from typing import Final
from uuid import SafeUUID

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
    "SafeUUID",
    "__version__",
    "getnode",
    "reseed_rng",
    "uuid1",
    "uuid3",
    "uuid4",
    "uuid5",
    "uuid6",
    "uuid7",
    "uuid8",
]

# 与官方 0.17.1 保持一致：部分下游会做版本判断
__version__: Final = "0.17.1"

# 官方导出全部直接复用标准库的取值（这些常量本来就是 RFC 4122 固定值，
# 不含任何实现相关逻辑），保证与官方逐位相同。
NAMESPACE_DNS: Final = _stdlib_uuid.NAMESPACE_DNS
NAMESPACE_OID: Final = _stdlib_uuid.NAMESPACE_OID
NAMESPACE_URL: Final = _stdlib_uuid.NAMESPACE_URL
NAMESPACE_X500: Final = _stdlib_uuid.NAMESPACE_X500
RESERVED_NCS: Final = _stdlib_uuid.RESERVED_NCS
RFC_4122: Final = _stdlib_uuid.RFC_4122
RESERVED_MICROSOFT: Final = _stdlib_uuid.RESERVED_MICROSOFT
RESERVED_FUTURE: Final = _stdlib_uuid.RESERVED_FUTURE

#: 128 位全 1，即 ``ffffffff-ffff-ffff-ffff-ffffffffffff``
MAX: Final = _stdlib_uuid.UUID(int=(1 << 128) - 1)

#: 全 0 UUID
NIL: Final = _stdlib_uuid.UUID(int=0)

_NANOS_PER_SECOND: Final = 1_000_000_000
_NANOS_PER_MILLI: Final = 1_000_000

# UUID v1/v6 使用「自 1582-10-15 起的 100ns tick 数」，与 Unix epoch 的差值。
# 取自 RFC 4122 §4.1.5 / RFC 9562 Appendix A。
_GREGORIAN_OFFSET: Final = 0x01B21DD213814000


class UUID(_stdlib_uuid.UUID):
    """兼容 ``uuid_utils.UUID`` 的 UUID 类型。

    官方实现是独立的 Rust 类（非标准库子类），本替身直接继承标准库
    ``UUID``：标准库类已经覆盖 ``bytes`` / ``bytes_le`` / ``fields`` /
    ``hex`` / ``int`` / ``urn`` / ``variant`` / ``version`` / ``is_safe``
    等全部属性与方法，继承可避免重复实现带来的细微行为差异。

    唯一新增的是 ``timestamp`` 属性（仅对 v1/v6/v7 有意义）。
    """

    __slots__ = ()

    @property
    def timestamp(self) -> int:
        """返回「Unix epoch 起的毫秒数」。

        仅 v1 / v6 / v7 有意义，其他版本按官方行为抛 ``ValueError``。
        官方 pyd 内的对应报错串为
        ``'UUID version should be one of (v1, v6 or v7).'``

        三者的解析方式不同，不能统一走标准库的 ``.time``：

        * v7：最高 48 位即毫秒时间戳，直接右移 80 位
        * v1：标准库 ``.time`` 已是「自 1582-10-15 起的 100ns tick」，减偏移再除
        * v6：时间戳字段被**重排**过（高 32 位在 octet0-3），标准库 ``.time``
          会按未重排的布局解析，结果完全错误，必须按 RFC 9562 §5.6 自行拼回
        """
        version = self.version
        if version == 7:
            return self.int >> 80
        if version == 1:
            # 标准库 .time 是「自 1582-10-15 起的 100ns tick 数」
            return (self.time - _GREGORIAN_OFFSET) // 10_000
        if version == 6:
            b = self.bytes
            unix_ticks = (
                (int.from_bytes(b[0:4], "big") << 28)  # time_high(32)
                | (int.from_bytes(b[4:6], "big") << 12)  # time_mid(16)
                | (int.from_bytes(b[6:8], "big") & 0x0FFF)  # time_low(12)
            )
            return unix_ticks // 10_000
        raise ValueError("UUID version should be one of (v1, v6 or v7).")


def getnode() -> int:
    """返回本机节点标识（48 位整数），与官方同义。"""
    return _stdlib_uuid.getnode()


def reseed_rng() -> None:
    """重新播种随机源。

    官方注册了 ``os.register_at_fork(after_in_child=reseed_rng)``，防止 fork
    后父子进程生成相同 UUID。本替身用 ``os.urandom``（内核 CSPRNG，fork 后
    父子各自独立取随机数）故天然无此问题，此处保留仅为 API 兼容。
    """
    return None


def uuid1(node: int | None = None, clock_seq: int | None = None) -> UUID:
    """v1：基于节点 ID + 时钟序列 + 时间的 UUID。"""
    return UUID(int=_stdlib_uuid.uuid1(node=node, clock_seq=clock_seq).int)


def uuid3(namespace: _stdlib_uuid.UUID, name: str | bytes) -> UUID:
    """v3：命名空间 + 名称的 MD5 哈希。"""
    return UUID(int=_stdlib_uuid.uuid3(namespace, name).int)


def uuid4() -> UUID:
    """v4：随机 UUID。"""
    return UUID(int=int.from_bytes(os.urandom(16), "big"), version=4)


def uuid5(namespace: _stdlib_uuid.UUID, name: str | bytes) -> UUID:
    """v5：命名空间 + 名称的 SHA-1 哈希。"""
    return UUID(int=_stdlib_uuid.uuid5(namespace, name).int)


def uuid6(node: int | None = None, timestamp: int | None = None, nanos: int | None = None) -> UUID:
    """v6：与 v1 同源但**按时间可字典序排序**的 UUID（RFC 9562 §5.6）。

    与 v1 的差别仅在时间戳字段的字节序被重排，使整体字典序等于时间序。

    ``timestamp`` 单位为**秒**，``nanos`` 为秒内亚秒纳秒数。
    """
    if timestamp is None:
        total_nanos = time.time_ns()
        seconds, subsec_nanos = divmod(total_nanos, _NANOS_PER_SECOND)
    else:
        seconds = int(timestamp)
        subsec_nanos = int(nanos or 0)

    # v1 可以先算完整 ticks（60 位字段溢出即回绕，这是 v1 的既定行为），
    # 但 v6 不同：v6 的时间戳字段是**重排**的位段，没有「整体截断」这一步，
    # 必须直接把 60 位时间戳切成 32/16/12 三段写入。若沿用 v1 的
    # ``ticks =OFF + sec*1e7`` 再 ``& 0xFFFFFFFFFFFF``，当秒数较大时
    # ticks 会超过 2^60，高位被静默截掉（实测 2026 年即溢出），
    # 导致 timestamp 属性读回一个完全错误的值。
    # 正确做法：先减去 1582 偏移，得到「自 epoch 起的 100ns tick」，
    # 再按 v6 位段切分——这样 12 位低段永远装得下。
    unix_ticks = seconds * 10_000_000 + subsec_nanos // 100
    node_id = _stdlib_uuid.getnode() if node is None else node
    # v6 复用 v1 的 clock_seq 语义：随机 14 位。官方未暴露该参数，
    # 故此处自行生成（v6 的实际用途是时间排序，重复概率同 v1 的随机 clock_seq）。
    clock_seq = int.from_bytes(os.urandom(2), "big") & 0x3FFF

    # v6 与 v1 的唯一差别：把时间戳的字节序**重排**为高位在前，
    # 使整体字典序等于时间序。位布局（RFC 9562 §5.6 Figure 10）：
    #   octet0-3 = time_high = 时间戳高 32 位
    #   octet4-5 = time_mid  = 时间戳中间 16 位
    #   octet6   = ver(4)=0b0110 | time_low 高 4 位
    #   octet7   = time_low 低 8 位
    #   octet8   = var(2)=0b10 | clock_seq 高 6 位
    #   octet9   = clock_seq 低 8 位
    #   octet10-15 = node(48)
    #
    # 版本位在 octet6 的高半字节（与 v1/v4/v7 同位置）—— 这��踩过两次坑：
    # 第一次误把 ver 写进 octet1，第二次沿用 v1 的 ``ticks`` 再截断导致高位丢失。
    b = bytearray(16)
    b[0:4] = ((unix_ticks >> 28) & 0xFFFFFFFF).to_bytes(4, "big")  # 高 32 位
    b[4:6] = ((unix_ticks >> 12) & 0xFFFF).to_bytes(2, "big")  # 中间 16 位
    b[6] = 0x60 | ((unix_ticks >> 8) & 0x0F)  # ver + time_low 高 4 位
    b[7] = unix_ticks & 0xFF  # time_low 低 8 位
    b[8] = 0x80 | ((clock_seq >> 8) & 0x3F)
    b[9] = clock_seq & 0xFF
    b[10:16] = (node_id & 0xFFFFFFFFFFFF).to_bytes(6, "big")
    return UUID(bytes=bytes(b))


# ---------------------------------------------------------------------------
# uuid7 状态：实现 RFC 9562 §6.2 Method 1 的42 位单调计数器
# ---------------------------------------------------------------------------

_COUNTER_MAX: Final = (1 << 42) - 1
_state_lock = threading.Lock()
_last_ms: int = -1
_counter: int = 0
_counter_rand: int = 0


def _next_counter(ms: int) -> int:
    """在锁内推进单调计数器，返回本毫秒使用的 counter 值。

    RFC 9562 §6.2 Method 1：同一毫秒内递增；跨毫秒时重置为随机值
    （而非 0），以免不同毫秒的 UUID 低位相同。
    """
    global _last_ms, _counter, _counter_rand
    with _state_lock:
        if ms != _last_ms:
            _last_ms = ms
            _counter_rand = int.from_bytes(os.urandom(8), "big") >> (64 - 42)
            _counter = _counter_rand
        elif _counter < _COUNTER_MAX:
            _counter += 1
        else:
            # 计数器溢出（同一毫秒内生成超过 2^42 个，实践中不可能）：
            # 按 RFC 建议把毫秒值 +1 并重置随机段
            _last_ms = ms + 1
            _counter_rand = int.from_bytes(os.urandom(8), "big") >> (64 - 42)
            _counter = _counter_rand
        return _counter


def _uuid7_int(timestamp: int | None = None, nanos: int | None = None) -> int:
    """生成 UUIDv7 的 128 位整数值。

    ``timestamp`` 单位为秒，``nanos`` 为秒内亚秒纳秒数；两者皆 ``None`` 时
    取当前时间。
    """
    if timestamp is None:
        total_nanos = time.time_ns()
        seconds, subsec_nanos = divmod(total_nanos, _NANOS_PER_SECOND)
    else:
        seconds = int(timestamp)
        subsec_nanos = int(nanos or 0)

    unix_ts_ms = seconds * 1000 + subsec_nanos // _NANOS_PER_MILLI

    counter = _next_counter(unix_ts_ms)

    # 位布局（RFC 9562 §5.7）：
    #   unix_ts_ms(48) | ver(4)=7 | rand_a(12) | var(2)=0b10 | rand_b(30) | rand(32)
    # 其中 rand_a 的高6 位 + rand_b 组成 42 位 counter（Method 1）
    rand_a = (counter >> 30) & 0xFFF
    rand_b_low = counter & 0x3FFFFFFF
    rand32 = int.from_bytes(os.urandom(4), "big")

    value = unix_ts_ms << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b_low << 32
    value |= rand32
    return value


def uuid7(timestamp: int | None = None, nanos: int | None = None) -> UUID:
    """v7：Unix 时间戳有序的 UUID（RFC 9562 §5.7）。

    ``timestamp`` 单位为**秒**，``nanos`` 为秒内亚秒纳秒数。
    同一毫秒内连续调用保证严格单调递增。
    """
    return UUID(int=_uuid7_int(timestamp, nanos))


def uuid8(bytes: bytes) -> UUID:
    """v8：完全由调用方指定的 16 字节构造 UUID（RFC 9562 §5.8）。"""
    raw = bytes
    if len(raw) != 16:
        raise ValueError(f"uuid8 expects exactly 16 bytes, got {len(raw)}")
    return UUID(bytes=raw)


def _uuid4_int() -> int:
    """返回随机 UUID 的整数值（官方内部 API，供 ``compat`` 使用）。

    官方原实现返回的是**已置好版本号与 variant 的**整数值（由 pyd 直接
    产出，用户不可见），因此这里同样要置位，否则 ``compat.uuid4()`` 返回的
    UUID 会没有版本号。
    """
    b = bytearray(os.urandom(16))
    b[6] = 0x40 | (b[6] & 0x0F)
    b[8] = 0x80 | (b[8] & 0x3F)
    return int.from_bytes(bytes(b), "big")


if hasattr(os, "fork"):
    # 与官方一致：fork 后子进程重新播种。Windows 无 fork，此分支不生效。
    os.register_at_fork(after_in_child=reseed_rng)
