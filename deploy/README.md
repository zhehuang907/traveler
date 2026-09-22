# 部署说明（Docker Compose + MySQL8 + Caddy HTTPS）

## 1. 准备服务器

- 云服务器（Ubuntu 22.04/24.04 / Debian 12，2C4G 起步）
- 域名 A 记录解析到服务器公网 IP（Caddy 会自动签发 HTTPS 证书）
- 云安全组放行 `80/443`（无需外放 8000，应用只经 Caddy 访问）

## 2. 安装 Docker

```bash
curl -fsSL https://get.docker.com | sh
```

## 2.1 通过宝塔面板部署（Linux）

宝塔仅维护 Linux 版（无 Windows 版，Linux 也没有 D 盘概念；数据盘通过
宝塔「磁盘挂载」挂到 `/www` 使用）：

1. 安装宝塔（Ubuntu/Debian）：
   ```bash
   wget -O install.sh https://download.bt.cn/install/install-ubuntu_6.0.sh && sudo bash install.sh
   ```
   CentOS/RHEL 换用 `https://download.bt.cn/install/install_6.0.sh`。
2. 软件商店安装「Docker 管理器」。**不要安装宝塔的 Nginx/MySQL/PHP 应用**——
   本项目 compose 自带 Caddy 占用 80/443，装 Nginx 会抢端口导致证书失败。
3. 云安全组与宝塔「安全」页放行 `80/443`。
4. 上传代码到 `/www`（宝塔文件管理器或 `git clone`），在项目根
   `cp deploy/.env.prod.example .env` 并填写密钥（见下节）。
5. 宝塔「终端」执行 `docker compose up -d --build`（或 Docker 管理器 →
   容器编排 → 导入 `docker-compose.yml`）。后续验证/升级与 4-5 节相同。

## 3. 上传代码并配置

将项目目录同步到服务器（git clone 或 scp/rsync），进入项目根目录：

```bash
cd traveler
cp deploy/.env.prod.example .env
vim .env   # 必填：DOMAIN、MYSQL_ROOT_PASSWORD、MYSQL_PASSWORD、LLM_API_KEY、AMAP_API_KEY
```

`docker-compose.yml` 会按 `.env` 里的 `MYSQL_*` 自动拼接数据库连接串，
无需手写 `DATABASE_URL`。

## 4. 启动

```bash
bash deploy/web.sh        # 构建镜像 + 后台启动四服务
docker compose ps         # 确认 mysql healthy、app running、caddy running
```

启动顺序由依赖控制：`mysql`（健康检查通过）→ `migrate`（一次性跑
`alembic upgrade head`）→ `app` → `caddy`。数据库与对话数据分别落在
`mysql_data`、`./data` 卷中，`docker compose up -d --build` 升级不丢数据。

## 5. 验证与维护

```bash
curl -fsSL https://你的域名/healthz          # 期望 OK/状态码 200
docker compose logs -f app                   # 应用日志（prod 为 JSON 结构化）
docker compose build --build-arg INSTALL_CHROMIUM=1  # 如需 PDF 导出（镜像 +约500MB）
```

升级：改代码后 `docker compose up -d --build`（migrate 会自动补跑新迁移）。
回滚：`docker compose up -d <上一版本镜像>` 或重建引用的 git 提交。

## 注意事项

- 仅支持单实例部署（SQLite checkpointer + diskcache 落在本地卷，扩容副本会冲突）
- 密码建议纯字母数字，避免特殊字符破坏 MySQL 连接串
- 二次部署前若误删 `mysql_data` 卷会丢库，备份：`docker compose exec mysql sh -c 'exec mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' > backup.sql`