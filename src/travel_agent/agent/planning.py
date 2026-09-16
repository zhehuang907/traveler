"""compose/revise 节点共用的确定性辅助：目录构建与 Prompt 上下文格式化。

纯文本/纯数据加工，不含 LLM 与 HTTP 调用。
"""

from travel_agent.agent.state import CandidateGroups
from travel_agent.domain.models import DailyWeather, SearchResult
from travel_agent.domain.plan import TripPlan
from travel_agent.domain.plan_builder import CandidateCatalog

_CATEGORY_LABEL = {"attraction": "景点", "restaurant": "餐饮", "hotel": "住宿"}
_MAX_WEB_RESULTS = 8


def build_catalog(groups: CandidateGroups) -> CandidateCatalog:
    """从状态候选分组构建连续编号目录（景点→餐饮→住宿）。"""
    return CandidateCatalog.from_groups(
        groups.get("attractions", []),
        groups.get("restaurants", []),
        groups.get("hotels", []),
    )


def format_catalog(catalog: CandidateCatalog) -> str:
    """候选目录的人类可读文本（编号对 LLM 可见）。"""
    lines: list[str] = []
    for entry in catalog.entries:
        poi = entry.poi
        label = _CATEGORY_LABEL.get(entry.category, entry.category)
        parts = [f"#{entry.ref}", f"[{label}]", poi.name]
        if poi.rating is not None:
            parts.append(f"评分{poi.rating:g}")
        if poi.business_hours:
            parts.append(f"营业:{poi.business_hours}")
        if poi.cost_hint:
            parts.append(f"费用:{poi.cost_hint}")
        if poi.address:
            parts.append(f"地址:{poi.address}")
        lines.append("｜".join(parts))
    return (
        "\n".join(lines)
        if lines
        else "（无候选：工具检索全部失败，只允许输出自由活动/交通/备注类条目）"
    )


def format_weather(weather: dict[str, DailyWeather]) -> str:
    lines: list[str] = []
    for day, daily in weather.items():
        source = "（历史气候参考）" if daily.climate else "（预报）"
        precip = f"降水概率{daily.precip_prob}%" if daily.precip_prob is not None else "降水未知"
        lines.append(
            f"{day}{source}：{daily.condition}，{daily.temp_min:g}~{daily.temp_max:g}℃，{precip}"
        )
    return "\n".join(lines) if lines else "（天气不可用：请按季节常识做弹性安排并提示用户）"


def format_web(results: list[SearchResult]) -> str:
    lines = [
        f"- {item.title}：{item.snippet}" for item in results[:_MAX_WEB_RESULTS] if item.snippet
    ]
    return "\n".join(lines)


def format_plan_with_refs(plan: TripPlan, catalog: CandidateCatalog) -> str:
    """当前行程文本：事实条目反查候选编号，供修订提示引用。"""
    ref_by_poi = {entry.poi.poi_id: entry.ref for entry in catalog.entries}
    lines: list[str] = []
    for day in plan.days:
        lines.append(f"第{day.day_index}天 {day.date}")
        for item in day.items:
            ref = ref_by_poi.get(item.poi_id or "")
            ref_text = f"[候选#{ref}]" if ref else f"[{item.category}]"
            clock = (
                f"{item.start_time:%H:%M}-{item.end_time:%H:%M}" if item.start_time else "时间待定"
            )
            lines.append(f"  {item.item_id} {clock} {ref_text} {item.title}")
    return "\n".join(lines)
