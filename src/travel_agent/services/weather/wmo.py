"""WMO 天气代码到中文描述的映射（Open-Meteo 使用 WMO code）。"""

WMO_CONDITIONS: dict[int, str] = {
    0: "晴",
    1: "大部晴朗",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "冻雾",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴冰雹",
    99: "强雷暴伴冰雹",
}


def describe_wmo(code: object) -> str:
    """未知代码兜底为「未知」，绝不抛异常阻断行程生成。"""
    if not isinstance(code, (int, float, str, bytes)):
        return "未知"
    try:
        return WMO_CONDITIONS.get(int(code), "未知")
    except ValueError:
        return "未知"
