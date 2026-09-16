"""日志管线测试：structlog 中继、stdlib 拦截、JSON 落点、异常与级别过滤。"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest
import structlog
from loguru import logger as loguru_logger

from travel_agent.logging_conf import InterceptHandler, configure_logging, get_logger

if TYPE_CHECKING:
    from loguru import Message


@pytest.fixture
def captured() -> Iterator[list[Message]]:
    configure_logging("DEBUG", json_logs=False)
    messages: list[Message] = []
    loguru_logger.add(messages.append, level="DEBUG")
    yield messages
    # 测试体内可能重新 configure（会清空全部 sink），这里直接全量移除，避免 id 失效
    loguru_logger.remove()


def test_structlog_relays_bound_fields(captured: list[Message]) -> None:
    log = get_logger(component="relay-test")
    log.info("规划完成", city="成都", days=4)
    record = captured[-1].record
    assert record["message"] == "规划完成"
    assert record["extra"]["city"] == "成都"
    assert record["extra"]["days"] == 4
    assert record["extra"]["component"] == "relay-test"
    # 调用方定位应跳出 structlog / logging_conf，指向本测试模块
    name = record["name"]
    assert name is not None
    assert "test_logging_conf" in name


def test_stdlib_intercepted(captured: list[Message]) -> None:
    logging.getLogger("unit.demo").warning("标准库日志")
    messages_text = [m.record["message"] for m in captured]
    assert "标准库日志" in messages_text


def test_request_id_contextvar(captured: list[Message]) -> None:
    tokens = structlog.contextvars.bind_contextvars(request_id="req-123")
    try:
        get_logger().info("带请求 id")
    finally:
        structlog.contextvars.reset_contextvars(request_id=tokens["request_id"])
    assert captured[-1].record["extra"]["request_id"] == "req-123"


def test_exception_relayed(captured: list[Message]) -> None:
    try:
        raise RuntimeError("炸了")
    except RuntimeError:
        get_logger().exception("捕获异常")
    exc = captured[-1].record["exception"]
    assert exc is not None
    assert exc.type is RuntimeError


def test_level_filtering(captured: list[Message]) -> None:
    configure_logging("WARNING", json_logs=False)
    get_logger().debug("不该出现")
    assert not any(m.record["message"] == "不该出现" for m in captured)


def test_invalid_level_raises() -> None:
    with pytest.raises(ValueError):
        configure_logging("NOT_A_LEVEL")


def test_intercept_handler_unknown_level(captured: list[Message]) -> None:
    handler = InterceptHandler()
    record = logging.LogRecord("x", 99, __file__, 1, "自定义级别", None, None)
    handler.emit(record)
    assert any(m.record["message"] == "自定义级别" for m in captured)


def test_json_sink(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json_logs=True)
    get_logger(component="json").info("结构化输出", city="重庆")
    err = capsys.readouterr().err
    lines = [line for line in err.splitlines() if "结构化输出" in line]
    assert lines
    payload = json.loads(lines[-1])
    assert payload["level"] == "INFO"
    assert payload["message"] == "结构化输出"
    assert payload["city"] == "重庆"
    assert payload["component"] == "json"
