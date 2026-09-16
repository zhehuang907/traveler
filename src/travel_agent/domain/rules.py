"""行程程序化校验规则（纯函数，禁止 IO）。

validate_plan 节点先用路径工具核实相邻条目通勤，再把结果交给这里：
规则只做判定并返回人类可读的 reflection 文本，不做任何重排。
"""

import re
from collections.abc import Mapping
from datetime import date, datetime, time

from travel_agent.domain.models import DailyWeather
from travel_agent.domain.plan import PlanDay, PlanItem, TripPlan

# 相邻条目间允许的通勤缓冲（分钟）：路径耗时超出「时间空档 + 缓冲」即判不可行
_COMMUTE_SLACK_MIN = 15
_HIGH_TEMP_C = 37.0
_LOW_TEMP_C = 0.0
_RISK_KEYWORDS = ("台风", "暴雨", "暴雪", "大风", "冰雹")
_OPEN_WINDOW_RE = re.compile(r"(\d{1,2}):(\d{2})\s*[-~至]\s*(\d{1,2}):(\d{2})")


def check_day_load(day: PlanDay, commute_minutes: int, max_hours: float) -> str | None:
    """当天停留时长 + 通勤时长是否超过每日上限。"""
    total = day.activity_minutes() + max(0, commute_minutes)
    limit_min = max_hours * 60
    if total > limit_min:
        return (
            f"第 {day.day_index} 天总时长 {total // 60}h{total % 60:02d} "
            f"超出每日上限 {int(max_hours)}h，请精简或分散安排"
        )
    return None


def check_empty_day(day: PlanDay) -> str | None:
    """当天没有任何安排（可能是局部修改后留下的空档）。"""
    if not day.items:
        return f"第 {day.day_index} 天没有任何安排，需要补足或删除该天"
    return None


def check_budget(plan: TripPlan) -> str | None:
    """条目花费合计是否超过预算。"""
    if plan.budget_cny is None:
        return None
    total = plan.total_cost_cny
    if total > plan.budget_cny:
        over = round(total - plan.budget_cny, 2)
        return (
            f"预算合计 ¥{total:g} 超出预算 ¥{plan.budget_cny:g}"
            f"（超 ¥{over:g}），请降级消费或删减项目"
        )
    return None


def check_rainy_indoor(day: PlanDay, weather: DailyWeather | None) -> str | None:
    """高降水概率的一天必须有室内安排或显式的天气调整。"""
    if weather is None or not weather.rainy:
        return None
    has_indoor = any(item.indoor or item.weather_adjusted for item in day.items)
    if has_indoor:
        return None
    return f"{day.date} 降水概率 {weather.precip_prob}%，需安排室内备选（博物馆/商场/茶馆等）"


def parse_open_window(business_hours: str) -> tuple[time, time] | None:
    """从营业时段文本提取首个 ``HH:MM-HH:MM`` 区间，无法识别返回 None。"""
    match = _OPEN_WINDOW_RE.search(business_hours)
    if match is None:
        return None
    open_h, open_m, close_h, close_m = (int(group) for group in match.groups())
    try:
        return time(open_h, open_m), time(close_h, close_m)
    except ValueError:
        return None


def check_visit_window(item: PlanItem, business_hours: str | None) -> str | None:
    """到访时段是否落在营业窗口之外（仅当两边时间都可解析时判定）。"""
    if business_hours is None or item.start_time is None or item.end_time is None:
        return None
    window = parse_open_window(business_hours)
    if window is None:
        return None
    open_time, close_time = window
    if item.start_time < open_time or item.end_time > close_time:
        visit_text = f"{item.start_time:%H:%M}-{item.end_time:%H:%M}"
        window_text = f"{open_time:%H:%M}-{close_time:%H:%M}"
        return f"「{item.title}」到访 {visit_text} 不在营业时段 {window_text} 内"
    return None


