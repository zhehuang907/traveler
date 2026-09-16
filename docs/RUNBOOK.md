# 运行手册（Runbook）

## 1. 本地开发运行

```bash
uv sync                                   # 1. 安装依赖
cp .env.example .env                      # 2. 准备配置并填入 LLM_API_KEY
uv run python -m travel_agent.doctor      # 3. 自检
uv run uvicorn travel_agent.main:app --reload
```

预期输出（节选）：

```text
INFO | 智能旅游规划 Agent v0.1.0 环境自检 ... 核心环境就绪
INFO     2026-.. | startup app_env=dev version=0.1.0 host=127.0.0.1 port=8000
INFO     Uvicorn running on http://127.0.0.1:8000
```

访问：

| 地址 | 用途 |
|---|---|
| http://127.0.0.1:8000/docs | Swagger UI（当前可调试健康探针） |
| http://127.0.0.1:8000/healthz | 存活探针 |
| http://127.0.0.1:8000/readyz | 就绪探针（数据/缓存目录可写性） |

## 2. 数据库初始化

业务库为 **MySQL**（`127.0.0.1:3306`，root/1234，默认已含 root/1234 连接串，见 `.env` 的 `DATABASE_URL`）；LangGraph checkpoint 为独立的 SQLite 文件 `data/checkpoints.db`。表结构由 Alembic 迁移维护：

```bash
uv run python scripts/init_db.py     # 等价 alembic upgrade head，幂等，可重复执行
uv run alembic current               # 查看当前版本（期望 0003_plans_user_id (head)）
uv run alembic history               # 查看迁移链
```

开发中重建（会清空业务数据）：

```sql
-- 逐个 drop 业务表后重新执行 init_db.py：
DROP DATABASE travel_agent; CREATE DATABASE travel_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

脚本 `scripts/init_db.py` 内部会把异步驱动 URL 替换为同步驱动跑迁移（`mysql+aiomysql` → `mysql+pymysql`）。

## 3. CLI 调试入口

```bash
uv run travel-agent doctor [--online]          # 环境自检（含 MySQL 3306 连通性）
uv run travel-agent serve [--host 0.0.0.0] [--port 8000] [--reload]
uv run travel-agent plan "下个月成都4天，预算5000，爱吃辣"
uv run travel-agent rollback <plan_id> <version>  # 回滚行程到指定版本
uv run travel-agent --version
```

无前端也可通过 `plan` 子命令跑通 Agent 全链路（意图→澄清/检索→天气→编排→校验-修订→交付）：

- 退出码：`0` 成功；`1` Agent 运行失败（如 LLM 连续两次结构化输出不合规 → `LLMError`）；`2` 未配置 `LLM_API_KEY`（先复制 `.env.example` 并填 Key）。
- 成功后：报告打印到 stdout；用户消息与 AI 回复按 `seq` 顺序写入 `messages` 表，生成的行程写入 `plans` 表（含 `PlanDiff` JSON 与触发消息摘要），偏好写入 `user_preferences` 表；LangGraph 全状态（含 Pydantic 模型经 TypedJSON 序列化）落到 checkpoint 库。
- **版本回滚**：CLI `rollback <plan_id> <version>` 或 HTTP `POST /api/plan/{plan_id}/rollback` 把旧版本快照作为新版本写入（不删除历史），便于回退后继续修改。版本号可通过 `GET /api/plan/{plan_id}/versions` 或直接查询 `plan_versions` 表获取。
- **Windows WDAC 环境**若控制台 shim 被「应用程序控制策略已阻止此文件」拦截，改用模块方式运行，功能等价：

```bash
uv run python -m travel_agent.cli plan "成都4天"
uv run python -m travel_agent.cli doctor
```

## 4. Docker 运行

```bash
docker compose up --build
# PDF 自动导出为预留能力（暂未启用），当前版本用浏览器打印将行程另存为 PDF，无需 Chromium；
# 如为预留功能提前构建：在 docker-compose.yml 中将 INSTALL_CHROMIUM 改为 1 后重新构建
```

compose 内只有 app 服务（宿主机 MySQL 经 `DATABASE_URL` 连接）与 `./data` 卷（checkpoint/缓存），键值通过 `env_file: .env` 注入。

| 项 | 默认 | 说明 |
|---|---|---|
| `INSTALL_CHROMIUM` | `0` | 1 时镜像内安装 Chromium（体积增大约 500MB） |
| `DATABASE_URL` | `.env` 中的地址（`mysql+aiomysql://root:1234@127.0.0.1:3306/travel_agent`） | 业务库需先建库并跑过迁移 |
| 卷 `./data:/app/data` | — | checkpoint 与缓存持久化 |

## 5. 生产部署要点

### 5.1 进程模型（单机单 worker）

```bash
# 约束沿用 ADR-007（单 worker）：LangGraph checkpoint 写 SQLite（不支持多进程写），
# 业务库 MySQL 仅读写单个 worker 连接池
uv run uvicorn travel_agent.main:app \
  --host 0.0.0.0 --port 8000 \
  --workers 1 --proxy-headers --forwarded-allow-ips="*"
```

会话状态在 LangGraph SQLite checkpointer 中，SQLite 不保证多进程写一致性，因此仍单 worker；需要扩展时重新评审拆分 checkpointer 存储。

环境：`APP_ENV=prod`（自动 JSON 日志），密钥通过环境变量或密钥管理系统注入。

### 5.2 Nginx 反向代理（SSE 必须关缓冲）

