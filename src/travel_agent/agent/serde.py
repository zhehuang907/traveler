"""Checkpointer 的状态序列化器。

langgraph-checkpoint-sqlite 3.x 默认 msgpack serde 不支持本项目的
Pydantic v2 领域模型（嵌套 HttpUrl 等会直接 TypeError），因此使用带类型
信封的 JSON 序列化：

- Pydantic v2 模型：``model_dump(mode="json")`` + 类型标签，读回时
  ``model_validate`` 还原；
- date/time/datetime：ISO 字符串标签；
- 反序列化只允许白名单模块（本项目与 langchain 消息），拒绝任意类型导入；
- 其余值必须是 JSON 原生类型，禁止 pickle（checkpoint 虽为本地单机库，
  仍保持最小反序列化攻击面）。
"""

import importlib
import json
from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel

_TYPE_KEY = "__ta_type__"
_DATA_KEY = "__ta_data__"
_KIND_KEY = "__ta_kind__"
_VALUE_KEY = "__ta_value__"

# 仅允许还原这两个模块树下的类型
_ALLOWED_PREFIXES = ("travel_agent.", "langchain_core.messages")


class TypedJSONSerializer:
    """满足 langgraph ``SerializerProtocol`` 的 JSON 序列化器。"""

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        payload = json.dumps(obj, default=_encode_default, ensure_ascii=False)
        return "ta-json", payload.encode("utf-8")

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        type_tag, raw = data
        if type_tag == "null":
            return None
        if type_tag != "ta-json":
            raise NotImplementedError(f"不支持的 checkpoint 数据类型: {type_tag}")
        return json.loads(raw.decode("utf-8"), object_hook=_decode_object)


def _encode_default(obj: Any) -> dict[str, Any]:
    if isinstance(obj, BaseModel):
        dotted = f"{type(obj).__module__}.{type(obj).__name__}"
        # 排除计算字段：回读走 model_validate（extra=forbid），计算字段不可作为输入
        data = obj.model_dump(mode="json", exclude_computed_fields=True)
        return {_TYPE_KEY: dotted, _DATA_KEY: data}
    if isinstance(obj, datetime):
        return {_KIND_KEY: "datetime", _VALUE_KEY: obj.isoformat()}
    if isinstance(obj, date):
        return {_KIND_KEY: "date", _VALUE_KEY: obj.isoformat()}
    if isinstance(obj, time):
        return {_KIND_KEY: "time", _VALUE_KEY: obj.isoformat()}
    raise TypeError(f"状态中存在不可 JSON 序列化的类型: {type(obj)!r}")


def _decode_object(value: dict[str, Any]) -> Any:
    tagged = value.get(_TYPE_KEY)
    if isinstance(tagged, str):
        return _decode_model(tagged, value[_DATA_KEY])
    kind = value.get(_KIND_KEY)
    if isinstance(kind, str):
        return _decode_primitive(kind, str(value[_VALUE_KEY]))
    return value


def _decode_model(dotted: str, data: Any) -> Any:
    if not any(dotted.startswith(prefix) for prefix in _ALLOWED_PREFIXES):
        raise ValueError(f"拒绝反序列化非白名单类型: {dotted}")
    module_name, class_name = dotted.rsplit(".", 1)
    cls = getattr(importlib.import_module(module_name), class_name)
    if not issubclass(cls, BaseModel):
        raise ValueError(f"标签类型不是 Pydantic 模型: {dotted}")
    return cls.model_validate(data)


def _decode_primitive(kind: str, raw: str) -> Any:
    if kind == "date":
        return date.fromisoformat(raw)
    if kind == "datetime":
        return datetime.fromisoformat(raw)
    if kind == "time":
        return time.fromisoformat(raw)
    raise NotImplementedError(f"不支持的时间标签: {kind}")
