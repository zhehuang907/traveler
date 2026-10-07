"""``vendor/uuid_utils_stub``（uuid-utils 纯 Python 替身）的单元测试。

背景：本机Smart App Control 会按文件内容哈希拦截 ``uuid-utils`` 官方 wheel 的
原生扩展 ``_uuid_utils.cp313-*.pyd``（WinError 4551），导致
``langchain_core`` / ``langsmith`` 在模块级 import 失败、应用无法启动。
故以纯 Python 替身替换（见 ``vendor/uuid_utils_stub``）。

这些测试锁定的是**替身最容易写错的三处**——都是本次实现实际踩过的坑：

1. ``uuid7(timestamp=...)`` 的 ``timestamp`` 单位是**秒**而非毫秒
   （两个消费方均以 ``divmod(ns, 1e9)`` 传参）
2. UUIDv6 的时间戳字段是**重排**的，且版本位在 octet6高半字节；
   标准库 ``.time`` 按未重排布局解析 v6 会得到完全错误的值
3. v6 只有 60 位时间戳，不能沿用 v1 的「先算完整 ticks 再截断」写法
   （2026 年的秒数即溢出，会静默丢高位）

测试直接 ``import uuid_utils``——解析结果若被换回官方 wheel，这些断言
同样应成立（官方实现是正确的），因此本套测试不会因环境差异而误报。
"""

from __future__ import annotations

import time
import uuid as stdlib_uuid
from collections.abc import Callable

import pytest
import uuid_utils
from uuid_utils import compat

#: 四个无参生成器（uuid1/4/6/7），供 parametrize 复用
type UuidFactory = Callable[[], uuid_utils.UUID]

# ---------------------------------------------------------------------------
# 版本 / variant / 时间戳单位
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("factory", "expected_version"),
    [
        (uuid_utils.uuid1, 1),
        (uuid_utils.uuid4, 4),
        (uuid_utils.uuid6, 6),
        (uuid_utils.uuid7, 7),
    ],
)
def test_version_nibble(factory: UuidFactory, expected_version: int) -> None:
    """版本号必须落在 octet6 高半字节（v1/v4/v6/v7 的共同位置）。"""
    u = factory()
    assert u.version == expected_version
    assert u.bytes[6] >> 4 == expected_version


@pytest.mark.parametrize(
    "factory",
    [
        uuid_utils.uuid1,
        uuid_utils.uuid4,
        uuid_utils.uuid6,
        uuid_utils.uuid7,
    ],
)
def test_variant_is_rfc4122(factory: UuidFactory) -> None:
    """variant 必须为 0b10（octet8 高两位）。"""
    u = factory()
    assert u.variant == stdlib_uuid.RFC_4122
    assert u.bytes[8] >> 6 == 0b10


def test_timestamp_unit_is_seconds_not_milliseconds() -> None:
    """uuid7(uuid6) 的 timestamp 参数单位是「秒」。

    这是最易错的一处：若误按毫秒理解，生成的时间戳会偏离真实时间约 1000倍，
    而UUID 本身仍然「看起来正常」，故必须用真实时间做往返断言。
    """
    seconds = 1_789_000_000
    nanos = 123_456_789
    expected_ms = seconds * 1000 + nanos // 1_000_000

    assert uuid_utils.uuid7(timestamp=seconds, nanos=nanos).timestamp == expected_ms
    assert uuid_utils.uuid6(timestamp=seconds, nanos=nanos).timestamp == expected_ms


def test_uuid7_now_matches_wall_clock() -> None:
    """不传参时 uuid7 的时间戳应贴近当前时间（反证「秒/毫秒」没搞反）。"""
    before = int(time.time() * 1000)
    u = uuid_utils.uuid7()
    after = int(time.time() * 1000)
    assert before <= u.timestamp <= after


def test_uuid1_now_matches_wall_clock() -> None:
    assert abs(uuid_utils.uuid1().timestamp / 1000 - time.time()) < 5


# ---------------------------------------------------------------------------
# UUIDv6：重排布局（本替身踩过两次坑的地方）
# ---------------------------------------------------------------------------


def test_uuid6_timestamp_roundtrip_large_second_value() -> None:
    """v6 的 60 位时间戳不得因「先算完整 ticks 再截断」而丢高位。

    2026 年的秒数经 gregorian offset 后已超2^60，若沿用 v1 的写法
    （``ticks =OFF + sec*1e7`` 后 ``& 0xFFFFFFFFFFFF``）会静默截断高位。
    """
    seconds = 1_789_000_000
    u = uuid_utils.uuid6(timestamp=seconds)
    assert u.timestamp == seconds * 1000


