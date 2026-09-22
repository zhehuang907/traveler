# syntax=docker/dockerfile:1
# 多阶段构建：builder 用 uv 装依赖，runtime 只保留虚拟环境与源码

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

# ---------- builder ----------
FROM base AS builder
COPY pyproject.toml uv.lock ./
COPY src ./src
# --frozen 严格以 lockfile 安装；--no-dev 排除 ruff/mypy/pytest
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---------- runtime ----------
FROM base AS runtime
ARG INSTALL_CHROMIUM=0
COPY --from=builder /app/.venv /app/.venv
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src ./src
# 迁移脚本：migrate 服务在容器内执行 alembic upgrade head 需要
COPY migrations ./migrations

# PDF 导出可选：--build-arg INSTALL_CHROMIUM=1
RUN if [ "$INSTALL_CHROMIUM" = "1" ]; then \
        uv sync --frozen --no-dev --extra pdf \
        && uv run playwright install --with-deps chromium ; \
    else true ; fi

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

ENV PATH="/app/.venv/bin:$PATH" \
    HOST=0.0.0.0 \
    PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status==200 else 1)"
CMD ["uvicorn", "travel_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
