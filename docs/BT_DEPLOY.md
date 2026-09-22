# 宝塔面板部署手册（云服务器）

> 适用：腾讯云 / 阿里云 / 华为云等任意云服务器 + 宝塔面板（Linux）。
> 项目要求 **Python ≥ 3.13**（uv 自动管理）、**MySQL 8**、**Nginx**（反向代理 + SSE 流式）。

---

## 0. 部署架构

```
用户浏览器
   │ HTTPS
   ▼
Nginx（宝塔网站 · 443/80）
   │ 反向代理（SSE 已关缓冲）
   ▼
uvicorn（127.0.0.1:8000 · FastAPI）
   ├── MySQL 8（业务库 travel_agent，宝塔安装）
   ├── data/checkpoints.db（LangGraph 会话记忆，SQLite 文件）
   └── data/cache（diskcache 搜索/天气/POI 缓存）
```

- 本机 Windows 已验证：对话 SSE、多轮上下文、PDF 导出、SVG 路线、355 测试全绿。
- 无需 Node 构建链：前端是 Jinja2 + 静态资源，直接由 FastAPI 服务。

---

## 1. 宝塔前置准备

1. **安装宝塔**：按云厂商文档执行（CentOS: `yum install -y wget && wget -O install.sh https://download.bt.cn/install/install_6.0.sh && bash install.sh`；Ubuntu/Debian 用对应脚本）。
2. **安装软件**（宝塔软件商店）：
   - **Nginx 1.22+**
   - **MySQL 8.0**（安装后记下 root 密码；生产建议改强密码）
3. **开放端口**（云厂商安全组 + 宝塔防火墙）：
   - `22`（SSH，尽量改密钥登录）
   - `80` / `443`（网站访问）
   - `8888`（宝塔面板，建议仅白名单）

> 用 `python --version` 无需关心：项目由 uv 自动拉取 Python 3.13，见第 4 步。

---

## 2. 上传项目代码

两种方式任选：

- **宝塔文件管理器**：进入 `/www/wwwroot/`，新建目录 `travel-agent`，上传本项目全部文件（**可不上传**：`.git/`、`.idea/`、`.venv/`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`、`tests/`、`data/`、`scripts/_*.py` 临时脚本）。
- **Git**（若项目已推到远端）：
  ```bash
  cd /www/wwwroot
  git clone <仓库地址> travel-agent
  cd travel-agent
  ```

> 关键目录确认存在：`src/travel_agent/`、`migrations/`、`prompts/`、`scripts/`、`docs/`、`pyproject.toml`、`uv.lock`。

---

## 3. 创建数据库

宝塔「数据库」→「添加数据库」：

- 数据库名：`travel_agent`
- 编码：`utf8mb4`
- 访问权限：**本地服务器**
- 用户名/密码：建议单独建账号（如 `travel` / 强密码），不要用 root

> 表结构由 Alembic 迁移自动创建（下一步），无需手工建表。

---

## 4. 安装依赖（SSH / 宝塔终端）

```bash
cd /www/wwwroot/travel-agent

# 4.1 安装 uv（Python 3.13 由 uv 自动下载，无需手动装 Python）
curl -LsSf https://astral.sh/uv/install.sh | sh
# 重新登录 shell 或：export PATH="$HOME/.local/bin:$PATH"

# 4.2 用 uv 同步依赖（自动装 Python 3.13 + 全部生产依赖）
uv sync --no-dev --frozen

# 4.3 可选：PDF 导出依赖（含 Chromium，约 +150MB；不安也能启动，PDF 导出会降级为浏览器打印）
uv sync --no-dev --extra pdf
uv run playwright install --with-deps chromium
```

验证：

```bash
uv run python -m travel_agent.doctor        # 环境自检（核心）
uv run python -m travel_agent.doctor --online  # 含外部服务连通性
```

---

## 5. 配置 .env

```bash
cp .env.example .env
nano .env        # 宝塔终端可用 vi/nano
```

必改项（生产）：

```ini
APP_ENV=prod                    # JSON 日志
LOG_LEVEL=INFO
HOST=0.0.0.0
PORT=8000

# LLM（DeepSeek，必需——Agent 规划核心）
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=你的DeepSeekKey

# 数据库：改成第 3 步建的库和账号
DATABASE_URL=mysql+aiomysql://travel:你的强密码@127.0.0.1:3306/travel_agent?charset=utf8mb4

# 搜索：tavily 没配就留空；本机 BOCHA_API_KEY 已失效（403），部署时务必清空或换新，
# 否则每次搜索都白等一轮 403 再降级
SEARCH_PROVIDER_CHAIN=tavily,bocha,duckduckgo
TAVILY_API_KEY=
BOCHA_API_KEY=

# 地图 POI（高德，可选但强烈建议——景点/餐厅/路线都靠它）
AMAP_API_KEY=你的高德Web服务Key

