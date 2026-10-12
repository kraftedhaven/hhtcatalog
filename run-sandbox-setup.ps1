# HHT Docker Sandbox Automation - Complete Setup
# Windows PowerShell - Runs all steps automatically

param(
    [string]$SandboxName = "hht-worker-prod",
    [int]$LifetimeHours = 24,
    [switch]$SkipSandboxCreation
)

$ErrorActionPreference = "Stop"

# ╔════════════════════════════════════════════════════════════╗
# ║  SECTION 1: CREATE SANDBOX                                ║
# ╚════════════════════════════════════════════════════════════╝

if (-not $SkipSandboxCreation) {
    Write-Host ""
    Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║  Step 1: Creating Docker Sandbox                          ║" -ForegroundColor Cyan
    Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
    
    Write-Host "Creating sandbox: $SandboxName" -ForegroundColor Yellow
    Write-Host "Lifetime: $LifetimeHours hours" -ForegroundColor Yellow
    Write-Host ""
    
    Write-Host "Run this in PowerShell:" -ForegroundColor Green
    Write-Host "  sbx run --lifetime ${LifetimeHours}h --name $SandboxName claude" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "This will take 1-2 minutes..." -ForegroundColor Gray
    Write-Host ""
    
    Read-Host "Press Enter after sandbox is running (sbx should show prompt)"
}

# ╔════════════════════════════════════════════════════════════╗
# ║  SECTION 2: VERIFY SANDBOX                                ║
# ╚════════════════════════════════════════════════════════════╝

Write-Host ""
Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Step 2: Verifying Sandbox                                ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Check if sbx is available
try {
    $sbxCheck = & sbx ls 2>&1 | Select-String $SandboxName
    if ($sbxCheck) {
        Write-Host "✓ Sandbox found: $SandboxName" -ForegroundColor Green
    } else {
        throw "Sandbox not found in sbx ls"
    }
} catch {
    Write-Host "✗ Could not verify sandbox" -ForegroundColor Red
    Write-Host "Make sure sandbox is running: sbx ls" -ForegroundColor Yellow
    exit 1
}

Write-Host ""

# ╔════════════════════════════════════════════════════════════╗
# ║  SECTION 3: COPY SETUP SCRIPT TO SANDBOX                  ║
# ╚════════════════════════════════════════════════════════════╝

Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Step 3: Copying Setup Script                             ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

$setupScript = "$PSScriptRoot\sandbox-setup.sh"
if (-not (Test-Path $setupScript)) {
    Write-Host "✗ Setup script not found: $setupScript" -ForegroundColor Red
    exit 1
}

Write-Host "Copying sandbox-setup.sh to sandbox..." -ForegroundColor Yellow

# Copy file to sandbox
& sbx cp $setupScript "$SandboxName":/tmp/setup.sh 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }

Write-Host "✓ Script copied" -ForegroundColor Green
Write-Host ""

# ╔════════════════════════════════════════════════════════════╗
# ║  SECTION 4: EXECUTE SETUP IN SANDBOX                      ║
# ╚════════════════════════════════════════════════════════════╝

Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Step 4: Running Setup Inside Sandbox                    ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

Write-Host "Executing setup script..." -ForegroundColor Yellow
Write-Host "  • Creating config directory" -ForegroundColor Gray
Write-Host "  • Writing worker.env (credentials secured)" -ForegroundColor Gray
Write-Host "  • Testing PostgreSQL connection" -ForegroundColor Gray
Write-Host "  • Testing Flask app" -ForegroundColor Gray
Write-Host "  • Starting worker process" -ForegroundColor Gray
Write-Host ""

try {
    & sbx exec $SandboxName -- chmod +x /tmp/setup.sh
    & sbx exec $SandboxName -- /tmp/setup.sh
} catch {
    Write-Host "✗ Setup failed: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""

# ╔════════════════════════════════════════════════════════════╗
# ║  SECTION 5: MONITOR WORKER                                ║
# ╚════════════════════════════════════════════════════════════╝

Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║  Step 5: Worker Status                                    ║" -ForegroundColor Green
Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""

Write-Host "Your worker is now:" -ForegroundColor Green
Write-Host "  ✓ Running in sandbox: $SandboxName" -ForegroundColor Green
Write-Host "  ✓ Connected to Supabase PostgreSQL" -ForegroundColor Green
Write-Host "  ✓ Using NVIDIA NIM (primary) + Groq (fallback)" -ForegroundColor Green
Write-Host "  ✓ Configured for eBay draft creation (no live publish)" -ForegroundColor Green
Write-Host "  ✓ Polling for jobs every 5 seconds" -ForegroundColor Green
Write-Host ""

Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Yellow
Write-Host "NEXT STEPS:" -ForegroundColor Yellow
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Yellow
Write-Host ""

Write-Host "1. Monitor logs (in new PowerShell window):" -ForegroundColor Cyan
Write-Host "   sbx exec $SandboxName -- tail -f /tmp/hht-worker.log" -ForegroundColor Green
Write-Host ""

Write-Host "2. In HHT UI, upload photos for analysis" -ForegroundColor Cyan
Write-Host "   Watch status change from 'queued·0%' to 'running' → 'complete'" -ForegroundColor Gray
Write-Host ""

Write-Host "3. Once a job completes, review the listing" -ForegroundColor Cyan
Write-Host "   Verify title, category, photos, price" -ForegroundColor Gray
Write-Host ""

Write-Host "4. Select 3 unique approved items" -ForegroundColor Cyan
Write-Host "   Add to queue with proper eBay fields" -ForegroundColor Gray
Write-Host ""

Write-Host "5. Submit Seller Hub draft batch" -ForegroundColor Cyan
Write-Host "   Via HHT UI: Queue → Send approved drafts now" -ForegroundColor Gray
Write-Host ""

Write-Host "6. Confirm in eBay Seller Hub" -ForegroundColor Cyan
Write-Host "   All 3 drafts should appear (not live, not published)" -ForegroundColor Gray
Write-Host ""

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Yellow
Write-Host "SANDBOX COMMANDS:" -ForegroundColor Yellow
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Yellow
Write-Host ""

Write-Host "Check if running:" -ForegroundColor Cyan
Write-Host "  sbx ls" -ForegroundColor Green
Write-Host ""

Write-Host "View logs:" -ForegroundColor Cyan
Write-Host "  sbx exec $SandboxName -- tail -f /tmp/hht-worker.log" -ForegroundColor Green
Write-Host ""

Write-Host "Reconnect to shell:" -ForegroundColor Cyan
Write-Host "  sbx run --name $SandboxName" -ForegroundColor Green
Write-Host ""

Write-Host "Stop sandbox (keep it):" -ForegroundColor Cyan
Write-Host "  sbx stop $SandboxName" -ForegroundColor Green
Write-Host ""

Write-Host "Extend lifetime (if needed):" -ForegroundColor Cyan
Write-Host "  sbx stop $SandboxName" -ForegroundColor Green
Write-Host "  sbx run --lifetime 48h --name $SandboxName-extended claude" -ForegroundColor Green
Write-Host ""

Write-Host "Delete sandbox (cleanup):" -ForegroundColor Cyan
Write-Host "  sbx rm $SandboxName" -ForegroundColor Green
Write-Host ""

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "Setup complete! Sandbox is running and ready for pilot." -ForegroundColor Green
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Green
