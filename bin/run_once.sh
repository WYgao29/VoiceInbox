#!/bin/bash
# 手动跑一轮。launchd 不走这个脚本，而是直接执行 bin/voiceinbox-python。
# 完全磁盘访问授给那份项目专用副本，不授给 /bin/bash，也不授给 Homebrew 的 python3.14。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHER="$ROOT/bin/voiceinbox-python"
if [[ ! -x "$LAUNCHER" || -L "$LAUNCHER" ]]; then
  echo "缺少 $LAUNCHER。先运行 bash \"$ROOT/scripts/build_launcher.sh\"" >&2
  exit 1
fi
export TZ=Asia/Shanghai
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export HF_HUB_OFFLINE=1
export NO_PROXY="127.0.0.1,localhost"
export VOICEINBOX_CONFIG="${VOICEINBOX_CONFIG:-$ROOT/config.toml}"
cd "$ROOT"
exec "$LAUNCHER" -m voiceinbox run-once
