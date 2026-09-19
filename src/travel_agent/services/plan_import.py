"""行程文件文本抽取：docx / pdf / txt / md → 纯文本。

职责边界：
- 只做格式解析，不含任何业务假设；抽取出的文本由聊天流程交给 LLM
  （intent/compose 节点）结合文档内容完成行程规划；
- 不支持的格式或抽不出可读文本时抛 ImportError_，由路由层转为 4xx。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

_SUPPORTED_SUFFIXES = {".docx", ".pdf", ".txt", ".md"}


class ImportError_(RuntimeError):
    """导入失败（格式不支持 / 内容缺失关键要素）。"""


def extract_text(filename: str, content: bytes) -> str:
    """按扩展名抽取纯文本；不支持的格式抛 ImportError_。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        joined = "、".join(sorted(_SUPPORTED_SUFFIXES))
        raise ImportError_(f"暂不支持 {suffix or '该'} 格式，支持：{joined}")
    if suffix == ".docx":
        return _extract_docx(content)
    if suffix == ".pdf":
        return _extract_pdf(content)
    return content.decode("utf-8", errors="replace")


def _extract_docx(content: bytes) -> str:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover - 依赖裁剪安装时才触发
        raise ImportError_("缺少 python-docx，无法解析 Word 文档") from exc
    try:
        document = docx.Document(BytesIO(content))
    except Exception as exc:
        raise ImportError_(f"Word 文档解析失败：{exc}") from exc
    blocks: list[str] = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    return "\n".join(blocks)


def _extract_pdf(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - 依赖裁剪安装时才触发
        raise ImportError_("缺少 pypdf，无法解析 PDF 文件") from exc
    try:
        reader = PdfReader(BytesIO(content))
    except Exception as exc:
        raise ImportError_(f"PDF 解析失败：{exc}") from exc
    pages: list[str] = []
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(text)
    if not pages:
        raise ImportError_("PDF 中未提取到可读文本（可能是扫描件，请改用文字版）")
    return "\n".join(pages)
