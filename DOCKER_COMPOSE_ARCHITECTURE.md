# HHT Catalog: Local Docker Compose Architecture Proposal

## Overview

This proposal extends the current single-container setup to a production-grade, multi-service architecture supporting:
- **Stateless API** (Flask + Gunicorn)
- **Persistent database** (PostgreSQL / Supabase-compatible)
- **Job queue & worker** (Redis + Celery)
- **Background analysis worker** (image analysis, eBay pricing)
- **Optional GPU worker** (NVIDIA CUDA for computer vision)
- **CPU fallback** (mock/lightweight analysis)
- **eBay credential isolation** (no secrets in images)
- **Bounded concurrency** (explicit approval for all mutations)

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    Reverse Proxy / Load Balancer                │
└──────────────┬──────────────────────────────────────────────────┘
               │
        ┌──────┴──────┬─────────────┬────────────────┐
        │             │             │                │
   ┌────▼────┐   ┌───▼───┐   ┌───▼────┐   ┌───────▼──┐
   │  Flask  │   │ Redis │   │ Postgres│   │ Celery  │
   │   API   │   │  7.0  │   │ 15.x   │   │ Worker  │
   │ :8080   │   │ :6379 │   │ :5432  │   │ (bg)    │
   └────┬────┘   └───────┘   └────────┘   └──────────┘
        │                            │
   [uploads]                    [hht_db]
        │                            │
        └────────────┬───────────────┘
                     │
            ┌────────▼────────┐
            │  Optional GPU   │
            │  NVIDIA Worker  │
            │  (gpu, model)   │
            └─────────────────┘
```

---

## Service Definitions

### 1. **hht-api** (Flask Application)
**Purpose:** HTTP server, authentication, request validation, eBay OAuth.

**Environment:**
```yaml
services:
  hht-api:
    build:
      context: .
      dockerfile: Dockerfile
      target: production
    container_name: hht-api
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      # Core app
      PORT: "8080"
      GUNICORN_WORKERS: "2"
      GUNICORN_TIMEOUT: "120"
      
      # Database (PostgreSQL / Supabase)
      DATABASE_URL: "postgresql://hht_user:hht_pass@postgres:5432/hht_db"
      
      # Redis (job queue & caching)
      REDIS_URL: "redis://redis:6379/0"
      CELERY_BROKER_URL: "redis://redis:6379/1"
      CELERY_RESULT_BACKEND: "redis://redis:6379/2"
      
      # eBay (NEVER in image — env vars only)
      EBAY_CLIENT_ID: "${EBAY_CLIENT_ID}"
      EBAY_CLIENT_SECRET: "${EBAY_CLIENT_SECRET}"
      EBAY_REFRESH_TOKEN: "${EBAY_REFRESH_TOKEN}"
      EBAY_ENVIRONMENT: "production"
      EBAY_MARKETPLACE_ID: "EBAY_US"
      EBAY_MERCHANT_LOCATION_KEY: "${EBAY_MERCHANT_LOCATION_KEY}"
      EBAY_PAYMENT_POLICY_ID: "${EBAY_PAYMENT_POLICY_ID}"
      EBAY_FULFILLMENT_POLICY_ID: "${EBAY_FULFILLMENT_POLICY_ID}"
      EBAY_RETURN_POLICY_ID: "${EBAY_RETURN_POLICY_ID}"
      EBAY_REDIRECT_URI: "http://localhost:8080/api/ebay/oauth/callback"
      
      # Vision providers (optional; mocked if not set)
      PRIMARY_VISION_PROVIDER: "cpu"  # or groq, zai, etc.
      GROQ_API_KEY: "${GROQ_API_KEY:-}"
      GROQ_MODEL: "qwen/qwen3.6-27b"
      
      # Upload & limits
      LOCAL_UPLOAD_DIR: "/data/uploads"
      SAVE_UPLOADS: "true"
      MAX_UPLOAD_MB: "10"
      
      # CORS
      CORS_ORIGINS: "http://localhost:5173"
      
      # Demo/dev
      DEMO_MODE: "false"
    
    volumes:
      - hht_uploads:/data/uploads
    
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5).read()"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s
```

**Key Routes:**
- `POST /analyze` — submits image to queue, returns job ID
- `GET /api/ebay/drafts/{offerId}` — get draft status
- `POST /api/ebay/offers/{offerId}/publish` — requires `confirmPublish: true` flag
- `GET /api/commerce/dashboard` — lists pending approvals from database

---

### 2. **postgres** (PostgreSQL 15)
**Purpose:** Persistent storage for listings, drafts, approvals, audit logs.

**Schema (minimal):**
```yaml
services:
  postgres:
    image: postgres:15-alpine
    container_name: hht-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: "hht_user"
      POSTGRES_PASSWORD: "hht_pass"
      POSTGRES_DB: "hht_db"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "hht_user", "-d", "hht_db"]
      interval: 10s
      timeout: 5s
      retries: 5
