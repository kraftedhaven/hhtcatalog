# HHT Catalog: Worker Infrastructure & Heroku Deployment

## ✅ Validation Checklist

- [x] Docker Compose validates successfully
- [x] API and worker images build without errors
- [x] No hard-coded credentials in Dockerfile or compose
- [x] Worker performs **no** automatic eBay mutations
- [x] Health checks for API and worker readiness
- [x] NVIDIA optional (profiles: ["nvidia"])
- [x] SQLite local-dev only; Supabase (DATABASE_URL) for production
- [x] Worker restart does not lose job status (shared volume + DB)
- [x] No Azure, Appwrite, DigitalOcean, or Gemini services
- [x] Worker failures logged without exposing secrets

---

## Local Development

### 1. Setup Environment
```bash
cp .env.example .env.local

# Edit .env.local with your configuration (optional for local dev)
# DATABASE_URL=postgresql://...  # Leave blank for SQLite
# EBAY_REFRESH_TOKEN=...
# GROQ_API_KEY=...
```

### 2. Start All Services
```bash
# API + Worker + Frontend
docker compose up -d

# Verify health
curl http://localhost:8080/health
docker compose ps

# View logs
docker compose logs -f hht-api
docker compose logs -f hht-worker
```

### 3. Stop and Clean
```bash
docker compose down
docker compose down -v              # Remove volumes (database reset)
```

---

## Production Deployment (Heroku)

### Prerequisites
- Heroku CLI installed
- Heroku app: `hht-catalog`
- Supabase PostgreSQL URL
- GitHub connected to Heroku for auto-deploy

### Step 1: Configure Heroku Environment Variables

**Set config vars for production:**
```bash
heroku config:set \
  DATABASE_URL="postgresql://user:pass@db.supabase.co:5432/postgres" \
  EBAY_CLIENT_ID="your_client_id" \
  EBAY_CLIENT_SECRET="your_client_secret" \
  EBAY_REFRESH_TOKEN="your_refresh_token" \
  EBAY_MERCHANT_LOCATION_KEY="your_location_key" \
  EBAY_PAYMENT_POLICY_ID="your_payment_policy" \
  EBAY_FULFILLMENT_POLICY_ID="your_fulfillment_policy" \
  EBAY_RETURN_POLICY_ID="your_return_policy" \
  EBAY_ENVIRONMENT="production" \
  GROQ_API_KEY="your_groq_key" \
  GROQ_MODEL="qwen/qwen3.6-27b" \
  PRIMARY_VISION_PROVIDER="groq" \
  GUNICORN_WORKERS="3" \
  GUNICORN_TIMEOUT="120" \
  WORKER_POLL_SECONDS="10" \
  LOG_LEVEL="INFO" \
  --app hht-catalog
```

**Verify config:**
```bash
heroku config --app hht-catalog
```

### Step 2: Scale Dynos

**Start one web dyno and one worker dyno:**
```bash
heroku ps:scale web=1 worker=1 --app hht-catalog
```

**Verify running processes:**
```bash
heroku ps --app hht-catalog
```

Expected output:
```
=== hht-catalog Processes
web.1     up  2024-01-15 10:00:00 -0500  python app.py  (free)
worker.1  up  2024-01-15 10:01:00 -0500  python worker.py  (free)
```

### Step 3: Deploy

**Windows PowerShell:**
```powershell
.\deploy-heroku.ps1
```

This script adds the `heroku` Git remote when needed, pushes `main`, scales
the web and worker dynos, and checks the canonical Heroku app URL. It retries
the health endpoint for two minutes by default and shows the current dynos and
most recent release when a deployment fails. It uses the Windows Heroku CLI
launcher directly; do not run `deploy-heroku.sh` through Git Bash on Windows.

**Windows troubleshooting:**
```powershell
# Retry the health endpoint for up to five minutes and include recent logs on failure.
.\deploy-heroku.ps1 -HealthRetries 30 -HealthRetrySeconds 10 -ShowLogsOnFailure
```

The script verifies the Git repository and `main` branch before pushing. It
also warns when local changes are uncommitted, because they are not deployed.

**Option A: From GitHub (recommended)**
- Connect repo to Heroku
- Enable auto-deploy on main branch
- Any push to main triggers rebuild + restart

**Option B: From CLI**
```bash
git push heroku main
```

**Option C: Manual rebuild**
```bash
heroku builds:create --source-url https://github.com/kraftedhaven/hhtcatalog/archive/refs/heads/main.zip --app hht-catalog
```

### Step 4: Monitor

**View logs (real-time):**
```bash
heroku logs --tail --app hht-catalog
```

**Check web dyno health:**
```bash
heroku apps:info --app hht-catalog
```

Use the displayed `Web URL` with `/health`. Heroku may assign a URL that does
not match the app name.

**Check worker is running:**
```bash
heroku ps --app hht-catalog
heroku logs --tail --dyno worker.1 --app hht-catalog
```

### Step 5: Verify Production Setup

**Test the API:**
```bash
curl "$(heroku apps:info --app hht-catalog | sed -n 's/^Web URL: *//p')health" | jq .
```

