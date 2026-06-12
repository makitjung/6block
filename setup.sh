#!/bin/bash
# 새 맥에서 6block 을 1회 부트스트랩 (.venv + 패키지 + .env + launchd 등록)
set -e
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "[1/4] .venv 생성"
if [ ! -d .venv ]; then
    (python3.13 -m venv .venv 2>/dev/null || python3 -m venv .venv)
fi
./.venv/bin/python -m pip install --quiet --upgrade pip

echo "[2/4] 패키지 설치 (requirements.txt)"
./.venv/bin/python -m pip install -r requirements.txt

echo "[3/4] .env 준비"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  .env 생성됨. GCAL_ICAL_URL 등 실제 값과 SIXBLOCK_CLOUD_DIR(비우면 자동탐지)을 채우세요."
else
    echo "  .env 이미 존재 (건너뜀)"
fi

echo "[4/4] launchd 등록"
bash install_launchd.sh

echo ""
echo "완료. 코드=이 폴더, 라이브 데이터=~/6block-data, 오프사이트 백업=OneDrive(AI_data/6block)."
