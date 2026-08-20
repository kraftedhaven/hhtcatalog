#!/usr/bin/env bash
set -euo pipefail

# Simple cron wrapper: run the runner immediately, then sleep for INTERVAL seconds and repeat.
INTERVAL=${INTERVAL_SECONDS:-900}
echo "Starting Vision QA cron loop. Interval=${INTERVAL} seconds"
while true; do
  if [ -f /app/scripts/vision_qa_node.js ]; then
    node /app/scripts/vision_qa_node.js || echo "vision_qa_node failed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  elif [ -f /workspaces/hhtcatalog/scripts/vision_qa_node.js ]; then
    node /workspaces/hhtcatalog/scripts/vision_qa_node.js || echo "vision_qa_node failed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  elif [ -f /workspaces/hhtcatalog/scripts/vision_qa.js ]; then
    node /workspaces/hhtcatalog/scripts/vision_qa.js || echo "vision_qa failed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  else
    echo "No runner found in /workspaces/hhtcatalog/scripts"
  fi
  sleep "$INTERVAL"
done
