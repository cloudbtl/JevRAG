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
: "${LAND_RUN_BASELINE:=0}"   # 0 = 추출은 큐에 넣고 drain 이 처리(대량 이관), 1 = 인라인
EXTRA=""; [ "$LAND_RUN_BASELINE" = "0" ] && EXTRA="--no-baseline"
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
# 병렬 실행: Cloud Run Job 의 task 가 여러 개면 task i 는 DRIVE_IDS 의 i, i+N, i+2N … 번째 드라이브를 맡는다(순서 = 우선순위).
# DRIVE_IDS 가 비면 backend drives 순서 전부. 사진 같은 대용량 드라이브는 DRIVE_IDS 로 빼둘 수 있다.
TASK_INDEX="${CLOUD_RUN_TASK_INDEX:-0}"; TASK_COUNT="${CLOUD_RUN_TASK_COUNT:-1}"
ordered="$(python3 - "$drives_json" "$DRIVE_IDS" <<'PYEOF'
import json, sys
drives = {x["id"]: x["name"] for x in json.loads(sys.argv[1])}
want = [i for i in sys.argv[2].split(",") if i] or list(drives)
for i in want:
    if i in drives:
        print(i + "|" + drives[i])
PYEOF
)"
n=0
echo "$ordered" | while IFS='|' read -r id name; do
  [ -z "$id" ] && continue
  if [ $((n % TASK_COUNT)) -ne "$TASK_INDEX" ]; then n=$((n+1)); continue; fi
  n=$((n+1))
  meta="$(python3 -c 'import json,sys; print(json.dumps({"drive": sys.argv[1], "driveId": sys.argv[2]}, ensure_ascii=False))' "$name" "$id")"
  echo "{"event":"task_drive","task":$TASK_INDEX,"drive":$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1],ensure_ascii=False))' "$name")}"
  python3 /app/rclone_land.py --remote "gdrive,team_drive=$id:" --prefix "$name" --source gdrive --metadata "$meta" \
    --state "gs://$STATE_BUCKET/$STATE_PREFIX/land-$id.json" --max-minutes "$MAX_MINUTES_PER_DRIVE" $EXTRA || true
done
if [ "$INCLUDE_SHARED_WITH_ME" = "1" ] && [ "$TASK_INDEX" = "0" ]; then
  python3 /app/rclone_land.py --remote 'gdrive,shared_with_me=true:' --prefix 'shared-with-me' --source gdrive --metadata '{"drive":"shared-with-me"}' \
    --state "gs://$STATE_BUCKET/$STATE_PREFIX/land-shared-with-me.json" --max-minutes "$MAX_MINUTES_PER_DRIVE" $EXTRA || true
fi