```nginx
server {
    listen 443 ssl;
    server_name travel.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Request-ID $request_id;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/chat {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Connection "";          # SSE 长连接
        proxy_buffering off;                      # 关键：关闭缓冲
        proxy_cache off;
        proxy_read_timeout 300s;
        chunked_transfer_encoding on;
    }
}
```

### 5.3 日志与观测

- 生产日志为单行 JSON（字段：time/level/message/request_id/...），建议接 Loki/ELK。
- 配置 Langfuse 后可在其面板看逐节点 trace、token 成本、失败回放。

## 6. 故障排查表

| 症状 | 可能原因 | 解决 |
|---|---|---|
| 启动即退出，提示配置校验失败 | `.env` 类型/取值非法（如端口非数字、LOG_LEVEL 拼错） | 对照 `.env.example` 修正；`doctor` 会定位失败字段 |
| `doctor` 报 `MySQL 3306 — FAIL` | 本机 MySQL 未启动 / 密码非 1234 / 未建库 | 启动 MySQL 服务；改 `.env` 的 `DATABASE_URL`；执行建库语句（见 SETUP §0） |
| 接口返回 `401 UNAUTHORIZED` | 未登录或 Session 过期（默认 7 天） | 前端先 `/api/auth/login` 获取 Cookie；过期重新登录 |
| 接口对他人行程返回 `404 PLAN_NOT_FOUND` | 行程归属其他用户（数据隔离） | 属预期行为，勿尝试越权访问 |
| 401 / 403（LLM） | Key 错或欠费、base_url 与厂商不匹配 | `doctor --online` 验证；核对厂商兼容模式地址 |
| 搜索无结果/降级日志 | Tavily 限频或 Key 无效 | 自动切博查/DuckDuckGo；查 Langfuse trace 确认命中链 |
| 天气显示「气候常态参考」 | 行程日期超出 7–15 天预报窗口 | 属预期设计，卡片会明确标注数据性质 |
| SSE 前端收不到流式、一次性返回 | Nginx/CDN 缓冲 | 按 5.2 关闭 `proxy_buffering` |
| SSE 频繁断线 | 代理读超时过短 | 调大 `proxy_read_timeout`；前端 EventSource 自带重连续传 |
| PDF 自动导出乱码/空白（预留能力启用后） | 未装 Chromium 或镜像缺 CJK 字体 | `playwright install chromium`；镜像用 `INSTALL_CHROMIUM=1` |
| 地图不显示 | 瓦片网络受限或 Key 配额用尽 | 检查浏览器控制台；更换瓦片源/Key，海外目的地用 Google |
| 429 限流 | 免费额度耗尽 | 降低 `FANOUT_CONCURRENCY` 与 max_results；调大 `POI_MIN_INTERVAL_S`；提高缓存命中率；更换 Key |
| `travel-agent plan` 退出码 2 | 未配置 `LLM_API_KEY` | 复制 `.env.example` 为 `.env` 并填入 DeepSeek Key |
| `plan` 退出码 1 / Web 对话报「结构化输出两次均失败」 | 模型连续两次结构化输出不合规；或单次生成超 `LLM_TIMEOUT`（长草稿高峰期实测 9-24s，若超时被误设为 10s 级会必然失败） | 查看日志中的校验错误回灌内容；确认 `LLM_TIMEOUT` 不低于 60s（默认 120s）；重试；必要时切 `LLM_MODEL` |
| 行程回复里出现「未解决项」 | 校验-修订循环达到 `MAX_REVISE_LOOPS` 仍不合规 | 属预期的强制交付；按提示放宽预算/节奏或手动调整，必要时调大循环次数 |
| 依赖冲突 | 手动 pip 装包污染环境 | 删除 `.venv` 后 `uv sync`（以 uv.lock 为准） |
| doctor 对已安装包报 WARN「导入失败: 应用程序控制策略已阻止此文件」（Windows） | WDAC/AppLocker 拦截了 uv 独立分发 Python 的原生 `.pyd`（如 `_multiprocessing`、`_tiktoken`、lxml `sax`） | 改用受信任的系统解释器建 venv：`uv venv --python 3.13 --python-preference only-system` 后 `uv sync`；或请 IT 对该路径加白。doctor 对「已安装但导入失败」只报 WARN，不阻断启动 |
| 重启后会话丢失 | 误用内存 checkpointer / 数据卷未挂载 | 确认 `CHECKPOINT_DB` 路径持久、compose 挂载 `./data` |

## 7. 成本控制

| Provider | 计费方式 | 控制手段 |
|---|---|---|
| LLM（DeepSeek 等） | 按 token | 结构化输出约束长度；上下文压缩只压早期消息，brief/plan 保留 |
| Tavily | 免费 1000 次/月，后付费 | 搜索 TTL 6h；`max_results<=5`；降级链兜底 |
| 高德 | 个人约 5000 次/日 | POI TTL 24h、路线 TTL 7d；同段路线不重复请求；`POI_MIN_INTERVAL_S` 1 QPS 节流 + 扇出信号量 |
| 和风天气 | 免费约 1000 次/日 | 天气 TTL 1h；超窗走 Open-Meteo/历史气候 |
| Open-Meteo / DuckDuckGo | 免费 | 限频保护（tenacity 退避） |

观测：Langfuse trace 记录每次会话 token 与外部调用次数；日志中缓存命中打 `cache_hit=true` 标记（阶段二）。
