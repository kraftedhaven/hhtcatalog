#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${HHT_REPO_URL:-https://github.com/kraftedhaven/hhtcatalog.git}"
APP_DIR="${HHT_APP_DIR:-/opt/hhtcatalog}"
ENV_FILE="${HHT_WORKER_ENV_FILE:-/etc/hht-catalog/worker.env}"
BRANCH="${HHT_BRANCH:-main}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this installer as root (sudo)." >&2
  exit 1
fi

command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

getent group hhtworker >/dev/null || groupadd --system hhtworker
id hhtworker >/dev/null 2>&1 || useradd --system --gid hhtworker --home-dir "$APP_DIR" --shell /usr/sbin/nologin hhtworker
mkdir -p "$(dirname "$ENV_FILE")"
chmod 750 "$(dirname "$ENV_FILE")"

if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
  mkdir -p "$(dirname "$APP_DIR")"
  git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$APP_DIR"
fi

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<'ENV'
# HHT persistent worker configuration. Fill values on the VM; never commit this file.
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/postgres?sslmode=require
GROQ_API_KEY=
GROQ_MODEL=qwen/qwen3.8-27b
GROQ_FALLBACK_MODEL=qwen/qwen3.8-27b
NVIDIA_NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_NIM_API_KEY=
NVIDIA_CATEGORY_MODEL=
OPENROUTER_API_KEY=
OPENROUTER_MODEL=nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free
PRIMARY_VISION_PROVIDER=groq
VISION_PROVIDER_FALLBACK_ORDER=nvidia,openrouter,groq
WORKER_POLL_SECONDS=5
WORKER_CONCURRENCY=8
WORKER_STALE_AFTER_SECONDS=900
EBAY_MUTATIONS_ENABLED=false
EBAY_DRAFTS_ENABLED=false
ENV
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE. Fill DATABASE_URL and provider keys, then rerun this installer."
  exit 0
fi

chmod 600 "$ENV_FILE"
chown -R hhtworker:hhtworker "$APP_DIR"
install -m 0644 "$APP_DIR/deploy/hht-worker.service" /etc/systemd/system/hht-worker.service
systemctl daemon-reload
systemctl enable hht-worker.service
systemctl restart hht-worker.service
systemctl --no-pager --full status hht-worker.service || true

echo "Worker installed and restarted. Follow logs with: journalctl -u hht-worker -f"
