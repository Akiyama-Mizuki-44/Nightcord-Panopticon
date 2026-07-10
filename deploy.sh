#!/usr/bin/env bash
# 把本地代码同步到部署服务器（rsync over SSH，不依赖服务器能访问 GitHub）。
#
# 首次使用：
#   cp deploy.env.example deploy.env   # 填好服务器地址/用户名/路径
#   ./deploy.sh
#
# 之后每次本地改完代码，直接 ./deploy.sh 就会增量同步 + 重启远程服务。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/deploy.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "找不到 deploy.env。先执行:" >&2
  echo "  cp deploy.env.example deploy.env" >&2
  echo "然后编辑 deploy.env 填上你自己的服务器信息。" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "$ENV_FILE"

: "${DEPLOY_USER:?deploy.env 里缺少 DEPLOY_USER}"
: "${DEPLOY_HOST:?deploy.env 里缺少 DEPLOY_HOST}"
: "${DEPLOY_PATH:?deploy.env 里缺少 DEPLOY_PATH}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"
DEPLOY_RESTART="${DEPLOY_RESTART:-true}"
DEPLOY_SERVICE="${DEPLOY_SERVICE:-nightcord-panopticon}"

echo "==> 同步 $SCRIPT_DIR/ -> $DEPLOY_USER@$DEPLOY_HOST:$DEPLOY_PATH (端口 $DEPLOY_PORT)"

rsync -avz --progress \
  -e "ssh -p $DEPLOY_PORT" \
  --exclude='.git' \
  --exclude='.claude' \
  --exclude='__pycache__' \
  --exclude='.venv' \
  --exclude='config.yaml' \
  --exclude='deploy.env' \
  --exclude='deploy.log' \
  --exclude='*.pyc' \
  "$SCRIPT_DIR/" \
  "$DEPLOY_USER@$DEPLOY_HOST:$DEPLOY_PATH/"

if [ "$DEPLOY_RESTART" = "true" ]; then
  echo "==> 重启远程服务 ($DEPLOY_SERVICE)"
  ssh -p "$DEPLOY_PORT" "$DEPLOY_USER@$DEPLOY_HOST" \
    "sudo systemctl restart $DEPLOY_SERVICE 2>/dev/null || echo '未找到 systemd 服务，若还是手动 python app.py 方式运行，请自己重启进程'"
else
  echo "==> DEPLOY_RESTART=false，跳过重启，记得自己去重启服务"
fi

echo "==> 完成"
