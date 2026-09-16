"""智能旅游规划 Agent。

分层结构：
    api/       FastAPI 接口层（参数校验与转发，不写业务逻辑）
    agent/     LangGraph 编排层
    services/  外部能力封装（搜索 / 天气 / 地图 / 缓存 / PDF）
    domain/    纯业务模型与规则（禁止 IO，禁止依赖 services/agent）
    db/        SQLAlchemy 持久化
"""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("travel-agent")
except PackageNotFoundError:  # pragma: no cover - 未安装（直接读源码）时的兜底
    __version__ = "0.0.0+local"
