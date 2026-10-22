#!/bin/bash
# 把 com.wayne.voiceinbox 装进当前用户的 LaunchAgents。
# 装之前请先读 README.md 的「安装与卸载」和「完全磁盘访问」。
# 这个脚本可重复执行：会先卸掉同名任务再装。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.wayne.voiceinbox"
DOMAIN="gui/$(id -u)"
SRC="$ROOT/launchd/${LABEL}.plist"
DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "缺少可执行的 $ROOT/.venv/bin/python" >&2
  exit 1
fi
if [[ ! -f "$SRC" ]]; then
  echo "缺少 $SRC" >&2
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

# 重签提示留到本脚本结尾再打，避免被后面的安装输出盖住。
VOICEINBOX_SUPPRESS_RESIGN_WARNING=1 bash "$ROOT/scripts/build_launcher.sh"
if [[ ! -x "$LAUNCHER" || -L "$LAUNCHER" ]]; then
  echo "缺少项目专用 Python：$LAUNCHER" >&2
  exit 1
fi

cat <<EOF
即将安装 launchd 任务 ${LABEL}

完全磁盘访问请授给这个文件，不要授给 Homebrew 的 python3.14：
  ${LAUNCHER}

它会在下面几种情况各跑一轮「扫描一次就退出」：
- 登录后（RunAtLoad）
- 语音备忘录 group 容器、Recordings、或 ~/VoiceInbox/inbox 发生变化（WatchPaths）
- 按 config.toml [monitor] interval_min 定时兜底（StartInterval），两次触发至少隔 30 秒（ThrottleInterval）
- [monitor] watch = false 时不监视文件夹

没有待转写的录音时不会加载 Qwen。有录音时同一时间只有一个转写进程（文件锁）。
标准输出：~/VoiceInbox/logs/launchd.out.log
业务日志：~/VoiceInbox/logs/voiceinbox-日期.log
卸载（只卸任务，不删数据）：
  bash "$ROOT/scripts/uninstall_launchd.sh"
授权步骤以 README 为准。若这次重新签名了副本，脚本结束时会提示重新授权。
从终端跑 doctor 可能按终端自己的授权判断，不能代替 kickstart 之后的日志。
EOF

mkdir -p \
  "${HOME}/VoiceInbox/inbox" \
  "${HOME}/VoiceInbox/staging" \
  "${HOME}/VoiceInbox/work" \
  "${HOME}/VoiceInbox/transcripts" \
  "${HOME}/VoiceInbox/failed" \
  "${HOME}/VoiceInbox/state" \
  "${HOME}/VoiceInbox/logs" \
  "${HOME}/VoiceInbox/archive/audio" \
  "${HOME}/Library/LaunchAgents"

# 按 config.toml 的 [monitor]（网页「设置 → 后台监控」改的也是它）生成 plist：间隔、是否监视文件夹。
if [[ -f "$DEST" ]]; then
  mkdir -p "${HOME}/VoiceInbox/backups/launchd"
  cp "$DEST" "${HOME}/VoiceInbox/backups/launchd/${LABEL}.$(date +%Y%m%d-%H%M%S).plist"
fi
WANT="$("$ROOT/.venv/bin/python" -m voiceinbox monitor render "$DEST")"
plutil -lint "$DEST"
launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
if [[ "$WANT" == "disabled" ]]; then
  echo "config.toml 里后台监控是关的（[monitor] enabled = false），只写好 plist，不装载。"
  exit 0
fi
launchctl bootstrap "$DOMAIN" "$DEST"
echo "已安装 ${LABEL}"
launchctl print "${DOMAIN}/${LABEL}" | sed -n '1,30p' || true
launchctl print "${DOMAIN}/${LABEL}" >/dev/null
echo "登录时会自动跑一轮。也可以马上触发："
echo "  launchctl kickstart -k ${DOMAIN}/${LABEL}"

after="$(cdhash "$LAUNCHER" || true)"
if [[ "$existed" -eq 1 && -n "$after" && "$before" != "$after" ]]; then
  cat <<EOF

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
这次安装重新签名了：
  ${LAUNCHER}
需要重新到完全磁盘访问授权
旧的授权对这份新签名无效。
系统设置 → 隐私与安全性 → 完全磁盘访问权限
按 ⌘⇧G 重新加入上面的文件，并打开开关。
然后执行：
  launchctl kickstart -k ${DOMAIN}/${LABEL}
能不能读语音备忘录，以这次 kickstart 之后的日志为准，
不要只看从终端跑出来的 doctor。
建议锁住 Homebrew Python，避免下次升级再重签：
  brew pin python@3.14
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

EOF
fi
