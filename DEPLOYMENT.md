# Deployment

This app is designed for local Docker development and production hosting on a
managed container service. Docker is not the production host by itself.

## Local Development

1. Copy `.env.example` to `.env` and fill in only the values you need.
2. Start the API and frontend:

   ```bash
   docker compose up --build
   ```

3. Open `http://localhost:5173`.
4. Check the backend health endpoint at `http://localhost:8080/health`.

The app works without AI keys in demo mode. Real API keys stay in `.env` or the
host dashboard and are never exposed through Vite/browser code.

## Production Host: Render

`render.yaml` defines one Docker web service. The container builds the Svelte
frontend, serves it from Flask, exposes `/analyze`, and persists uploaded images
on a mounted disk at `/data/uploads`.

1. Push this branch to GitHub.
2. In Render, choose **New +** then **Blueprint**.
3. Connect the GitHub repository and select the branch that contains this PR.
4. Render detects `render.yaml`. Create the `hht-catalog` service.
5. When prompted for `sync: false` environment variables, enter real values only
   in Render. Leave optional providers blank if unused.
6. Confirm the disk from `render.yaml` is attached:
   - Name: `hht-catalog-data`
   - Mount path: `/data`
   - Size: `1 GB` or larger
7. Deploy the service.
8. Open the Render URL from a phone or another computer while the laptop is off.
9. Verify `https://YOUR-RENDER-HOST/health` returns `{"status":"ok", ...}`.
10. Upload an image in the browser and confirm the listing draft is editable.

## Environment Variables

Set these exact names in the production host. Do not commit real values.

```text
PORT=8080
CORS_ORIGINS=*
LOCAL_UPLOAD_DIR=/data/uploads
SAVE_UPLOADS=true
MAX_UPLOAD_MB=10
GUNICORN_WORKERS=2
GUNICORN_TIMEOUT=120
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_KEY=
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_API_VERSION=2024-08-01-preview
DO_SPACES_KEY=
DO_SPACES_SECRET=
DO_SPACES_REGION=nyc3
DO_SPACES_BUCKET=
APPWRITE_ENDPOINT=https://cloud.appwrite.io/v1
APPWRITE_PROJECT_ID=
APPWRITE_API_KEY=
APPWRITE_DATABASE_ID=default
APPWRITE_COLLECTION_ID=inventory
```

## Persistence

For hosted storage, keep the Render disk mounted at `/data` and set:

```text
LOCAL_UPLOAD_DIR=/data/uploads
SAVE_UPLOADS=true
```

The server creates `/data/uploads` automatically. Optional DigitalOcean Spaces
and Appwrite variables can also be set when you want public image URLs or
inventory document persistence, but marketplace publishing is intentionally not
implemented. Drafts must be reviewed before copy or export.
