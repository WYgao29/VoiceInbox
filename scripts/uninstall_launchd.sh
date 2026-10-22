#!/bin/bash
# 卸掉 com.wayne.voiceinbox。不删除 ~/VoiceInbox 里的转写结果。
set -euo pipefail

LABEL="com.wayne.voiceinbox"
DOMAIN="gui/$(id -u)"
DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null \
    || launchctl bootout "$DOMAIN" "$DEST" \
    || true
fi
rm -f "$DEST"

if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  echo "卸载未完成，${LABEL} 仍在运行" >&2
  exit 1
fi

echo "已卸载 ${LABEL}"
echo "数据仍留在 ${HOME}/VoiceInbox"
echo "确认不需要后可以手动删除：rm -rf \"${HOME}/VoiceInbox\""
