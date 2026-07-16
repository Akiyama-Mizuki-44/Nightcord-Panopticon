#!/bin/bash
# SessionStart hook: 如果这个云端 environment 配了 GPG_SIGNING_KEY 环境变量（base64 编码的
# ASCII-armored 私钥），就把它导入 GPG 并给这个仓库配好 commit 签名。没配这个变量的环境
# （比如本地开发机）直接跳过，不报错。
set -uo pipefail

if [ -z "${GPG_SIGNING_KEY:-}" ]; then
  exit 0
fi

export GNUPGHOME="${GNUPGHOME:-$HOME/.gnupg}"
mkdir -p "$GNUPGHOME" && chmod 700 "$GNUPGHOME"

echo "$GPG_SIGNING_KEY" | base64 -d | gpg --batch --import 2>/dev/null

KEY_ID=$(gpg --list-secret-keys --with-colons 2>/dev/null | awk -F: '/^sec/ {print $5; exit}')
if [ -z "$KEY_ID" ]; then
  echo "[restore_gpg_signing] 没能从 GPG_SIGNING_KEY 导入密钥" >&2
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"
git config --local gpg.format openpgp
git config --local user.signingkey "$KEY_ID"
git config --local commit.gpgsign true

echo "[restore_gpg_signing] 已恢复 commit 签名配置（key: $KEY_ID）"
