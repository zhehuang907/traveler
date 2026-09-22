#!/usr/bin/env bash
# 一键部署：构建镜像 → 启动 MySQL/迁移/应用/Caddy
# 前提：服务器已装 Docker 20.10+（curl -fsSL https://get.docker.com | sh）
# 用法：bash deploy/web.sh
set -euo pipefail
cd "$(dirname "$0")/.."

command -v docker >/dev/null 2>&1 || {
  echo "错误：未检测到 Docker。安装：curl -fsSL https://get.docker.com | sh"; exit 1
}
docker compose version >/dev/null 2>&1 || {
  echo "错误：需要 docker compose 插件（Docker 自 23+ 内置）"; exit 1
}

if [ ! -f .env ]; then
  cp deploy/.env.prod.example .env
  echo "已生成 .env —— 请先编辑："
  echo "  DOMAIN（你的域名）"
  echo "  MYSQL_ROOT_PASSWORD / MYSQL_PASSWORD（强密码，字母数字）"
  echo "  LLM_API_KEY / AMAP_API_KEY（其余可留空自动降级）"
  exit 1
fi

docker compose up -d --build
echo
echo "启动完成，检查状态：docker compose ps"
echo "健康探针：curl -fsSL https://\${DOMAIN}/healthz"