# 天气（和风，可选）
QWEATHER_API_KEY=
```

> 密钥只放 `.env`，代码与日志不出现明文（SecretStr 承载）。
> 所有 Key 留空都能启动：搜索/天气/地图自动降级，只有 `LLM_API_KEY` 是对话必需。

---

## 6. 初始化数据库（建表）

```bash
cd /www/wwwroot/travel-agent
uv run python scripts/init_db.py       # 等价 alembic upgrade head，幂等可重复
uv run alembic current                 # 应显示 0003_plans_user_id (head)
```

---

## 7. 启动服务（二选一）

### 方式 A：宝塔 Supervisor 管理器（推荐，界面化管理）

1. 宝塔软件商店安装 **Supervisor 管理器**。
2. 「添加守护进程」：
   - 名称：`travel-agent`
   - 启动用户：`www`（或 root）
   - 运行目录：`/www/wwwroot/travel-agent`
   - 启动命令：
     ```
     /www/wwwroot/travel-agent/.venv/bin/uvicorn travel_agent.main:app --host 127.0.0.1 --port 8000
     ```
   - 进程数量：1
3. 保存后状态应为「运行中」。

### 方式 B：systemd（无宝塔扩展时）

```bash
cat > /etc/systemd/system/travel-agent.service <<'EOF'
[Unit]
Description=Travel Agent (FastAPI)
After=network.target mysql.service

[Service]
Type=simple
User=www
Group=www
WorkingDirectory=/www/wwwroot/travel-agent
ExecStart=/www/wwwroot/travel-agent/.venv/bin/uvicorn travel_agent.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

chown -R www:www /www/wwwroot/travel-agent
systemctl daemon-reload
systemctl enable --now travel-agent
systemctl status travel-agent
```

> 应用内部 `pydantic-settings` 会自动读取 `.env`，无需 EnvironmentFile。
> `data/` 目录必须对运行用户（www）可写（checkpoints.db / cache）。

自检：

```bash
curl -s http://127.0.0.1:8000/healthz   # {"status":"ok","version":"0.1.0"}
curl -s http://127.0.0.1:8000/readyz    # data/cache 可写则 ready
```

---

## 8. Nginx 反向代理（宝塔网站）

### 8.1 建站

宝塔「网站」→「添加站点」：
- 域名：你的域名（或服务器 IP，无域名可先用 IP 测试）
- PHP 版本：纯静态
- 创建后进入站点设置 → 配置文件，替换为下方模板。

### 8.2 Nginx 配置模板（关键：SSE 必须关缓冲）

```nginx
server {
    listen 80;
    server_name 你的域名或IP;

    client_max_body_size 10m;          # 文档上传（后端上限 5MB）

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 流式关键配置：禁止缓冲，防止对话进度卡住
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;       # AI 规划最长可达 1-2 分钟
        proxy_send_timeout 300s;

        # WebSocket 升级（若未来接入实时能力）
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

> 应用已返回 `X-Accel-Buffering: no`，但 Nginx 侧显式 `proxy_buffering off` 双保险。

### 8.3 HTTPS（推荐）

宝塔「SSL」→ 申请 Let's Encrypt 免费证书（需域名已解析到服务器）→ 开启「强制 HTTPS」。

---

## 9. 上线验证清单

| 检查项 | 方法 | 预期 |
|---|---|---|
| 健康探针 | `curl http://127.0.0.1:8000/healthz` | `{"status":"ok",...}` |
| 就绪探针 | `curl http://127.0.0.1:8000/readyz` | `"status":"ready"` |
| 首页 | 浏览器访问域名 | 登录/注册卡片正常 |
| 注册→登录 | 页面操作 | 成功进入对话页 |
| 对话流式 | 发送消息 | AI 逐步回复，进度节点实时滚动（验证 SSE 未缓冲） |
| 多轮上下文 | 刷新页面 | 历史消息恢复，继续对话带上下文 |
| 我的行程 | 生成行程后打开 | 行程详情 + SVG 路线示意 + 费用图 |
| PDF 导出 | 行程页点导出 | 下载有效 PDF（装了 Chromium）或提示浏览器打印 |
| API 文档 | `/docs` | Swagger 正常 |

---

## 10. 运维速查

```bash
# Supervisor 方式
# 宝塔界面重启/看日志；或：supervisorctl restart travel-agent

# systemd 方式
systemctl restart travel-agent
journalctl -u travel-agent -f          # 实时日志

# 数据备份（MySQL + 文件）
mysqldump -u travel -p travel_agent > /www/backup/travel_agent_$(date +%F).sql
tar czf /www/backup/data_$(date +%F).tar.gz -C /www/wwwroot/travel-agent data

# 数据库迁移（升级代码后）
uv sync --no-dev --frozen
uv run python scripts/init_db.py
```

---

## 11. 常见问题

| 现象 | 原因/处理 |
|---|---|
| 首页 502 | uvicorn 没起：看 Supervisor/systemd 日志；确认 `data/` 属主 |
| 对话进度卡住不滚动 | Nginx 缓冲了 SSE：确认 `proxy_buffering off` 生效 |
| 报错「Table doesn't exist」 | 未执行 `uv run python scripts/init_db.py` |
| 登录/注册 502 | 确认 MySQL 在跑、`DATABASE_URL` 账号密码正确 |
| 搜索总超时 | `BOCHA_API_KEY` 失效会白等 403 再降级：部署时清空失效 key |
| 中文乱码 | MySQL 建库未选 `utf8mb4`：重建库或改字符集 |
