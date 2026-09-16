"""Jinja2 Prompt 模板加载（仓库根 ``prompts/`` 目录）。

每个用例一对文件：``<name>.system.jinja2`` 与 ``<name>.user.jinja2``，
系统提示与用户内容严格分离（防注入要求）。
"""

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"

_env = Environment(
    loader=FileSystemLoader(str(_PROMPTS_DIR)),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    autoescape=False,  # noqa: S701 - Prompt 是发给 LLM 的纯文本，不渲染 HTML
)


def render_pair(name: str, **context: Any) -> tuple[str, str]:
    """渲染 (system, user) 一对模板。"""
    system = _env.get_template(f"{name}.system.jinja2").render(**context).strip()
    user = _env.get_template(f"{name}.user.jinja2").render(**context).strip()
    return system, user
