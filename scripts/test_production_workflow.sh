#!/usr/bin/env bash
set -u
BASE="${BASE_URL:-https://hht.ebbiehq.me}"
OUT="${OUT_DIR:-/tmp/hht-production-test-$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$OUT"
run_get() {
  local name="$1" path="$2"
  local headers="$OUT/${name}.headers" body="$OUT/${name}.json"
  local status
  status=$(curl -sS --max-time 60 -D "$headers" -o "$body" -w '%{http_code}' "$BASE$path" || true)
  printf '%-22s HTTP %s  %s\n' "$name" "$status" "$path"
  printf '%s\n' "$status" > "$OUT/${name}.status"
}
run_post_json() {
  local name="$1" path="$2" payload="$3"
  local headers="$OUT/${name}.headers" body="$OUT/${name}.json"
  local status
  status=$(curl -sS --max-time 90 -D "$headers" -o "$body" -w '%{http_code}' -X POST -H 'Content-Type: application/json' --data "$payload" "$BASE$path" || true)
  printf '%-22s HTTP %s  %s\n' "$name" "$status" "$path"
  printf '%s\n' "$status" > "$OUT/${name}.status"
}
run_get health /health
run_get dashboard /api/commerce/dashboard
run_get queue '/api/commerce/recommendations/page?page=1&pageSize=3'
run_get history /api/commerce/history
run_get settings /api/commerce/settings
run_post_json audit_start /api/commerce/audit/start '{}'
if [ -s "$OUT/audit_start.json" ]; then
  job_id=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("jobId", ""))' "$OUT/audit_start.json" 2>/dev/null || true)
  if [ -n "$job_id" ]; then
    sleep 2
    run_get "job_${job_id}" "/api/commerce/jobs/${job_id}"
  fi
fi
printf 'OUTPUT_DIR=%s\n' "$OUT"