def check_commute_gap(route_minutes: int, gap_minutes: int | None) -> str | None:
    """相邻条目间的路径耗时是否超过编排空档。"""
    if gap_minutes is None:
        return None
    if route_minutes > gap_minutes + _COMMUTE_SLACK_MIN:
        return (
            f"相邻安排之间通勤约 {route_minutes} 分钟，但只留了 {gap_minutes} 分钟空档"
            "（含 15 分钟缓冲），时间不可行"
        )
    return None


def gap_minutes(earlier: PlanItem, later: PlanItem) -> int | None:
    """两个条目的结束→开始空档（分钟）；任一时刻缺失则无法计算。"""
    if earlier.end_time is None or later.start_time is None:
        return None
    start = datetime.combine(date.min, later.start_time)
    end = datetime.combine(date.min, earlier.end_time)
    delta = int((start - end).total_seconds() // 60)
    # 允许跨午夜的极小概率编排：负值空档视为不可用而非负数
    return delta if delta >= 0 else None


def extreme_weather_alerts(weather: Mapping[date, DailyWeather]) -> list[str]:
    """高温/低温/极端天气风险提示（气泡，不阻塞行程）。"""
    alerts: list[str] = []
    for day, daily in weather.items():
        if daily.temp_max >= _HIGH_TEMP_C:
            alerts.append(f"{day} 高温 {daily.temp_max:g}℃，注意防暑补水")
        if daily.temp_min <= _LOW_TEMP_C:
            alerts.append(f"{day} 最低 {daily.temp_min:g}℃，注意保暖")
        if any(keyword in daily.condition for keyword in _RISK_KEYWORDS):
            alerts.append(f"{day} 可能出现{daily.condition}，请关注预警并预留弹性")
    return alerts


def evaluate_plan(
    plan: TripPlan,
    weather: Mapping[date, DailyWeather],
    business_hours: Mapping[str, str],
    commute_minutes: Mapping[tuple[str, str], int],
    max_daily_hours: float,
) -> list[str]:
    """对整份行程跑全部程序化校验，返回 reflection 列表（空列表表示通过）。"""
    reflections: list[str] = []
    for day in plan.days:
        reflections.extend(
            _evaluate_day(day, weather, business_hours, commute_minutes, max_daily_hours)
        )
    budget_issue = check_budget(plan)
    if budget_issue:
        reflections.append(budget_issue)
    return reflections


def _evaluate_day(
    day: PlanDay,
    weather: Mapping[date, DailyWeather],
    business_hours: Mapping[str, str],
    commute_minutes: Mapping[tuple[str, str], int],
    max_daily_hours: float,
) -> list[str]:
    """单天校验：空天、降雨、营业时段、通勤、总时长。"""
    issues: list[str] = []
    empty = check_empty_day(day)
    if empty:
        return [empty]
    rainy = check_rainy_indoor(day, weather.get(day.date))
    if rainy:
        issues.append(rainy)
    for item in day.items:
        if item.poi_id:
            issue = check_visit_window(item, business_hours.get(item.poi_id))
            if issue:
                issues.append(issue)
    issues.extend(_evaluate_commutes(day, commute_minutes))
    day_commute = sum(
        minutes for (a, b), minutes in commute_minutes.items() if _pair_in_day(a, b, day)
    )
    load = check_day_load(day, day_commute, max_daily_hours)
    if load:
        issues.append(load)
    return issues


def _pair_in_day(first_id: str, second_id: str, day: PlanDay) -> bool:
    ids = [item.item_id for item in day.items]
    return first_id in ids and second_id in ids


def _evaluate_commutes(day: PlanDay, commute_minutes: Mapping[tuple[str, str], int]) -> list[str]:
    issues: list[str] = []
    for earlier, later in zip(day.items, day.items[1:], strict=False):
        minutes = commute_minutes.get((earlier.item_id, later.item_id))
        if minutes is None:
            continue
        issue = check_commute_gap(minutes, gap_minutes(earlier, later))
        if issue:
            issues.append(f"第 {day.day_index} 天「{earlier.title}」→「{later.title}」：{issue}")
    return issues
