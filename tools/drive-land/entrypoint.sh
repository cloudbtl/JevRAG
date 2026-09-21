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
printf '%s\n' '[gdrive]' 'type = drive' 'env_auth = true' 'scope = drive.readonly' 'export_formats = docx,xlsx,pptx' > ~/.config/rclone/rclone.conf
drives_json="$(rclone backend drives gdrive: 2>/dev/null || echo '[]')"
echo "$drives_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({"event":"drives","count":len(d),"drives":[{"id":x["id"],"name":x["name"]} for x in d]}, ensure_ascii=False))'
if [ "$MODE" = "inventory" ]; then
  echo "$drives_json" | python3 -c 'import json,sys; [print(x["id"]+"|"+x["name"]) for x in json.load(sys.stdin)]' | while IFS='|' read -r id name; do
    sz="$(rclone size "gdrive,team_drive=$id:" --json 2>/dev/null || echo '{"count":-1,"bytes":-1}')"
    python3 -c 'import json,sys; print(json.dumps({"event":"drive_size","id":sys.argv[1],"name":sys.argv[2],"size":json.loads(sys.argv[3])}, ensure_ascii=False))' "$id" "$name" "$sz"
  done
  if [ "$INCLUDE_SHARED_WITH_ME" = "1" ]; then
    sz="$(rclone size 'gdrive,shared_with_me=true:' --json 2>/dev/null || echo '{}')"
    python3 -c 'import json,sys; print(json.dumps({"event":"shared_with_me","size":json.loads(sys.argv[1])}))' "$sz"
  fi
  exit 0
fi
export RCLONE=rclone
echo "$drives_json" | python3 -c 'import json,sys; [print(x["id"]+"|"+x["name"]) for x in json.load(sys.stdin)]' | while IFS='|' read -r id name; do
  if [ -n "$DRIVE_IDS" ] && ! echo ",$DRIVE_IDS," | grep -q ",$id,"; then continue; fi
  meta="$(python3 -c 'import json,sys; print(json.dumps({"drive": sys.argv[1], "driveId": sys.argv[2]}, ensure_ascii=False))' "$name" "$id")"
  python3 /app/rclone_land.py --remote "gdrive,team_drive=$id:" --prefix "$name" --source gdrive --metadata "$meta" \
    --state "gs://$STATE_BUCKET/$STATE_PREFIX/land-$id.json" --max-minutes "$MAX_MINUTES_PER_DRIVE" || true
done
if [ "$INCLUDE_SHARED_WITH_ME" = "1" ]; then
  python3 /app/rclone_land.py --remote 'gdrive,shared_with_me=true:' --prefix 'shared-with-me' --source gdrive --metadata '{"drive":"shared-with-me"}' \
    --state "gs://$STATE_BUCKET/$STATE_PREFIX/land-shared-with-me.json" --max-minutes "$MAX_MINUTES_PER_DRIVE" || true
fi
