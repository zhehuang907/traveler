"""日志系统：loguru 作为唯一输出落点，structlog 承载结构化字段。

- 开发环境（APP_ENV=dev）：彩色可读文本。
- 生产环境（APP_ENV=prod 或 LOG_JSON=true）：单行 JSON，便于采集。
- 标准库 logging（uvicorn / sqlalchemy / fastapi）经 InterceptHandler 汇入同一管线。
- 安全：diagnose 恒为 False，绝不把局部变量（可能含密钥）写进异常日志。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
import types
from typing import TYPE_CHECKING, Any, cast

import structlog
from loguru import logger as loguru_logger
from structlog.typing import EventDict, WrappedLogger

if TYPE_CHECKING:
    # Message/Record 仅存在于 loguru 的类型存根中，运行时不导出
    from loguru import Message, Record

__all__ = ["InterceptHandler", "configure_logging", "get_logger"]

_TEXT_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level> {extra[_detail]}\n{exception}"
)


class InterceptHandler(logging.Handler):
    """把标准库 logging 记录转发给 loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame: types.FrameType | None = logging.currentframe()
        depth = 2
        while frame is not None and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        loguru_logger.opt(depth=depth, exception=record.exc_info).log(
            level, "{}", record.getMessage()
        )


def _caller_depth() -> int:
    """计算 loguru 的 depth，使日志记录定位到 structlog/本模块之外的真实调用方。

    loguru 在 Core._log 中以 ``get_frame(depth + 2)`` 取帧，index 2 恰好是
    ``.log()`` 的调用点（即本模块的 _relay）。因此 depth 等于从 _relay 向上
    到第一个外部帧的 f_back 跳数。路径统一 realpath + normcase，
    规避 Windows 盘符大小写/符号链接差异。
    """
    this_file = os.path.normcase(os.path.realpath(__file__))
    frame: types.FrameType | None = sys._getframe(1)  # 调用者：_relay_to_loguru
    depth = 0
    while frame is not None:
        frame = frame.f_back
        depth += 1
        if frame is None:
            break
        filename = os.path.normcase(os.path.realpath(frame.f_code.co_filename))
        if filename != this_file and "structlog" not in filename:
            return depth
    return depth


def _normalize_exc_info(
    exc_info: object,
) -> bool | tuple[type[BaseException], BaseException, types.TracebackType | None]:
    """对齐 loguru ``opt(exception=...)`` 与 structlog 的 exc_info 语义。"""
    if isinstance(exc_info, tuple) and len(exc_info) == 3:
        return cast(
            "tuple[type[BaseException], BaseException, types.TracebackType | None]",
            exc_info,
        )
    return bool(exc_info)


def _relay_to_loguru(_logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """structlog 末端处理器：把结构化事件转投 loguru 并终止 structlog 管线。"""
    event = str(event_dict.pop("event", ""))
    event_dict.pop("level", None)
    exc_info = event_dict.pop("exc_info", None)
    level = "ERROR" if method_name == "exception" else method_name.upper()
    loguru_logger.opt(
        depth=_caller_depth(),
        exception=_normalize_exc_info(exc_info),
    ).bind(**cast("dict[str, Any]", event_dict)).log(level, "{}", event)
    raise structlog.DropEvent


def _detail_patcher(record: Record) -> None:
    """文本模式下把 extra 键值追加到行尾。"""
    extra = record["extra"]
    detail = " ".join(f"{key}={value}" for key, value in extra.items() if key != "_detail")
    extra["_detail"] = f" {detail}" if detail else ""


def _json_sink(message: Message) -> None:
    """生产 JSON 落点：一行一个 JSON 对象。"""
    record = message.record
    payload: dict[str, Any] = {
        "time": record["time"].isoformat(),
        "level": record["level"].name,
        "logger": record["name"],
        "message": record["message"],
    }
    payload.update(record["extra"])
    exc = record["exception"]
    if exc is not None:
        payload["exception"] = "".join(
            traceback.format_exception(exc.type, exc.value, exc.traceback)
        )
    sys.stderr.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _configure_stdlib() -> None:
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "fastapi", "sqlalchemy.engine", "httpx", "httpcore"):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers = [InterceptHandler()]
        lib_logger.propagate = False
    # 访问日志统一由 RequestID 中间件产出，避免重复
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    """配置整条日志管线。重复调用安全。"""
    normalized = level.upper()
    level_no = logging.getLevelName(normalized)
    if not isinstance(level_no, int):
        raise ValueError(f"非法日志级别: {level!r}")

    loguru_logger.remove()
    loguru_logger.configure(patcher=_detail_patcher)
    if json_logs:
        loguru_logger.add(_json_sink, level=normalized, backtrace=True, diagnose=False)
    else:
        loguru_logger.add(
            sys.stderr,
            level=normalized,
            format=_TEXT_FORMAT,
            colorize=True,
            backtrace=True,
            diagnose=False,
        )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", key="ts"),
            _relay_to_loguru,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_no),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configure_stdlib()


def get_logger(**initial_values: Any) -> structlog.stdlib.BoundLogger:
    """获取带初始绑定字段的结构化 logger。"""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(**initial_values))
