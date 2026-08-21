# Free Open-Source AI Setup (no Copilot quota needed)

Korinn — your Codespace ran out of Copilot quota. Here is a **100% free, open-source**
replacement so you can keep coding and running HHT without paying for Copilot.

## What you actually need (two separate things)

| Need | Free open-source tool | Why |
|------|----------------------|-----|
| AI autocomplete in your editor | **Continue.dev + Qwen2.5-Coder** | Replaces GitHub Copilot autocomplete |
| AI chat / code generation | **Groq** (free hosted) or **Ollama** (local) | Replaces Copilot Chat |

---

## 1. Continue.dev — autocomplete (the Copilot replacement)

Continue is an open-source VS Code / JetBrains extension. Pair it with a free model.

### Option A — Local + private (no GPU required, runs on your laptop)
Uses **Ollama** to run Qwen2.5-Coder locally. Slower but fully private + offline.

1. Install Ollama: <https://ollama.com/download>
2. Pull the model (1.5B is fast on any laptop; 7B if you have 16GB+ RAM):
   ```bash
   ollama pull qwen2.5-coder:1.5b
   # or for better quality: ollama pull qwen2.5-coder:7b
   ```
3. Install the **Continue** extension in VS Code (search "Continue" in the marketplace).
4. In Continue, add a model:
   ```yaml
   models:
     - name: Qwen Coder (local)
       provider: ollama
       model: qwen2.5-coder:1.5b
       roles: [autocomplete, chat, edit]
   ```

### Option B — Hosted + fast (no local install)
Uses **Groq's free tier** (Qwen + Llama models, very fast). Needs a free Groq API key.

1. Get a free key at <https://console.groq.com/keys>
2. Install the **Continue** extension in VS Code.
3. Add to your Continue config (`.continue/config.json` or the new-config.yaml):
   ```yaml
   models:
     - name: Qwen Coder (Groq)
       provider: groq
       model: qwen2.5-coder-32b-instruct
       roles: [chat, edit]
       apiKey: ${{ env.GROQ_API_KEY }}
     - name: Gemma (Groq, autocomplete)
       provider: groq
       model: gemma2-9b-it
       roles: [autocomplete]
       apiKey: ${{ env.GROQ_API_KEY }}
   ```
4. Put your key in `.env`:
   ```
   GROQ_API_KEY=your_key_here
   ```

> Note: Groq is great for chat/edit. For pure autocomplete latency, the local Ollama
> Qwen2.5-Coder:1.5B (Option A) usually feels snappier because there's no network round-trip.

---

## 2. Groq — free chat + code generation (no local install)

Groq hosts open-source models (Qwen, Llama, Mixtral, Gemma) for free at high speed.
Use it for: generating routes, scaffolding, debugging, refactoring.

- Free key: <https://console.groq.com/keys>
- Free tier limits are generous for solo development.
- Models to try: `qwen2.5-coder-32b-instruct`, `llama-3.3-70b-versatile`, `gemma2-9b-it`.

You can wire Groq directly into Continue (above) OR call it from a script:
```python
import os, requests
r = requests.post(
    "https://api.groq.com/openai/v1/chat/completions",
    headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
    json={"model": "qwen2.5-coder-32b-instruct",
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
Without any API key it runs in **demo mode** (extracts real colors from the image,
generates SKU/pricing/SEO from a curated vintage catalog). Add `GEMINI_API_KEY` for
real AI vision analysis per image.

### C. Deploy to your DigitalOcean droplet
Use `deploy/digitalocean_setup.sh` and `Dockerfile.vision`, or run with gunicorn:
```bash
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```
Set env vars (GEMINI_API_KEY, DO_SPACES_*, APPWRITE_*) in the DigitalOcean panel.

---

## Recommended free stack for Korinn

- **Editor:** VS Code (free) + Continue extension (open source)
- **Autocomplete:** Qwen2.5-Coder 1.5B via Ollama (local, private, fast)
- **Chat/code-gen:** Groq free tier (Qwen2.5-Coder-32B / Llama 3.3 70B)
- **Vision (HHT):** Gemini API free tier, or Groq's LLaVA for image analysis
- **Hosting:** DigitalOcean droplet (already have one) or the deployed preview here

Total cost: $0. No Copilot quota. Everything open source.
