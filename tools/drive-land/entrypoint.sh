#!/bin/bash
# MODE=inventory : 공유 드라이브 목록·크기만 출력
# MODE=land      : 공유 드라이브마다 rclone_land.py (상태는 gs://$STATE_BUCKET/$STATE_PREFIX/land-<driveId>.json)
# 인증: rclone drive env_auth=true → Cloud Run 런타임 서비스 계정(ADC). 키 파일 없음.
set -euo pipefail
: "${MODE:=inventory}"
: "${STATE_BUCKET:=ax-apps-storage}"
: "${STATE_PREFIX:=cloudbtl-drive-land}"
: "${MAX_MINUTES_PER_DRIVE:=90}"
: "${DRIVE_IDS:=}"          # 콤마 구분. 비우면 전부.
: "${INCLUDE_SHARED_WITH_ME:=1}"
mkdir -p ~/.config/rclone
cat > ~/.config/rclone/rclone.conf <<EOF
[gdrive]
type = drive
env_auth = true
scope = drive.readonly
export_formats = docx,xlsx,pptx