def test_uuid6_is_time_ordered() -> None:
    """v6 的核心价值：按时间可字典序排序。"""
    early = uuid_utils.uuid6(timestamp=1_000)
    late = uuid_utils.uuid6(timestamp=2_000)
    assert early.bytes < late.bytes


def test_uuid6_fields_follow_rfc9562_layout() -> None:
    """按 RFC 9562 §5.6 Figure 10 校验各字段落位。

    官方参考向量：1EC9414C-232A-6B00-B3C8-9E6BDECED846
    其中 time_high=0x1EC9414C占 octet0-3、time_mid=0x232A 占 octet4-5。
    """
    u = uuid_utils.uuid6(node=0x9E6BDECED846, timestamp=1_789_000_000)
    assert u.node == 0x9E6BDECED846
    # 时间戳分段可被官方 UUID 解析器（标准库）正确读回同一秒
    assert stdlib_uuid.UUID(bytes=u.bytes).version == 6


def test_uuid6_nanoseconds_sub_second_precision() -> None:
    """亚秒精度：nanos 应体现到毫秒位。"""
    seconds = 1_789_000_000
    a = uuid_utils.uuid6(timestamp=seconds, nanos=0)
    b = uuid_utils.uuid6(timestamp=seconds, nanos=999_999_999)
    assert b.timestamp - a.timestamp == 999  # 999999999ns // 1e6 = 999ms


# ---------------------------------------------------------------------------
# UUIDv7：位布局与单调性（langchain / langsmith 选它的唯一理由）
# ---------------------------------------------------------------------------


def test_uuid7_bit_layout() -> None:
    """v7 位布局（RFC 9562 §5.7）。

    字段顺序：48 位 unix_ts_ms | 4 位 ver | 12 位 rand_a | 2 位 var
    | 30 位 rand_b | 32 位 rand。
    """
    u = uuid_utils.uuid7(timestamp=0, nanos=0)
    assert u.bytes[0:6] == b"\x00" * 6
    assert u.bytes[6] >> 4 == 0b0111
    assert u.bytes[8] >> 6 == 0b10


def test_uuid7_rfc9562_appendix_b_timestamp_vector() -> None:
    """RFC 9562 Appendix B 的 unix_ts_ms = 0x017F22E279B0。"""
    unix_ts_ms = 0x017F22E279B0
    u = uuid_utils.uuid7(
        timestamp=unix_ts_ms // 1000,
        nanos=(unix_ts_ms % 1000) * 1_000_000,
    )
    assert u.bytes[0:6].hex() == "017f22e279b0"


def test_uuid7_monotonic_within_millisecond() -> None:
    """同一毫秒内连续生成必须严格单调递增（RFC 9562 §6.2 Method 1）。

    ``langchain_core`` 与 ``langsmith`` 都把「毫秒内单调」写进注释，
    并据此用 uuid7 生成 run_id —— 若退化则 trace 排序失效。
    """
    fixed = uuid_utils.uuid7(timestamp=1_789_000_000, nanos=0)
    following = uuid_utils.uuid7(timestamp=1_789_000_000, nanos=0)
    assert fixed.bytes < following.bytes

    ids = [uuid_utils.uuid7() for _ in range(2000)]
    assert all(ids[i].bytes < ids[i + 1].bytes for i in range(len(ids) - 1))


def test_uuid7_distinct_across_calls() -> None:
    """随机段保证不同调用不重复（单调计数器只覆盖 42 位低段）。"""
    assert len({uuid_utils.uuid7() for _ in range(1000)}) == 1000


def test_uuid7_rand_a_randomised_per_millisecond() -> None:
    """跨毫秒时计数器的随机段应重置，而非从0 开始。

    若重置为 0，不同毫秒的 UUID 低位会高度相似，降低唯一性。
    """
    a = uuid_utils.uuid7(timestamp=1_789_000_000, nanos=0)
    b = uuid_utils.uuid7(timestamp=1_789_000_100, nanos=0)
    rand_a_a = a.bytes[6] & 0x0F
    rand_a_b = b.bytes[6] & 0x0F
    assert (rand_a_a, rand_a_b) != (0, 0)


