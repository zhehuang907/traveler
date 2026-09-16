# 环境配置指南

## 0. 数据库前置：MySQL 8

业务数据存储在 **MySQL**（默认 `127.0.0.1:3306`，root / 1234）。请先确保本机 MySQL 8 已安装并启动，然后建库：

```sql
CREATE DATABASE travel_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- 测试库（pytest 使用，建表自动完成）：
CREATE DATABASE travel_agent_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

连接串在 `.env` 的 `DATABASE_URL` 配置（`mysql+aiomysql://root:1234@127.0.0.1:3306/travel_agent?charset=utf8mb4`），非 root/1234 请同步修改。表结构用 Alembic 迁移维护：`uv run python scripts/init_db.py`（幂等，等价 `alembic upgrade head`）。

> LangGraph 的离线会话 Checkpointer 仍使用独立 SQLite 文件（`CHECKPOINT_DB=./data/checkpoints.db`），与业务库互不影响。

## 1. 安装 Python 3.13+ 与 uv

本项目要求 Python 3.13+（`.python-version` 已固定为 3.13），uv 会在首次 `uv sync` 时自动下载对应解释器，无需手动安装。

**Windows（PowerShell，二选一）**

```powershell
# 官方安装脚本
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# 或 winget
winget install --id=astral-sh.uv -e
```

**macOS**

```bash
brew install uv
```

**Linux**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

验证：`uv --version`。

## 2. 安装依赖

```bash
uv sync                 # 核心依赖 + 开发依赖
# 备用（无 uv 时）：python -m venv .venv && .venv\Scripts\activate && pip install -e ".[pdf]"
```

PDF 自动导出为**预留能力**（当前版本未启用，行程页请用浏览器打印：Ctrl+P → 另存为 PDF）。如需为预留功能提前安装依赖：

```bash
uv sync --extra pdf
uv run playwright install chromium
```

## 3. 申请并填写 API Key

复制环境文件：

```bash
cp .env.example .env       # Windows PowerShell: copy .env.example .env
```

除 LLM 外全部可选——缺失时自动降级，应用仍可启动。

> **密钥安全**：所有 Key 仅写入 `.env`（已被 `.gitignore` 排除），代码中以 `SecretStr` 承载；doctor/日志只显示"已配置/未配置"，绝不打印密钥内容。

### 3.1 LLM（必需）

默认固定 **DeepSeek**（OpenAI 兼容协议）：https://platform.deepseek.com/ 注册创建 API Key，新用户通常赠送额度。在 `.env` 填：

```env
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-xxxx
LLM_MODEL=deepseek-chat
```

客户端走 OpenAI 兼容协议，日后想换厂商只需改环境变量（应用代码不绑定 DeepSeek）：

| 厂商 | 申请地址 | base_url | 模型示例 |
|---|---|---|---|
| DeepSeek（默认） | https://platform.deepseek.com/ | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 阿里云百炼（通义千问） | https://dashscope.console.aliyun.com/apiKey | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 智谱 GLM | https://open.bigmodel.cn/usercenter/apikeys | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |

### 3.2 通用网页搜索（可选）

- **Tavily**（首选，专为 LLM 清洗摘要，免费约 1000 次/月）：https://tavily.com → 注册后在 Dashboard 复制 `TVLY-...` 到 `TAVILY_API_KEY`。
- **博查 Bocha**（国内备选）：https://open.bochaai.com → 申请 Key 填 `BOCHA_API_KEY`。
- **DuckDuckGo**：无需 Key（依赖随项目安装的 `duckduckgo-search` 包），兜底永远可用；稳定性一般、限频较严，依赖缺失时该家自动记为失败并继续降级。

### 3.3 地图 / POI（默认链下必需）

- **高德开放平台**（默认且唯一的默认 Provider，GCJ-02）：https://lbs.amap.com → 注册 → 控制台创建「Web 服务」类型 Key（个人认证约 5000 次/日免费），填 `AMAP_API_KEY`。地理编码、POI、驾车/步行/公交路径全部由高德提供。
- **OpenStreetMap（实现保留，默认关闭）**：代码内置 Nominatim 地理编码 + Overpass POI + OSRM 路径的免 Key 兜底，但本项目目标部署环境无法访问这些公共端点，因此 `MAPS_PROVIDER_CHAIN` 默认仅 `amap`。在网络可达 OSM 的环境（如海外服务器）设置 `MAPS_PROVIDER_CHAIN=amap,osm` 即可恢复兜底，无需改代码。注意 OSM 无免费公交路径接口，公交模式仅高德支持。
- `GOOGLE_MAPS_API_KEY` 为后续阶段预留，阶段二尚未接入。

### 3.4 天气（可选）

