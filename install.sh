#!/usr/bin/env bash
# 把本仓库安装（或更新）成 Codex skill：$CODEX_HOME/skills/hoi4-focus-generator
#
# 为什么是"拷贝"而不是软链接：Codex 枚举 skills 时**不会跟进软链接目录**
# （实测：在 ~/.codex/skills/ 下放一个指向仓库的软链接，会话的 skills 清单里不会出现它）。
# 所以本仓库是真源，装到 skills 目录里的是一份拷贝；改了仓库就重跑一次这个脚本。
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
name="hoi4-focus-generator"
dest="${CODEX_HOME:-$HOME/.codex}/skills/$name"

if ! [ -f "$here/SKILL.md" ]; then
  echo "找不到 $here/SKILL.md —— 请在仓库根目录运行本脚本" >&2
  exit 1
fi

mkdir -p "$dest"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
        --exclude '.git' --exclude '.venv*' --exclude '__pycache__' --exclude '*.pyc' \
        --exclude 'install.sh' --exclude '.gitignore' \
        "$here"/ "$dest"/
else
  echo "没有 rsync，改用 cp（不会删除目标目录里的旧文件）" >&2
  for item in SKILL.md scripts references agents README.md README.en.md LICENSE \
              requirements.txt selftest.sh; do
    [ -e "$here/$item" ] && cp -r "$here/$item" "$dest/"
  done
fi

echo "已安装：$dest"
echo "（这是拷贝，不是软链；库里改了代码就重跑本脚本）"
