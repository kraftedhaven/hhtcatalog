# HHT Worker Sandbox Setup - Optimized for $250 Credit
# Windows PowerShell - Fresh Sandbox Creation & Configuration

Write-Host ""
Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  HHT Docker Sandbox - Optimized Setup                     ║" -ForegroundColor Cyan
Write-Host "║  Budget: $250 credit                                       ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Step 1: Create fresh sandbox
Write-Host "STEP 1: Creating fresh sandbox with 24-hour lifetime..." -ForegroundColor Yellow
Write-Host ""
Write-Host "Run this command:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  sbx run --lifetime 24h --name hht-worker-prod claude" -ForegroundColor Green
Write-Host ""
Write-Host "This will:" -ForegroundColor Gray
Write-Host "  • Create a new sandbox (fresh token/auth)" -ForegroundColor Gray
Write-Host "  • Set 24-hour expiration (enough for pilot)" -ForegroundColor Gray
Write-Host "  • Name it 'hht-worker-prod' for reference" -ForegroundColor Gray
Write-Host ""

Read-Host "Run that command in PowerShell, then press Enter here to continue..."

Write-Host ""
Write-Host "STEP 2: Verify sandbox is running..." -ForegroundColor Yellow
Write-Host ""
Write-Host "Run this to check:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  sbx ls" -ForegroundColor Green
Write-Host ""
Write-Host "You should see:" -ForegroundColor Gray
Write-Host "  hht-worker-prod    claude    running    /path/to/workspace" -ForegroundColor Gray
Write-Host ""

Read-Host "Check that, then press Enter..."

Write-Host ""
Write-Host "STEP 3: Connect to sandbox shell..." -ForegroundColor Yellow
Write-Host ""
Write-Host "Run this:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  sbx run --name hht-worker-prod" -ForegroundColor Green
Write-Host ""
Write-Host "You'll see the Linux prompt:" -ForegroundColor Gray
Write-Host "  agent@sbx-xxxxxxxxxxxx:~\$" -ForegroundColor Gray
Write-Host ""
Write-Host "⚠️  IMPORTANT: Use only the Linux prompt from here on" -ForegroundColor Red
Write-Host "Do NOT paste 'agent@sbx-...' as a command" -ForegroundColor Red
Write-Host ""

Read-Host "Connect to sandbox, then press Enter..."

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "Now run these commands INSIDE the sandbox (at agent@sbx-...# prompt):" -ForegroundColor Yellow
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

$commands = @(
    @{
        desc = "Clone/enter HHT repo"
        cmd = "cd ~/workspace/hhtcatalog"
    },
    @{
        desc = "Activate Python environment"
        cmd = "source .venv/bin/activate"
    },
    @{
        desc = "Create secure config directory"
        cmd = "mkdir -p ~/.config/hht && chmod 700 ~/.config/hht"
    },
    @{
        desc = "Create worker.env with mode 600"
        cmd = "cat > ~/.config/hht/worker.env << 'EOF'
# Paste your actual credentials below (one per line)
DATABASE_URL=postgresql://...
EBAY_REFRESH_TOKEN=...
GROQ_API_KEY=...
EBAY_MUTATIONS_ENABLED=false
EBAY_DRAFTS_ENABLED=true
EBAY_DRAFT_MIN_ITEMS=3
EBAY_ENVIRONMENT=production
EOF
chmod 600 ~/.config/hht/worker.env"
    },
    @{
        desc = "Load environment into this session"
        cmd = "export `$(cat ~/.config/hht/worker.env | xargs)"
    },
    @{
        desc = "Test PostgreSQL connection"
        cmd = "python3 -c ""import psycopg2; psycopg2.connect(os.getenv('DATABASE_URL')); print('✓ DB OK')"""
    },
    @{
        desc = "Verify Flask app loads"
        cmd = "python3 -c ""from hht_app import create_app; create_app(); print('✓ Flask OK')"""
    },
    @{
        desc = "Start worker process"
        cmd = "python worker.py"
    }
)

$step = 1
foreach ($cmd in $commands) {
    Write-Host "$step. $($cmd.desc):" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  $($cmd.cmd)" -ForegroundColor Green
    Write-Host ""
    $step++
}

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Yellow
Write-Host "HEALTH CHECKS (while worker runs):" -ForegroundColor Yellow
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Yellow
Write-Host ""

Write-Host "Worker is HEALTHY when:" -ForegroundColor Green
Write-Host "  ✓ Process stays alive after 30 seconds" -ForegroundColor Gray
Write-Host "  ✓ No 'Commerce Agent PostgreSQL connection failed'" -ForegroundColor Gray
Write-Host "  ✓ No 'Worker loop failed; retrying' repeating" -ForegroundColor Gray
Write-Host "  ✓ Logs show job status updates" -ForegroundColor Gray
Write-Host ""

Write-Host "Check logs in separate terminal:" -ForegroundColor Cyan
Write-Host "  tail -f /tmp/hht-worker.log" -ForegroundColor Green
Write-Host ""

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "SANDBOX LIFETIME & RENEWAL:" -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

Write-Host "Current sandbox expires in: 24 hours" -ForegroundColor Yellow
Write-Host ""
Write-Host "If you need more time during pilot:" -ForegroundColor Cyan
Write-Host "  1. From host PowerShell (outside sandbox):" -ForegroundColor Gray
Write-Host "     sbx stop hht-worker-prod" -ForegroundColor Green
Write-Host "  2. Create new sandbox:" -ForegroundColor Gray
Write-Host "     sbx run --lifetime 48h --name hht-worker-prod-extended claude" -ForegroundColor Green
Write-Host "  3. Copy worker.env and restart" -ForegroundColor Gray
Write-Host ""

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "PILOT SEQUENCE:" -ForegroundColor Green
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""

$pilotSteps = @(
    "Worker running → accepts queued jobs",
    "One job moves from 'queued·0%' to 'running' then 'complete'",
    "Listing result displayed for review",
    "Photo URLs verified (HTTPS 200 OK)",
    "Select 3 unique, approved items",
    "Submit Seller Hub draft feed (3-item batch)",
    "eBay returns task ID & success count",
    "Verify 3 drafts appear in Seller Hub",
    "No live listings published ✓"
)

$pilotNum = 1
foreach ($step in $pilotSteps) {
    Write-Host "$pilotNum. $step" -ForegroundColor Gray
    $pilotNum++
}

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "COST ESTIMATE (from $250 credit):" -ForegroundColor Cyan
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

Write-Host "Sandbox compute (24h): ~$8-12" -ForegroundColor Gray
Write-Host "Claude agent (queries): ~$2-5" -ForegroundColor Gray
Write-Host "Network/storage: ~$1-2" -ForegroundColor Gray
Write-Host "───────────────────────" -ForegroundColor Gray
Write-Host "Estimated pilot cost: ~$15-20" -ForegroundColor Green
Write-Host "Remaining from $250: ~$230-235" -ForegroundColor Green
Write-Host ""

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Yellow
Write-Host "Ready? Start with: sbx run --lifetime 24h --name hht-worker-prod claude" -ForegroundColor Yellow
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Yellow
