"""按出行人数推断房型建议（纯逻辑，无 IO，可单测）。

行业惯例要点
------------
- 1 位成人：单人间（大床 1.2m）为惯例，双床房通常不划算
- 2 位成人：双床房（Twin）为惯例；情侣出行时大床房更常见——
  因此这里给**主推**与**备选**两种，前端可展示「也可选」
- 3 位：单间标准房通常只允许加床 1 张（3 成人偏挤），
  故主推家庭房/三人房 1 间，并把「1 大床 + 1 加床 + 1 单间」列为备选
- 4 位：家庭房（2 大 2 小）1 间是家庭出行主流；若为 4 成人，
  2 间双人房比拼住更舒适，故备选给出 2 间
- 5 位及以上：⌈n/2⌉ 间双人房是唯一稳妥解；⌈n/3⌉ 家庭房仅在含儿童时考虑

本模块**不查询任何酒店**、**不假设具体酒店有某房型**，
只回答「该开几间、每间大概住几人」。真实库存以酒店实际为准。
"""

from typing import Final

from travel_agent.domain.models import RoomSuggestion, RoomType

#: 每间标准房默认可舒适入住人数（2 大 1 小 / 2 成人）
_OCCUPANCY_DEFAULT: Final = 2
#: 单间标准房加床后的上限（酒店普遍限制 1 张加床）
_OCCUPANCY_WITH_EXTRA_BED: Final = 3
#: 家庭房常见配置（2 大 2 小）
_OCCUPANCY_FAMILY: Final = 4
#: 家庭房含 1 大 2 小 + 加床
_OCCUPANCY_FAMILY_LARGE: Final = 5

#: 房型的中文展示名，供 rationale 直接使用
_ROOM_LABEL: Final[dict[RoomType, str]] = {
    "single": "单人间",
    "double": "大床房",
    "twin": "双床房",
    "triple": "三人房",
    "family": "家庭房",
    "suite": "套房",
}

#: 房型按舒适容纳人数升序
_BY_CAPACITY: Final = sorted(
    _ROOM_LABEL.items(),
    key=lambda kv: {
        "single": 1,
        "double": 2,
        "twin": 2,
        "triple": 3,
        "family": _OCCUPANCY_FAMILY,
        "suite": _OCCUPANCY_FAMILY + 2,
    }[kv[0]],
)


def _ceil_div(total: int, per_room: int) -> int:
    return -(-total // per_room)


def _label(room_type: RoomType) -> str:
    return _ROOM_LABEL[room_type]


def suggest_rooms(travelers: int) -> list[RoomSuggestion]:
    """按出行人数给出一组房型建议，首项为主推。

    Args:
        travelers: 出行总人数（含儿童）。<= 0 时按 1 处理，避免除零/空列表。

    Returns:
        主推 + 备选建议，主推在前。每项都带 rationale 说明依据。
    """
    n = max(1, int(travelers))
    suggestions: list[RoomSuggestion] = []

    if n == 1:
        suggestions.append(
            RoomSuggestion(
                room_type="single",
                occupancy=1,
                rooms=1,
                rationale="1 位成人，建议单人间（大床 1.2m），通常比双床房更实惠",
            )
        )
        suggestions.append(
            RoomSuggestion(
                room_type="double",
                occupancy=1,
                rooms=1,
                rationale="若偏好更宽敞，可升级为大床房 1 间",
            )
        )
        return suggestions

    if n == 2:
        # 2 人：双床与大床两种都常见，双床放主推位并把大床列为备选
        suggestions.append(
            RoomSuggestion(
                room_type="twin",
                occupancy=2,
                rooms=1,
                rationale="2 位成人，建议双床房 1 间（两张 1.2m 单床）",
            )
        )
        suggestions.append(
            RoomSuggestion(
                room_type="double",
                occupancy=2,
                rooms=1,
                rationale="若为情侣出行，可改选大床房 1 间",
            )
        )
        return suggestions

    if n == 3:
        suggestions.append(
            RoomSuggestion(
                room_type="family",
                occupancy=_OCCUPANCY_FAMILY,
                rooms=1,
                rationale="3 人建议家庭房 1 间（2 大 1 小），比加床舒适",
            )
        )
        suggestions.append(
            RoomSuggestion(
                room_type="double",
                occupancy=_OCCUPANCY_WITH_EXTRA_BED,
                rooms=1,
                rationale="预算优先可选大床房加床 1 张（酒店通常限加 1 张）",
            )
        )
        suggestions.append(
            RoomSuggestion(
                room_type="twin",
                occupancy=2,
                rooms=2,
                rationale="若需更大活动空间，可订双床房 2 间",
            )
        )
        return suggestions

    if n == 4:
        suggestions.append(
            RoomSuggestion(
                room_type="family",
                occupancy=_OCCUPANCY_FAMILY,
                rooms=1,
                rationale="4 人建议家庭房 1 间（2 大 2 小），行李与卫浴空间更从容",
            )
        )
        suggestions.append(
            RoomSuggestion(
                room_type="twin",
                occupancy=2,
                rooms=2,
                rationale="若为 4 位成人，2 间双人房比拼住更舒适",
            )
        )
        return suggestions

    # 5 人及以上：双人房是唯一稳妥解；家庭房仅在能装下时给出
    twin_rooms = _ceil_div(n, _OCCUPANCY_DEFAULT)
    suggestions.append(
        RoomSuggestion(
            room_type="twin",
            occupancy=_OCCUPANCY_DEFAULT,
            rooms=twin_rooms,
            rationale=f"{n} 人建议双床房 {twin_rooms} 间（每间 2 人），确保每人有独立床位",
        )
    )
    family_rooms = _ceil_div(n, _OCCUPANCY_FAMILY)
    if family_rooms < twin_rooms:
        suggestions.append(
            RoomSuggestion(
                room_type="family",
                occupancy=_OCCUPANCY_FAMILY,
                rooms=family_rooms,
                rationale=f"若含儿童，可选家庭房 {family_rooms} 间（每间约 2 大 2 小）",
            )
        )
    packed_rooms = _ceil_div(n, _OCCUPANCY_WITH_EXTRA_BED)
    suggestions.append(
        RoomSuggestion(
            room_type="twin",
            occupancy=_OCCUPANCY_WITH_EXTRA_BED,
            rooms=packed_rooms,
            rationale=f"若需压缩房间数，可选加床房 {packed_rooms} 间（每间 3 人）",
        )
    )
    return suggestions


def primary_room_suggestion(travelers: int) -> RoomSuggestion:
    """取主推房型建议（列表首项）。"""
    return suggest_rooms(travelers)[0]


def describe_suggestions(suggestions: list[RoomSuggestion]) -> str:
    """把建议列表拼成一行摘要，用于日志与前端副标题。"""
    if not suggestions:
        return ""
    primary = suggestions[0]
    head = f"{primary.rooms} 间{_label(primary.room_type)}（每间 {primary.occupancy} 人）"
    if len(suggestions) == 1:
        return head
    return f"主推 {head}；备选 " + "，".join(
        f"{s.rooms} 间{_label(s.room_type)}（每间 {s.occupancy} 人）" for s in suggestions[1:]
    )