```

**Tables:**
```sql
-- Listings (from photo analysis)
CREATE TABLE listings (
    id BIGSERIAL PRIMARY KEY,
    sku VARCHAR(50) UNIQUE,
    title VARCHAR(80) NOT NULL,
    price DECIMAL(10, 2),
    category_id VARCHAR(10),
    condition_id VARCHAR(10),
    ebay_offer_id VARCHAR(64),
    ebay_listing_id VARCHAR(64),
    status VARCHAR(20), -- draft, pending_approval, approved, published, error
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Approvals (explicit human sign-off for eBay mutations)
CREATE TABLE approvals (
    id BIGSERIAL PRIMARY KEY,
    listing_id BIGINT REFERENCES listings(id),
    action VARCHAR(20), -- create_draft, update_offer, publish
    proposed_changes JSONB,
    approved_by VARCHAR(255),
    approved_at TIMESTAMP,
    rejected_at TIMESTAMP,
    rejection_reason TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Analysis jobs
CREATE TABLE analysis_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status VARCHAR(20), -- pending, processing, completed, failed
    input_file_path TEXT,
    output_json JSONB,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

-- Audit log
CREATE TABLE audit_log (
    id BIGSERIAL PRIMARY KEY,
    listing_id BIGINT,
    action VARCHAR(50),
    details JSONB,
    actor VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW()
);

-- eBay token cache (optional; can use Redis instead)
CREATE TABLE ebay_tokens (
    id SMALLINT PRIMARY KEY,
    access_token TEXT,
    expires_at TIMESTAMP,
    refresh_token TEXT,
    updated_at TIMESTAMP DEFAULT NOW()
);
```

---

### 3. **redis** (Redis 7)
**Purpose:** Job queue, caching, token cache, rate-limit state.

```yaml
services:
  redis:
    image: redis:7-alpine
    container_name: hht-redis
    restart: unless-stopped
    command: redis-server --appendonly yes --maxmemory 512mb --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
```

**Usage:**
- **Broker:** `redis://redis:6379/1` (Celery job queue)
- **Result store:** `redis://redis:6379/2` (Celery task results)
- **Cache:** `redis://redis:6379/0` (tokens, rate limits, session data)

---

### 4. **celery-worker** (Background Job Processor)
**Purpose:** Long-running tasks: image analysis, eBay API calls, bulk operations.

```yaml
services:
  celery-worker:
    build:
      context: .
      dockerfile: Dockerfile.worker
      target: cpu  # or gpu; see below
    container_name: hht-celery-worker
    restart: unless-stopped
    environment:
      # Same core env as hht-api (DATABASE_URL, REDIS_URL, eBay vars, etc.)
      DATABASE_URL: "postgresql://hht_user:hht_pass@postgres:5432/hht_db"
      CELERY_BROKER_URL: "redis://redis:6379/1"
      CELERY_RESULT_BACKEND: "redis://redis:6379/2"
      PRIMARY_VISION_PROVIDER: "cpu"
      WORKER_CONCURRENCY: "2"
      
      # eBay secrets (loaded from env, not image)
      EBAY_CLIENT_ID: "${EBAY_CLIENT_ID}"
      EBAY_CLIENT_SECRET: "${EBAY_CLIENT_SECRET}"
      EBAY_REFRESH_TOKEN: "${EBAY_REFRESH_TOKEN}"
    
    volumes:
      - hht_uploads:/data/uploads
      - ./hht_app:/app/hht_app:ro  # Task code
    
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
    
    command: celery -A hht_app.tasks worker -l info --concurrency=2 --max-tasks-per-child=100
```

---

### 5. **celery-worker-gpu** (Optional: GPU-Accelerated Worker)
**Purpose:** Faster image analysis using NVIDIA CUDA for computer vision models.

```yaml
services:
  celery-worker-gpu:
    build:
      context: .
      dockerfile: Dockerfile.worker
      target: gpu
    container_name: hht-celery-worker-gpu
    restart: unless-stopped
    runtime: nvidia
    environment:
      NVIDIA_VISIBLE_DEVICES: "all"
      NVIDIA_DRIVER_CAPABILITIES: "compute,utility"
      CUDA_VISIBLE_DEVICES: "0"
      
      DATABASE_URL: "postgresql://hht_user:hht_pass@postgres:5432/hht_db"
      CELERY_BROKER_URL: "redis://redis:6379/1"
      PRIMARY_VISION_PROVIDER: "gpu"
      WORKER_CONCURRENCY: "1"
      
      EBAY_CLIENT_ID: "${EBAY_CLIENT_ID}"
      EBAY_CLIENT_SECRET: "${EBAY_CLIENT_SECRET}"
      EBAY_REFRESH_TOKEN: "${EBAY_REFRESH_TOKEN}"
    
    volumes:
      - hht_uploads:/data/uploads
      - ./hht_app:/app/hht_app:ro
    
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
    
    command: celery -A hht_app.tasks worker -l info --concurrency=1 -Q gpu_tasks
```

---

## Key Architectural Decisions

### 1. **Credential Management**
- **Never** bake secrets (eBay tokens, API keys) into Docker images.
- Use **environment variables** loaded from `.env` file or CI/CD secrets.
- Store **refresh tokens** in `.env` (local) or Heroku Config Vars (production).
- Store **access tokens** in Redis with TTL or PostgreSQL with expiry check.

**Example `.env.local`:**
```
DATABASE_URL=postgresql://hht_user:hht_pass@postgres:5432/hht_db
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2

EBAY_CLIENT_ID=your_client_id
EBAY_CLIENT_SECRET=your_secret
EBAY_REFRESH_TOKEN=your_refresh_token
EBAY_MERCHANT_LOCATION_KEY=...
EBAY_PAYMENT_POLICY_ID=...
EBAY_FULFILLMENT_POLICY_ID=...
EBAY_RETURN_POLICY_ID=...

GROQ_API_KEY=...
PRIMARY_VISION_PROVIDER=groq
```

---

### 2. **Separation of Concerns**

| Service | Role | Concurrency | Failures |
|---------|------|-------------|----------|
| **hht-api** | HTTP, sync requests, auth | N/A (Gunicorn) | Restart; user sees 500 |
| **celery-worker** | Analysis, eBay drafts | 2 tasks/worker | Retry from queue; audit trail |
| **celery-worker-gpu** | Fast analysis | 1 task (CUDA) | Fall back to CPU worker |
| **postgres** | State, approvals, audit | N/A | PVC persists; reconnect on restart |
| **redis** | Queue, cache, tokens | N/A | In-memory; can rebuild from DB |

**Flow:**
1. Frontend → `/analyze` → API enqueues task
2. Task goes to Redis queue
3. Worker pulls task, calls Groq/Z.AI or CPU fallback
4. Task saves result to DB + Redis
5. API returns status; UI polls `/api/commerce/jobs/{job_id}`
6. User reviews → `/api/commerce/recommendations/{id}/approve`
7. API validates approval flag, queues eBay mutation
8. Mutation worker calls eBay with Bearer token
9. Audit trail saved

---

### 3. **Explicit Approval for eBay Mutations**

All eBay API calls require user confirmation:

```python
# API endpoint: POST /api/ebay/offers/{offerId}/publish
@app.route("/api/ebay/offers/<offer_id>/publish", methods=["POST"])
def ebay_offer_publish(offer_id):
    body = request.get_json(silent=True) or {}
    
    # Fail if `confirmPublish` is not True
    if body.get("confirmPublish") is not True:
        return jsonify({
            "error": "Confirm publish before creating a live eBay listing."
        }), 400
    
    # Load approval record from DB
    approval = db.query(Approval).filter_by(
        listing_id=...,
        action="publish"
    ).first()
    
    if not approval or not approval.approved_at:
        return jsonify({"error": "Approval required."}), 403
    
    # Proceed only after explicit approval
    try:
        result = publish_ebay_offer(offer_id, confirm_publish=True)
    except EbayDraftError as exc:
        db.add(AuditLog(action="publish_failed", details=exc.to_public()))
        db.commit()
        return jsonify({"error": exc.safe_message}), exc.status_code
    
    # Audit success
    db.add(AuditLog(action="published", details=result))
    db.commit()
    return jsonify({"result": result})
```

---

### 4. **Bounded Concurrency**

**API (Flask):**
```yaml
GUNICORN_WORKERS: "2"
GUNICORN_TIMEOUT: "120"
```

**Background (Celery):**
```yaml
# CPU worker: max 2 concurrent tasks
celery worker --concurrency=2

# GPU worker: max 1 (CUDA doesn't parallelize within one GPU well)
celery worker --concurrency=1 -Q gpu_tasks
```

**Database:**
- Connection pool: `max_overflow=5, pool_size=10`

**eBay API:**
- Rate limit aware; retries on 429 with backoff
- Provider cooldown in Redis with TTL

---

### 5. **Fallback Hierarchy**

1. **Primary:** Groq / Z.AI (hosted)
2. **Secondary:** GPU worker (on-premise, slow)
3. **Tertiary:** CPU mock analysis (demo mode)
4. **Final:** User-editable form (manual entry)

```python
# hht_app/tasks.py
@celery_app.task(bind=True, max_retries=2)
def analyze_image_task(self, image_id, fallback_to_cpu=False):
    try:
        if fallback_to_cpu:
            return analyze_cpu(image_id)
        else:
            return analyze_hosted(image_id)  # Groq, Z.AI
    except ProviderError as exc:
        if exc.status_code == 429:  # Rate limited
            # Retry with CPU fallback
            raise self.retry(kwargs={"fallback_to_cpu": True}, countdown=60)
        raise
```

---

## File Structure

```
hhtcatalog/
├── docker-compose.yml          # Multi-service orchestration
├── Dockerfile                   # API image (production target)
├── Dockerfile.worker            # Worker image (cpu/gpu targets)
├── Dockerfile.vision            # (optional) Separate GPU image
├── init.sql                     # PostgreSQL initialization
├── .env.example                 # Template for local .env.local
├── .env.local                   # (gitignored) actual secrets
│
├── hht_app/
│   ├── tasks.py                 # Celery task definitions
│   ├── providers.py             # Vision provider logic
│   ├── ebay_drafts.py           # eBay Inventory API mutations
│   ├── ebay_auth.py             # OAuth token management
│   ├── schema.py                # Data schemas
│   └── ...
│
├── app.py                       # Flask application
├── requirements.txt             # Python dependencies
├── requirements-worker.txt      # (optional) Additional worker deps
│
└── docs/
    ├── ARCHITECTURE.md          # This file
    ├── DEPLOYMENT.md
    └── OPERATIONS.md
```

---

## Docker Compose Files

### `docker-compose.yml` (Local / Development)
```yaml
version: "3.9"

services:
  postgres:
    # ... see above
  
  redis:
    # ... see above
  
  hht-api:
    # ... see above
  
  celery-worker:
    # ... see above
  
  celery-worker-gpu:
    profiles: ["gpu"]  # Optional; run with: docker compose --profile gpu up
  
  hht-frontend:
    image: node:20-alpine
    working_dir: /app/frontend
    command: npm ci && npm run dev -- --host 0.0.0.0
    ports:
      - "5173:5173"
    volumes:
      - ./frontend:/app/frontend
    environment:
      VITE_PUBLIC_API_URL: "http://localhost:8080"
    depends_on:
      hht-api:
        condition: service_healthy

volumes:
  postgres_data:
  redis_data:
  hht_uploads:
  frontend_node_modules:
```

**Run locally:**
```bash
docker compose up -d                    # Start all services
docker compose --profile gpu up -d      # Include GPU worker
docker compose logs -f hht-api          # Watch API logs
docker compose down -v                  # Stop and remove volumes
```

---

## Startup / Initialization

### 1. Create `.env.local`
```bash
cp .env.example .env.local
# Edit .env.local with your eBay credentials
```

### 2. Initialize Database
```bash
docker compose up postgres -d
docker compose exec postgres psql -U hht_user -d hht_db -f /init.sql
```

### 3. Run Migrations (if using Alembic)
```bash
docker compose exec hht-api alembic upgrade head
```

### 4. Start All Services
```bash
docker compose up -d
```

### 5. Health Check
```bash
curl http://localhost:8080/health
curl http://localhost:5173/                    # Frontend
docker compose exec redis redis-cli ping       # Redis
docker compose exec postgres psql -U hht_user -d hht_db -c "SELECT 1"  # DB
```

---

## Monitoring & Debugging

### Logs
```bash
docker compose logs -f hht-api                 # API logs
docker compose logs -f celery-worker           # Worker logs
docker compose logs -f postgres                # DB logs
```

### Database
```bash
docker compose exec postgres psql -U hht_user -d hht_db
# \dt                                          # List tables
# SELECT * FROM listings LIMIT 5;
```

### Redis CLI
```bash
docker compose exec redis redis-cli
> KEYS *                                       # List all keys
> LLEN celery                                  # Queue length
```

### Celery Tasks
```bash
# Inside worker container
docker compose exec celery-worker celery -A hht_app.tasks inspect active
docker compose exec celery-worker celery -A hht_app.tasks inspect stats
```

---

## Production Deployment

### Kubernetes (Recommended)
- Use Helm to deploy HHT API, workers, Postgres, Redis
- Separate GPU nodes for optional `celery-worker-gpu`
- PersistentVolumeClaim for uploads & DB
- Secrets for eBay tokens, API keys

### Docker Swarm
- Use `docker stack deploy` with `docker-compose.yml`
- Restrict eBay task scheduling to specific nodes
- Use external volume plugins for persistence

### Heroku
- API: `Procfile` with `web: gunicorn app:app`
- Workers: `Procfile` with `worker: celery -A hht_app.tasks worker`
- Add-ons: Heroku Postgres, Heroku Redis
- Config Vars: `EBAY_CLIENT_ID`, `EBAY_REFRESH_TOKEN`, etc.

---

## Security Considerations

1. **Never commit `.env.local`** → Add to `.gitignore`
2. **Use env vars for all secrets** → No credentials in code
3. **Limit eBay API scope** → Use `sell.inventory`, not account-wide
4. **Rate-limit user requests** → Prevent abuse
5. **Audit all mutations** → Log to PostgreSQL
6. **Use TLS in production** → Encrypt API ↔ worker communication
7. **Isolate networks** → Workers don't expose ports publicly

---

## Notes

- **State management:** Database (listings, approvals) + Redis (transient cache)
- **Failover:** Workers retry on transient errors; API serves cached data
- **Scaling:** Horizontal scaling of workers; vertical scaling of API on demand
- **Backwards compat:** No breaking changes to existing `/analyze`, `/export/csv` endpoints
