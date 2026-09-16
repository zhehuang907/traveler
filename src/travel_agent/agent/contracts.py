"""Agent 内部 LLM 结构化契约（不属于领域内核：它们服务于编排）。"""

from pydantic import Field

from travel_agent.agent.state import Intent
from travel_agent.domain.brief import TravelBrief
from travel_agent.domain.models import DomainModel


class IntentResult(DomainModel):
    """parse_intent 节点的结构化输出：意图 + 合并后的槽位。"""

    intent: Intent
    brief: TravelBrief
    # modify_plan 命中的天/项描述（阶段三识别，阶段四 Patch 消费）
    target_scope: list[str] = Field(default_factory=list)
    # ask_info/chitchat 时模型可附一句应答素材，最终文案仍由 respond 生成
    reply_hint: str = ""
