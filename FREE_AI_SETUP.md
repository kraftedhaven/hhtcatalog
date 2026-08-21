# Free Open-Source AI Setup (no Copilot quota needed)

Korinn — your Codespace ran out of Copilot quota. Here is a **100% free, open-source**
replacement so you can keep coding and running HHT without paying for Copilot.

> Headline: **Autocomplete** = Continue.dev + Qwen2.5-Coder (local, private, free).
> **Chat / code-gen** = Groq free tier (current models listed below). **Total cost: $0.**

## What you actually need (two separate things)

| Need | Free open-source tool | Why |
|------|----------------------|-----|
| AI autocomplete in your editor | **Continue.dev + Qwen2.5-Coder** (via Ollama, local) | Replaces GitHub Copilot autocomplete |
| AI chat / code generation | **Groq free tier** (current models) | Replaces Copilot Chat |

---

## 1. Continue.dev — autocomplete (the Copilot replacement)

Continue is an open-source VS Code / JetBrains extension. Pair it with a free model.

### Option A — Local + private (recommended for autocomplete, no network latency)
Uses **Ollama** to run Qwen2.5-Coder locally. No GPU required; fully private + offline.

1. Install Ollama: <https://ollama.com/download>
2. Pull the model (1.5B is fast on any laptop; 7B if you have 16GB+ RAM):
   ```bash
   ollama pull qwen2.5-coder:1.5b
   # better quality if you have the RAM:
   ollama pull qwen2.5-coder:7b
   ```
3. Install the **Continue** extension in VS Code (search "Continue" in the marketplace).
4. Add to your Continue config (`.continue/config.json` or `.continue/agents/new-config.yaml`):
   ```yaml
   models:
     - name: qwen2.5-coder-1.5b (local)
       provider: ollama
       model: qwen2.5-coder:1.5b
       roles: [autocomplete, chat, edit]
   ```

### Option B — Hosted + fast (no local install)
Uses **Groq's free tier**. Needs a free Groq API key.

1. Free key: <https://console.groq.com/keys>
2. Install the **Continue** extension in VS Code.
3. Add to your Continue config:
   ```yaml
   models:
     - name: llama-3.3-70b (Groq)
       provider: groq
       model: llama-3.3-70b-versatile
       roles: [chat, edit]
       apiKey: ${{ env.GROQ_API_KEY }}
     - name: llama-3.1-8b-instant (Groq, autocomplete)
       provider: groq
       model: llama-3.1-8b-instant
       roles: [autocomplete, chat]
       apiKey: ${{ env.GROQ_API_KEY }}
   ```
4. Put your key in `.env`:
   ```
   GROQ_API_KEY=your_key_here
   ```

> The repo already ships an updated `.continue/agents/new-config.yaml` with these models.

---

## 2. Groq — free chat + code generation (no local install)

Groq hosts open-source models at high speed on a free tier. Use for scaffolding,
debugging, refactoring, generating routes/configs.

- Free key: <https://console.groq.com/keys>
- Current production models (verified Aug 2026):
  - `llama-3.3-70b-versatile` — best all-round quality
  - `qwen-qwq-32b` — coding + reasoning
  - `llama-3.1-8b-instant` — fastest / lowest latency
  - `openai/gpt-oss-120b` — large open model
- Note: Groq **deprecated** `qwen-2.5-coder-32b` (and `mixtral-8x7b`, `gemma-7b-it`)
  — don't use those IDs. The local Ollama `qwen2.5-coder:1.5b` is still current and is
  the best free autocomplete option.

Call Groq directly from a script:
```python
import os, requests
r = requests.post(
    "https://api.groq.com/openai/v1/chat/completions",
    headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
    json={"model": "llama-3.3-70b-versatile",
          "messages": [{"role": "user", "content": "write a fastapi route /health"}]},
)
print(r.json()["choices"][0]["message"]["content"])
```

---

## 3. Running HHT itself — free, no Codespace

You have three free ways to run this project (no Copilot needed at all):

### A. Perplexity Computer (already done)
The dashboard is deployed and live. Upload an image, get SKU + pricing + SEO instantly.
No setup, no quota. (Backend runs in the sandbox; keep this session open to use it.)

### B. Run locally
```bash
git clone https://github.com/kraftedhaven/hhtcatalog
cd hhtcatalog
pip install -r requirements.txt
python app.py            # backend on :8080
cd frontend && npm install && npm run dev   # dashboard on :5173
```
Without any API key it runs in **demo mode**: it extracts **real** dominant colors
from the uploaded image (via Pillow) and generates SKU/pricing/SEO from a curated
vintage catalog. It is **not** true per-image garment recognition until you add
`GEMINI_API_KEY` (real Gemini vision) or `AZURE_OPENAI_*` (Azure Foundry GPT-4o
vision fallback). The vision router tries Gemini -> Azure -> demo automatically.

### C. Deploy to your DigitalOcean droplet
Run with gunicorn (set env vars in the DigitalOcean panel):
```bash
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```
Required for production: `GEMINI_API_KEY` (or `AZURE_OPENAI_*`), and
`DO_SPACES_*` + `APPWRITE_*` if you want image storage + inventory persistence.

---

## Recommended free stack for Korinn

- **Editor:** VS Code (free) + Continue extension (open source)
- **Autocomplete:** Qwen2.5-Coder 1.5B via Ollama (local, private, fast)
- **Chat/code-gen:** Groq free tier (llama-3.3-70b-versatile / qwen-qwq-32b)
- **Vision (HHT):** Gemini API free tier (primary) + Azure Foundry GPT-4o (fallback)
- **Hosting:** DigitalOcean droplet (you already have one) or the deployed preview here

Total cost: $0. No Copilot quota. Everything open source.
