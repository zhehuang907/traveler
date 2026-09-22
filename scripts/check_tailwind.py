"""校验 Tailwind 静态 CSS 是否覆盖模板/JS 全部 class token。"""

import pathlib
import re

root = pathlib.Path("src/travel_agent")
css = (root / "static/css/tailwind.css").read_text(encoding="utf-8")

# 提取 CSS 中定义的类名（含转义），还原转义序列
raw_defs = re.findall(r"\.((?:[a-zA-Z0-9_\\/.\[\]%:-]+?))(?=[,{])", css)
defs = [d.replace("\\", "") for d in raw_defs]
# 手动核对项（任意透明度写法在 CSS 里被转义，正则可能漏）
manual = {"border-black/[8%]", "max-w-[85%]", "pt-[8vh]", "text-ink-400", "text-ink-600"}
defined = set(defs) | manual


def _is_defined(token: str) -> bool:
    if token in defined:
        return True
    # 变体类在 CSS 里带伪类/组选择器后缀，如 .hover\:text-ink-600:hover
    return any(d == token or d.startswith(token + ":") for d in defs)


tokens: set[str] = set()


def _collect(text: str) -> None:
    # class="a b c"
    for m in re.findall(r'class="([^"]+)"', text):
        tokens.update(m.split())
    # 引号包裹的类串（含三元 :class 里的字面量）
    for m in re.findall(r"'([a-zA-Z0-9_\\/.\[\]%:-]+(?: [a-zA-Z0-9_\\/.\[\]%:-]+)+)'", text):
        tokens.update(m.split())
    for m in re.findall(r'"([a-zA-Z0-9_\\/.\[\]%:-]+(?: [a-zA-Z0-9_\\/.\[\]%:-]+)+)"', text):
        tokens.update(m.split())


for p in (root / "templates").glob("*.html"):
    _collect(p.read_text(encoding="utf-8"))
for p in (root / "static/js").glob("*.js"):
    _collect(p.read_text(encoding="utf-8"))

missing = sorted(t for t in tokens if not _is_defined(t))
print(f"tokens={len(tokens)} defined={len(defined)}")
print("MISSING:", missing if missing else "NONE")
