"""PDF 导出：Jinja2 渲染打印版 HTML → Playwright 无头 Chromium 转 PDF。

Playwright 属可选依赖（``uv sync --extra pdf``），未安装/浏览器缺失时抛出
``PdfUnavailable``，由路由降级为 503 提示，不阻塞主流程。
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from travel_agent.domain.plan import TripPlan

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_PDF_TEMPLATE = "plan_print.html"


class PdfUnavailable(Exception):
    """PDF 能力不可用（playwright 未安装或 Chromium 缺失）。"""


def _render_print_html(plan: TripPlan, plan_version: int | None = None) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=True,
        undefined=StrictUndefined,
    )
    template = env.get_template(_PDF_TEMPLATE)
    return template.render(plan=plan, plan_version=plan_version)


async def render_plan_pdf(plan: TripPlan, plan_version: int | None = None) -> bytes:
    """渲染行程打印版并导出 A4 PDF 字节流。"""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - 依赖缺失分支
        raise PdfUnavailable("PDF 导出组件未安装，请先执行 uv sync --extra pdf") from exc

    html = _render_print_html(plan, plan_version)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                await page.set_content(html, wait_until="load")
                pdf = await page.pdf(format="A4", print_background=True)
            finally:
                await browser.close()
    except Exception as exc:  # pragma: no cover - 环境相关
        raise PdfUnavailable(f"PDF 导出失败：{exc}") from exc
    # playwright 无 py.typed，page.pdf() 类型为 Any；Playwright 保证返回 PDF 字节流
    return bytes(pdf)
