# 智能旅游规划 Agent 常用命令（Windows 无 make 时可直接复制 uv 命令执行）
.PHONY: setup sync run dev doctor lint format typecheck test cov pdf docker-up clean

setup: ## 初始化：安装依赖 + pre-commit + Playwright Chromium
	uv sync --extra pdf
	uv run pre-commit install
	uv run playwright install chromium

sync: ## 仅同步 Python 依赖
	uv sync --extra pdf

run: ## 生产方式启动（python -m 方式兼容 Windows 应用控制策略拦截 uvicorn.exe）
	uv run python -m uvicorn travel_agent.main:app --host 0.0.0.0 --port 8000

dev: ## 热重载开发（python -m 方式兼容 Windows 应用控制策略拦截 uvicorn.exe）
	uv run python -m uvicorn travel_agent.main:app --reload --host 127.0.0.1 --port 8000

doctor: ## 环境自检
	uv run python -m travel_agent.doctor

lint: ## Ruff 检查
	uv run ruff check .

format: ## Ruff 格式化 + 自动修复
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## mypy 严格类型检查
	uv run mypy

test: ## 全量测试
	uv run pytest

cov: ## 测试 + 覆盖率报告
	uv run pytest --cov --cov-report=term-missing

pdf: ## PDF 自动导出（预留，尚未启用）
	@echo "PDF 自动导出暂未启用：请在行程页使用浏览器打印（Ctrl+P → 另存为 PDF）"

docker-up: ## 容器方式启动
	docker compose up --build

clean: ## 清理缓存与本地产物
	rm -rf .ruff_cache .mypy_cache .pytest_cache htmlcov .coverage .coverage.* coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