- **和风天气 QWeather**（国内首选，7 日预报）：https://dev.qweather.com → 创建项目获取 Key（免费订阅约 1000 次/日），填 `QWEATHER_API_KEY`。新项目控制台会分配专属 API Host，如非默认 `https://devapi.qweather.com` 请填 `QWEATHER_API_HOST`。
- **Open-Meteo**：https://open-meteo.com ，完全免费、无需 Key，提供 16 天预报并作为兜底；超出预报窗口的日期自动改用其 archive 接口的**去年同期气候参考**（返回中显式标记 `is_climate_reference=true`，不冒充实时预报）。

### 3.5 降级链与外部调用调优（可选）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SEARCH_PROVIDER_CHAIN` | `tavily,bocha,duckduckgo` | 逗号分隔，按序尝试，未配置 Key 的自动跳过 |
| `WEATHER_PROVIDER_CHAIN` | `qweather,openmeteo` | 同上 |
| `MAPS_PROVIDER_CHAIN` | `amap` | 同上；网络可达 OSM 时可设 `amap,osm` |
| `QWEATHER_API_HOST` | `https://devapi.qweather.com` | 和风专属主机 |
| `EXTERNAL_TIMEOUT` | `10.0` | 单次外部请求超时秒数（搜索/地图/天气等工具接口） |
| `LLM_TIMEOUT` | `120.0` | 单次 LLM 请求超时秒数；长文本生成（行程草稿数千 token）实测 9-24s，独立于 `EXTERNAL_TIMEOUT`，切勿照搬 10s 短超时会误杀高峰期调用 |
| `EXTERNAL_RETRY_ATTEMPTS` | `3` | 瞬时错误（408/429/5xx、网络错误）重试次数 |
| `EXTERNAL_RETRY_BASE_DELAY` | `0.5` | 指数退避基数秒，上限 8 倍 |
| `CACHE_TTL_SEARCH` / `CACHE_TTL_WEATHER` | `21600` / `3600` | 搜索 6h / 天气 1h |
| `CACHE_TTL_POI` / `CACHE_TTL_ROUTE` | `86400` / `604800` | POI·地理编码 24h / 路线 7d |
| `FANOUT_CONCURRENCY` | `4` | Agent 检索扇出并发上限（1-16），POI/酒店/贴士/攻略并发时取信号量 |
| `POI_MIN_INTERVAL_S` | `1.0` | 高德 POI 两次请求最小间隔秒数（0-10），个人开发者被限 QPS 时调大；0 关闭节流 |
| `MAX_REVISE_LOOPS` | `3` | 行程校验-修订循环最大次数，超出强制交付并把残留问题告知用户 |

所有缓存落在本地 diskcache（`CACHE_DIR`，默认 `data/cache`），缓存故障自动降级为直连，不影响主流程。
工具失败同样不炸断 Agent 主流程：异常被 `ToolRegistry` 吞掉、记 degraded trace 并降级为空结果。

金额展示固定为人民币（`DEFAULT_CURRENCY=CNY`）：不接入汇率服务，Provider 返回的外币价格不自动换算，仅在有原始币种信息时原样标注。

### 3.6 可观测性（可选）

- **Langfuse Cloud**：https://cloud.langfuse.com 注册 → 新建项目 → 将 public/secret key 填入 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`。
- 自托管：改 `LANGFUSE_HOST` 指向你的实例。

## 4. 环境自检

```bash
uv run python -m travel_agent.doctor
# 或
uv run travel-agent doctor --online
```

输出示例：

```text
智能旅游规划 Agent v0.1.0 环境自检
========================================================
  [ OK ] Python — 3.13
  [ OK ] 依赖 fastapi — 0.115.x
  ...
  [ OK ] MySQL 3306 — mysql+aiomysql://root:***@127.0.0.1:3306/travel_agent
  [WARN] 密钥 LLM_API_KEY — 未配置（Agent 核心能力不可用，应用仍可启动）
========================================================
合计 N 项：OK=.. WARN=.. FAIL=..
```

- `FAIL`：必须修复（Python 版本、核心依赖、目录不可写），退出码 1。
- `WARN`：可选项缺失或建议项，不影响启动。
- 自检只显示「已配置/未配置」，**绝不打印密钥内容**。

## 5. VS Code 推荐配置

插件：Python、Pylance、Ruff、Even Better TOML、Jinja。

`.vscode/settings.json`（按需自建）：

```json
{
  "python.defaultInterpreterPath": ".venv/Scripts/python.exe",
  "python.analysis.typeCheckingMode": "strict",
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": { "source.fixAll.ruff": "explicit" }
  },
  "ruff.configurationHint": "pyproject.toml"
}
```

## 6. 安装 Git 钩子（可选）

```bash
uv run pre-commit install
```

提交时自动执行 ruff 格式化/修复与 mypy 检查。
