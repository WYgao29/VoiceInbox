#!/bin/bash
# 手动拉起本机 API。桌面应用会自己拉起，一般不用这个脚本。
# 用项目虚拟环境，不经过 bin/voiceinbox-python，也不动 launchd。
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo "找不到 .venv/bin/python。请先按 README 创建虚拟环境。" >&2
  exit 1
fi
exec .venv/bin/python -m voiceinbox web "$@"
