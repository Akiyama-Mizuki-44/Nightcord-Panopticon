#!/usr/bin/env bash
# 通过 SSH 隧道访问部署服务器上的 Nightcord Panopticon（不对公网开 1810 端口）。
#
# 用法：
#   ./tunnel.sh
# 然后浏览器打开 http://127.0.0.1:1810（或者你在 deploy.env 里改的 LOCAL_TUNNEL_PORT），
# Ctrl+C 关掉隧道。复用 deploy.env 里的 DEPLOY_USER/DEPLOY_HOST/DEPLOY_PORT，跟 deploy.sh
# 是同一份服务器信息，不用重复配置。

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
DEPLOY_PORT="${DEPLOY_PORT:-22}"
REMOTE_APP_PORT="${REMOTE_APP_PORT:-1810}"
LOCAL_TUNNEL_PORT="${LOCAL_TUNNEL_PORT:-$REMOTE_APP_PORT}"

echo "==> 建立隧道：本机 127.0.0.1:${LOCAL_TUNNEL_PORT} -> ${DEPLOY_HOST}:${REMOTE_APP_PORT} (经 ${DEPLOY_USER}@${DEPLOY_HOST}:${DEPLOY_PORT})"
echo "==> 就绪后浏览器打开 http://127.0.0.1:${LOCAL_TUNNEL_PORT} ，Ctrl+C 关闭隧道"

ssh -N \
  -L "${LOCAL_TUNNEL_PORT}:127.0.0.1:${REMOTE_APP_PORT}" \
  -p "$DEPLOY_PORT" \
  "$DEPLOY_USER@$DEPLOY_HOST"
