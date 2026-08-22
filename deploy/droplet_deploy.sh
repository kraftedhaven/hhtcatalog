#!/usr/bin/env bash
# ============================================================
#  Hidden Haven Threads — one-shot DigitalOcean droplet deploy
# ============================================================
#  Runs REAL per-image recognition (Gemini / Azure) once you add keys.
#
#  USAGE (from your laptop, or on the droplet itself):
#    ssh root@YOUR_DROPLET_IP 'bash -s' < deploy/droplet_deploy.sh
#  or if the file is already on the droplet:
#    sudo bash deploy/droplet_deploy.sh
#
#  After it finishes: edit /etc/hht/.env and put in your GEMINI_API_KEY,
#  then:  sudo systemctl restart hht-catalog
# ============================================================
set -euo pipefail

REPO="https://github.com/kraftedhaven/hhtcatalog"
APP_DIR="/opt/hht"
ENV_FILE="/etc/hht/.env"
SERVICE="hht-catalog"
PORT=8080

echo ">>> [1/7] Installing system dependencies (python, node, nginx)..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git nodejs npm nginx

echo ">>> [2/7] Cloning / updating repo..."
if [ -d "$APP_DIR" ]; then
  cd "$APP_DIR" && git pull --ff-only
else
  git clone "$REPO" "$APP_DIR" && cd "$APP_DIR"
fi

echo ">>> [3/7] Building backend (python venv + deps)..."
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo ">>> [4/7] Building frontend..."
cd frontend
if [ ! -d node_modules ]; then npm install; fi
printf 'VITE_PUBLIC_API_URL=__PORT_%s__\n' "$PORT" > .env
npm run build
cd "$APP_DIR"

echo ">>> [5/7] Creating env file for your API keys..."
mkdir -p /etc/hht
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'ENV'
# --- real per-image recognition: set AT LEAST ONE (Gemini is cheapest) ---
GEMINI_API_KEY=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_KEY=
AZURE_OPENAI_DEPLOYMENT=gpt-4o
# --- file storage (optional, for image hosting) ---
DO_SPACES_KEY=
DO_SPACES_SECRET=
DO_SPACES_REGION=nyc3
DO_SPACES_BUCKET=
# --- catalog database (optional) ---
APPWRITE_ENDPOINT=
APPWRITE_PROJECT=
APPWRITE_DATABASE_ID=
ENV
  chmod 600 "$ENV_FILE"
  echo "    Created $ENV_FILE  ->  add your GEMINI_API_KEY there, then re-run this script."
fi

echo ">>> [6/7] Writing nginx + systemd config..."
# nginx: serve the SPA, route /port/PORT/ to the backend (mirrors pplx.app routing)
cat > /etc/nginx/sites-available/${SERVICE} <<EOF
server {
    listen 80 default_server;
    root ${APP_DIR}/frontend/dist;
    index index.html;

    # API -> backend on :PORT (frontend calls /port/PORT/...)
    location /port/${PORT}/ {
        proxy_pass http://127.0.0.1:${PORT}/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 90s;
        client_max_body_size 25m;
    }

    location / { try_files \$uri \$uri/ /index.html; }
}
EOF
ln -sf /etc/nginx/sites-available/${SERVICE} /etc/nginx/sites-enabled/${SERVICE}
rm -f /etc/nginx/sites-enabled/default
nginx -t

# systemd: keep the backend alive across reboots
cat > /etc/systemd/system/${SERVICE}.service <<EOF
[Unit]
Description=HHT Catalog (Vision + Listing pipeline)
After=network.target

[Service]
User=root
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/.venv/bin/gunicorn -w 4 -b 127.0.0.1:${PORT} app:app --timeout 60
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo ">>> [7/7] Starting services..."
systemctl daemon-reload
systemctl enable --now ${SERVICE}
systemctl restart ${SERVICE}
systemctl reload nginx

echo ""
echo "============================================================"
echo " DONE.  HHT Catalog is live on your droplet."
echo "   Frontend:  http://YOUR_DROPLET_IP/"
echo "   Health:    curl http://localhost:${PORT}/health"
echo ""
echo " NEXT: put your Gemini key in ${ENV_FILE}, then:"
echo "   sudo systemctl restart ${SERVICE}"
echo " Real per-image recognition will then be active (health shows gemini:true)."
echo "============================================================"
