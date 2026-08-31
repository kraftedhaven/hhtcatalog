# HHT Catalog Heroku Deployment

This repository is the canonical Heroku source tree for the merged HHT eBay Listing Builder.
The Flask app serves the built Svelte frontend and the same-origin API.

## Start Command

Heroku uses `heroku.yml` with the Dockerfile web process:

```sh
gunicorn --bind 0.0.0.0:${PORT:-8080} --workers ${GUNICORN_WORKERS:-2} --timeout ${GUNICORN_TIMEOUT:-120} app:app
```

The app binds to `0.0.0.0` and `PORT`.

## Required Config Vars

Set at least one hosted vision provider in Heroku Config Vars. Do not commit real values.

```text
OPENROUTER_API_KEY
OPENROUTER_MODEL
GEMINI_API_KEY
GEMINI_MODEL
GROQ_API_KEY
GROQ_MODEL
DEMO_MODE=false
```

Provider priority is OpenRouter, then Gemini, then Groq. `DEMO_MODE=false` is the production default.
When no provider is configured, `/analyze` returns an actionable error instead of fabricated listing data.
If Gemini credits are exhausted, unset `GEMINI_API_KEY` so the app skips Gemini instead of spending request time on a provider that cannot answer.

Example commands:

```sh
heroku stack:set container -a hht-catalog-b34ed1b32417
heroku config:set OPENROUTER_API_KEY=... OPENROUTER_MODEL=openrouter/free DEMO_MODE=false -a hht-catalog-b34ed1b32417
git push heroku main
```

## API

`POST /analyze` accepts `multipart/form-data` with one to five `file` fields.
Files must be JPEG, PNG, WebP, or GIF and fit under `MAX_UPLOAD_MB`.

`GET /health` returns provider availability booleans and never returns secrets.

`POST /export/csv` accepts:

```json
{
  "items": [],
  "sellerDefaults": {}
}
```

It returns an eBay Seller Hub fixed-price CSV using the exact 35-column header.

## Migration Note

The old Flask demo pipeline, cross-listing CSV, and Azure-specific provider path were replaced by:

- `hht_app/providers.py` for hosted provider calls.
- `hht_app/schema.py` for normalization, seller safeguards, title limits, and CSV generation.
- `frontend/src/App.svelte` for the phone-first eBay queue workflow ported from `hhtmobile-main`.

Rollback point: branch `backup-pre-hhtmobile-merge-20260830`.