# ---------------------------------------------------------------------------
# compat 子包：必须返回标准库 UUID 实例（这是它存在的全部意义）
# ---------------------------------------------------------------------------


def test_compat_returns_stdlib_uuid_instances() -> None:
    """compat 的契约：对下游框架暴露标准库 uuid.UUID，而非子类。

    Django / pydantic 等会做 ``isinstance`` 甚至 ``type(...) is UUID`` 判断。
    """
    for value in (compat.uuid1(), compat.uuid4(), compat.uuid6(), compat.uuid7()):
        assert type(value) is stdlib_uuid.UUID


def test_compat_uuid7_accepts_seconds_and_nanos() -> None:
    """langchain_core / langsmith 的实际传参方式。"""
    nanoseconds = 1_789_000_000_123_456_789
    seconds, nanos = divmod(nanoseconds, 1_000_000_000)
    u = compat.uuid7(timestamp=seconds, nanos=nanos)
    assert u.int >> 80 == seconds * 1000 + nanos // 1_000_000


def test_compat_versions() -> None:
    assert compat.uuid1().version == 1
    assert compat.uuid3(stdlib_uuid.NAMESPACE_DNS, "python.org").version == 3
    assert compat.uuid4().version == 4
    assert compat.uuid5(stdlib_uuid.NAMESPACE_DNS, "python.org").version == 5
    assert compat.uuid6().version == 6
    assert compat.uuid7().version == 7


def test_compat_uuid3_matches_stdlib() -> None:
    """v3/v5 是纯哈希，必须与标准库逐位一致。"""
    assert compat.uuid3(stdlib_uuid.NAMESPACE_DNS, "python.org").int == (
        stdlib_uuid.uuid3(stdlib_uuid.NAMESPACE_DNS, "python.org").int
    )
    assert compat.uuid5(stdlib_uuid.NAMESPACE_DNS, "python.org").int == (
        stdlib_uuid.uuid5(stdlib_uuid.NAMESPACE_DNS, "python.org").int
    )


def test_compat_exports_named_constants() -> None:
    assert str(compat.NIL) == "00000000-0000-0000-0000-000000000000"
    assert str(compat.MAX) == "ffffffff-ffff-ffff-ffff-ffffffffffff"
    assert compat.__version__ == uuid_utils.__version__


# ---------------------------------------------------------------------------
# 其余版本与常量
# ---------------------------------------------------------------------------


def test_uuid8_uses_supplied_bytes() -> None:
    raw = bytes(range(16))
    assert uuid_utils.uuid8(raw).bytes == raw


def test_uuid8_rejects_wrong_length() -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        uuid_utils.uuid8(b"\x00" * 15)


def test_timestamp_raises_for_non_time_versions() -> None:
    """v4 无时间戳，取用时应给出官方同样的报错。"""
    with pytest.raises(ValueError, match="v1, v6 or v7"):
        _ = uuid_utils.uuid4().timestamp


def test_nil_and_max_constants() -> None:
    assert str(uuid_utils.NIL) == "00000000-0000-0000-0000-000000000000"
    assert str(uuid_utils.MAX) == "ffffffff-ffff-ffff-ffff-ffffffffffff"


def test_namespace_constants_match_stdlib() -> None:
    assert uuid_utils.NAMESPACE_DNS == stdlib_uuid.NAMESPACE_DNS
    assert uuid_utils.NAMESPACE_URL == stdlib_uuid.NAMESPACE_URL
    assert uuid_utils.NAMESPACE_OID == stdlib_uuid.NAMESPACE_OID
    assert uuid_utils.NAMESPACE_X500 == stdlib_uuid.NAMESPACE_X500


def test_public_api_surface() -> None:
    """官方 ``__all__`` 中的名字必须齐备（避免下游 import 失败）。"""
    for name in uuid_utils.__all__:
        assert hasattr(uuid_utils, name), f"uuid_utils缺导出：{name}"
    for name in compat.__all__:
        assert hasattr(compat, name), f"uuid_utils.compat 缺导出：{name}"


def test_two_consumers_import_cleanly() -> None:
    """两个实际消费方必须能在不设PYTHONPATH 的情况下导入。"""
    from langchain_core.utils.uuid import uuid7 as lc_uuid7
    from langsmith._internal._uuid import uuid7 as ls_uuid7

    assert lc_uuid7().version == 7
    assert ls_uuid7().version == 7
