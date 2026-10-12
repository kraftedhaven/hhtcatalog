#!/bin/bash
# HHT Worker Sandbox Auto-Setup
# Configures Docker Sandbox with Heroku credentials + NVIDIA NIM
# Run inside sandbox after: sbx run --lifetime 24h --name hht-worker-prod claude

set -e

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  HHT Worker Setup - NVIDIA NIM + Groq Fallback            ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Step 1: Navigate to workspace
echo "Step 1: Preparing workspace..."
cd ~/workspace/hhtcatalog || { echo "ERROR: hhtcatalog not found"; exit 1; }
echo "  ✓ In $(pwd)"
echo ""

# Step 2: Activate Python environment
echo "Step 2: Activating Python environment..."
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
    echo "  ✓ Virtual env activated"
else
    echo "  ✗ .venv not found - creating..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    echo "  ✓ Virtual env created and packages installed"
fi
echo ""

# Step 3: Create secure config directory
echo "Step 3: Creating secure config directory..."
mkdir -p ~/.config/hht
chmod 700 ~/.config/hht
echo "  ✓ ~/.config/hht created (mode 700)"
echo ""

# Step 4: Create worker.env with credentials
echo "Step 4: Writing worker.env configuration..."
cat > ~/.config/hht/worker.env << 'ENVEOF'
# HHT Worker Configuration
# Supabase PostgreSQL
DATABASE_URL=postgresql://postgres.rjdhmnacbqelgekpodhb:[REDACTED]@aws-0-us-east-2.pooler.supabase.com:5432/postgres

# eBay OAuth
EBAY_REFRESH_TOKEN=v^1.1#i^1#r^1#f^0#p^3#I^3#t^Ul4xMF85OkE2N0ZCMTQwNzM2NjIzQzgwMUM4MTU0Q0Y5NUNEN0Y1XzJfMSNFXjI2MA==
EBAY_REFRESH_TOKEN_ISSUED_AT=2026-09-29T09:46:26Z
EBAY_ENVIRONMENT=production
EBAY_SANDBOX_MODE=false

# Vision Analysis - NVIDIA NIM Primary (stronger)
NVIDIA_NIM_API_KEY=[REDACTED]

# Vision Analysis - Groq Fallback
GROQ_API_KEY=[REDACTED]
OPENROUTER_API_KEY=[REDACTED]

# eBay Draft Controls (Pilot Mode)
EBAY_MUTATIONS_ENABLED=false
EBAY_DRAFTS_ENABLED=true
EBAY_DRAFT_MIN_ITEMS=3

# Supabase Backend
SUPABASE_URL=https://rjdhmnacbqelgekpodhb.supabase.co
SUPABASE_ANON_KEY=[REDACTED]

# Worker Settings
WORKER_POLL_INTERVAL=5
WORKER_TIMEOUT=300
WORKER_LOG_FILE=/tmp/hht-worker.log
ENVEOF

chmod 600 ~/.config/hht/worker.env
echo "  ✓ worker.env created (mode 600, credentials secured)"
echo ""

# Step 5: Load environment into current shell
echo "Step 5: Loading environment variables..."
export $(cat ~/.config/hht/worker.env | xargs)
echo "  ✓ Environment loaded"
echo ""

# Step 6: Test PostgreSQL connection
echo "Step 6: Testing PostgreSQL connection..."
python3 << 'PYEOF'
import psycopg2
import os
try:
    conn = psycopg2.connect(os.getenv('DATABASE_URL'))
    cursor = conn.cursor()
    cursor.execute("SELECT version();")
    version = cursor.fetchone()
    conn.close()
    print("  ✓ PostgreSQL connection successful")
    print(f"    Server: {version[0].split(',')[0]}")
except Exception as e:
    print(f"  ✗ PostgreSQL connection failed: {e}")
    exit(1)
PYEOF
echo ""

# Step 7: Test Flask app initialization
echo "Step 7: Testing Flask app initialization..."
python3 << 'PYEOF'
import os
os.environ['FLASK_ENV'] = 'production'
try:
    from hht_app import create_app
    app = create_app()
    print("  ✓ Flask app loaded successfully")
    print(f"    Registered blueprints: {len(app.blueprints)}")
except Exception as e:
    print(f"  ✗ Flask app failed to load: {e}")
    exit(1)
PYEOF
echo ""

# Step 8: Verify credentials are loaded
echo "Step 8: Verifying credentials..."
python3 << 'PYEOF'
import os
required = ['DATABASE_URL', 'EBAY_REFRESH_TOKEN', 'NVIDIA_NIM_API_KEY', 'GROQ_API_KEY', 'SUPABASE_URL']
missing = [k for k in required if not os.getenv(k)]
if missing:
    print(f"  ✗ Missing credentials: {', '.join(missing)}")
    exit(1)
else:
    print("  ✓ All required credentials present")
    print(f"    - Database: {os.getenv('DATABASE_URL').split('@')[1][:40]}...")
    print(f"    - eBay token: {os.getenv('EBAY_REFRESH_TOKEN')[:30]}...")
    print(f"    - NVIDIA NIM: available")
    print(f"    - Groq (fallback): available")
    print(f"    - Supabase: {os.getenv('SUPABASE_URL')}")
PYEOF
echo ""

# Step 9: Start worker process
echo "════════════════════════════════════════════════════════════"
echo "Step 9: Starting HHT worker process..."
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Worker starting with:"
echo "  • NVIDIA NIM for vision analysis (primary)"
echo "  • Groq as fallback"
echo "  • eBay draft mode (no live publishing)"
echo "  • Job polling every 5 seconds"
echo ""
echo "Logs: /tmp/hht-worker.log"
echo ""

python worker.py
