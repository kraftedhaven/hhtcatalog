# Persistent HHT batch worker

This worker is an optional durable execution lane for the existing HHT app. It uses the same Supabase PostgreSQL database as Heroku and never publishes or mutates eBay listings.

## Install on the Cloud Sandbox VM

Run as root on the persistent Ubuntu sandbox:

```bash
sudo HHT_BRANCH=main HHT_APP_DIR=/opt/hhtcatalog \
  bash /path/to/hhtcatalog/deploy/install-persistent-worker.sh
```

The first run creates `/etc/hht-catalog/worker.env` and stops before starting the service. Edit that file on the VM, filling only the environment values below, then rerun the installer:

```text
DATABASE_URL                 Supabase PostgreSQL connection URI; use sslmode=require
GROQ_API_KEY                 existing Groq key
NVIDIA_NIM_BASE_URL          NVIDIA OpenAI-compatible endpoint, if enabled
NVIDIA_NIM_API_KEY           NVIDIA key, if enabled
NVIDIA_CATEGORY_MODEL        configured NVIDIA vision model, if enabled
OPENROUTER_API_KEY           OpenRouter key, if enabled
OPENROUTER_MODEL             configured multimodal fallback model
```

The worker must use the **Supabase Postgres URI**, not the Supabase publishable key or service-role key. Never put secrets in GitHub, this repository, or Heroku source files.

## Start and observe

```bash
sudo systemctl restart hht-worker
sudo systemctl status hht-worker --no-pager
sudo journalctl -u hht-worker -f
```

Expected startup log:

```text
HHT catalog worker started; approval-only mode enabled; concurrency=8 stale_after=900s
```

## Safe defaults

```text
PRIMARY_VISION_PROVIDER=groq
VISION_PROVIDER_FALLBACK_ORDER=nvidia,openrouter,groq
WORKER_CONCURRENCY=8
WORKER_STALE_AFTER_SECONDS=900
EBAY_MUTATIONS_ENABLED=false
EBAY_DRAFTS_ENABLED=false
```

The worker claims jobs atomically in Supabase. If the VM or process dies after a claim, jobs still marked `running` for 15 minutes are returned to `queued` and retried. The worker has a hard concurrency ceiling of 16.

## Updating from the canonical repository

The installer pulls only `https://github.com/kraftedhaven/hhtcatalog.git`, branch `main`, and resets the worker checkout to `origin/main`. It does not touch the user's Windows folders. The canonical local checkout remains:

```text
C:\Users\korin\hhtcatalog-clean
```

Commit and push code from that checkout, then rerun the installer on the persistent VM.
