#!/bin/bash
# 6block launchd 등록을 모두 해제하고 ~/Library/LaunchAgents 에서 plist 삭제
set -e

PLIST_DST="$HOME/Library/LaunchAgents"

for target in "$PLIST_DST"/io.6block.*.plist; do
    [ -e "$target" ] || continue
    name=$(basename "$target")
    launchctl unload "$target" 2>/dev/null || true
    rm "$target"
    echo "제거: $name"
done

echo "제거 완료."
