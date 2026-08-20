HHTCatalog Frontend (Svelte + Vite)
===============================

This frontend is a Svelte + Vite app that uploads an image to a backend `/analyze` endpoint and displays the pipeline results (vision, SKU, pricing, SEO).

Quick start
-----------

1. Copy environment example:

```bash
cp .env.example .env
# set VITE_PUBLIC_API_URL in frontend/.env to your droplet API base (e.g. https://example.com)
```

2. Install and run locally:

```bash
cd frontend
npm install --legacy-peer-deps
npm run dev
```

Production build
----------------

```bash
npm run build
npm run preview
```

Environment variables
---------------------

- `VITE_PUBLIC_API_URL` — base URL for your backend (no trailing slash). The app POSTs to `${VITE_PUBLIC_API_URL}/analyze`.

DigitalOcean App Platform deployment
-----------------------------------

1. Create a new App on the DigitalOcean control panel.
2. Connect your GitHub repository and point the service to this repository and the `frontend` folder as the build context.
3. Set the build command to:

```bash
npm ci && npm run build
```

4. Set the run command (for a preview or static site) to:

```bash
npm run preview
```

5. Add the environment variable `VITE_PUBLIC_API_URL` in the App Platform settings.

Notes
-----
- The frontend expects the `/analyze` endpoint to accept a multipart file upload (`file`) and return JSON. It will attempt to map keys `vision`, `sku`, `pricing`, and `seo` from the response but will render whatever fields are present.
- TailwindCSS is included. Edit `tailwind.config.cjs` to expand `content` if you add more files.