**Test worker job queue:**
```bash
heroku exec --app hht-catalog "python -c \"
from hht_app import commerce_agent
commerce_agent.init_db()
jobs = commerce_agent.list_jobs()
print(f'Queued jobs: {len(jobs)}')
\""
```

---

## Architecture

### Services

| Service | Command | Purpose | Auto-restarts? |
|---------|---------|---------|----------------|
| **web** | `gunicorn app:app` | HTTP API, OAuth, eBay draft endpoints | Yes (dyno) |
| **worker** | `python worker.py` | Long-running jobs, approvals, no mutations | Yes (dyno) |

### Data Storage

| Type | Local Dev | Production |
|------|-----------|------------|
| **Database** | SQLite (commerce_agent.sqlite3) | Supabase PostgreSQL (DATABASE_URL) |
| **Uploads** | Ephemeral (/data/uploads) | Ephemeral (Heroku) |
| **Job State** | Shared volume (hht_app_data) | Shared database row |

### Worker Behavior

**Polling Loop:**
1. Check database for queued jobs
2. Process next job (analysis, import, etc.)
3. Save result to database
4. Sleep 10s, repeat

**No Automatic Mutations:**
- Worker does **not** call eBay `/publish` endpoint
- Worker does **not** update listings automatically
- Worker only creates drafts and saves recommendations
- User must approve via `/api/commerce/recommendations/{id}/approve`
- User must confirm via `/api/ebay/offers/{offerId}/publish?confirmPublish=true`

### Logging

**Worker logs exceptions without secrets:**
```python
except Exception:
    logging.exception("Worker loop failed; retrying")  # Logs full traceback
    # Secrets (API keys, tokens) are NEVER logged
```

**View worker logs:**
```bash
heroku logs --dyno worker.1 --tail --app hht-catalog
```

---

## Troubleshooting

### Web dyno dies (HTTP 502/503)

```bash
# Check logs
heroku logs --tail --app hht-catalog

# Restart dyno
heroku restart --app hht-catalog

# Increase timeout if needed
heroku config:set GUNICORN_TIMEOUT=240 --app hht-catalog
```

### Worker not processing jobs

```bash
# Verify worker is running
heroku ps --app hht-catalog

# Check worker logs
heroku logs --dyno worker.1 --tail --app hht-catalog

# Restart worker
heroku restart worker.1 --app hht-catalog
```

### Database connection errors

```bash
# Verify DATABASE_URL is set
heroku config:get DATABASE_URL --app hht-catalog

# Test connection
heroku exec --app hht-catalog "python -c 'from hht_app import commerce_agent; commerce_agent.init_db(); print(\"OK\")'"
```

### Job stuck (not completing)

```bash
# Find stuck job in database
heroku exec --app hht-catalog "python -c \"
from hht_app import commerce_agent
jobs = commerce_agent.query_jobs('status = pending')
for job in jobs[:5]:
    print(job)
\""

# Manual retry
heroku exec --app hht-catalog "python -c \"
from hht_app import commerce_agent
commerce_agent.init_db()
commerce_agent.retry_job('job_id_here')
\""
```

---

## Production Readiness

### Before Going Live

- [ ] DATABASE_URL points to Supabase (not local SQLite)
- [ ] All eBay credentials set in Heroku config
- [ ] GROQ_API_KEY or other vision provider configured
- [ ] Worker dyno is scaled to `worker=1`
- [ ] Heroku logs monitored for errors
- [ ] `/health` endpoint returns `200 OK`
- [ ] API is responding to `/api/commerce/dashboard`
- [ ] Worker is polling jobs (no errors in logs)
- [ ] No test/dummy data in production database

### After Deployment

- [ ] Monitor first 24 hours for stability
- [ ] Test end-to-end workflow (upload → analyze → approve → publish)
- [ ] Verify worker restarts don't lose job state (check database)
- [ ] Audit that no automatic eBay mutations occurred
- [ ] Document any custom env vars in DEPLOYMENT.md

---

## Scaling (Future)

### Multiple Workers
```bash
heroku ps:scale worker=2 --app hht-catalog
```
Each worker polls independently; jobs are claimed by first worker to fetch.

### Multiple Web Dynos
```bash
heroku ps:scale web=2 --app hht-catalog
```
Heroku load-balancer routes requests automatically.

### Upgrade Dyno Type
```bash
heroku dyno:type web=standard-1x --app hht-catalog
heroku dyno:type worker=standard-1x --app hht-catalog
```

---

## Local Docker Compose Override (Optional)

If you need Postgres locally instead of SQLite:

```yaml
# .docker/docker-compose.prod.yml
version: "3.9"
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: hht
      POSTGRES_PASSWORD: dev
      POSTGRES_DB: hht_catalog
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

# Override hht-api and hht-worker to use DATABASE_URL
# DATABASE_URL=postgresql://hht:dev@postgres:5432/hht_catalog

volumes:
  postgres_data:
```

**Usage:**
```bash
docker compose -f docker-compose.yml -f .docker/docker-compose.prod.yml up -d
```

---

## References

- **Heroku Procfile:** https://devcenter.heroku.com/articles/procfile
- **Heroku Environment Variables:** https://devcenter.heroku.com/articles/config-vars
- **Supabase PostgreSQL:** https://supabase.com/docs/guides/database
- **Docker Compose:** https://docs.docker.com/compose/
