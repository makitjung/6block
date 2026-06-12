#!/bin/bash
# 6block launchd plist 전체를 ~/Library/LaunchAgents 에 설치·활성화하고,
# 설치 시점에 __PROJECT_DIR__/__HOME__ 를 현재 경로로 치환한다.
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_SRC="$PROJECT_DIR/launchd"
PLIST_DST="$HOME/Library/LaunchAgents"

mkdir -p "$PLIST_DST"

for plist in "$PLIST_SRC"/io.6block.*.plist; do
    [ -e "$plist" ] || { echo "plist 파일을 찾을 수 없음: $PLIST_SRC"; exit 1; }
    name=$(basename "$plist")
    target="$PLIST_DST/$name"
    sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__HOME__|$HOME|g" "$plist" > "$target"
    launchctl unload "$target" 2>/dev/null || true
    launchctl load "$target"
    echo "활성화: $name"
done

echo ""
echo "설치 완료. 확인: launchctl list | grep 6block"
