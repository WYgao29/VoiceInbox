#!/bin/bash
# 复制 Homebrew Python，签成项目专用的 bin/voiceinbox-python。
# 完全磁盘访问授给这个文件，不要授给 Cellar 里的 python3.14。
# 源解释器没变时不会重签。brew 升级会改掉 Cellar 路径，那时会重签，
# 已经授过的完全磁盘访问失效。可用 brew pin python@3.14 锁版本。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "缺少可执行的 $ROOT/.venv/bin/python" >&2
  exit 1
fi
LAUNCHER="$ROOT/bin/voiceinbox-python"

cdhash() {
  codesign -dv --verbose=4 "$1" 2>&1 | awk -F= '/^CDHash=/{print $2; exit}'
}

existed=0
before=""
if [[ -e "$LAUNCHER" && ! -L "$LAUNCHER" ]]; then
  existed=1
  before="$(cdhash "$LAUNCHER" || true)"
fi

"$ROOT/.venv/bin/python" -c "from voiceinbox.launcher import ensure_launcher; print(ensure_launcher())"

if [[ "${VOICEINBOX_SUPPRESS_RESIGN_WARNING:-}" == "1" ]]; then
  exit 0
fi

after="$(cdhash "$LAUNCHER" || true)"
if [[ "$existed" -eq 1 && -n "$after" && "$before" != "$after" ]]; then
  cat <<EOF

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
已重新签名：
  ${LAUNCHER}
需要重新到完全磁盘访问授权
旧的授权对这份新签名无效。
系统设置 → 隐私与安全性 → 完全磁盘访问权限
按 ⌘⇧G 重新加入上面的文件，并打开开关。
然后执行：
  launchctl kickstart -k gui/$(id -u)/com.wayne.voiceinbox
建议锁住 Homebrew Python，避免下次升级再重签：
  brew pin python@3.14
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

EOF
fi
