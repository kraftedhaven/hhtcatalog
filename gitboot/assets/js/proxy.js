// proxy.js
import express from "express";
import fetch from "node-fetch";
import dotenv from "dotenv";

dotenv.config();
const app = express();
app.use(express.json());

// List of allowed free models
const allowedModels = [
  "mistralai/mistral-7b-instruct:free",
  "huggingfaceh4/zephyr-7b-beta:free",
  "nousresearch/nous-hermes-2-mistral-7b-dpo:free",
  "openchat/openchat-7b:free",
  "gryphe/mythomax-l2-13b:free",
  "undi95/toppy-m-7b:free",
  "lizpreciatior/lzlv-70b-fp16-hf:free",
  "neversleep/noromaid-20b:free",
  "sao10k/fimbulvetr-11b-v2:free",
  "koboldai/llama2-13b-supercot:free"
];

// Proxy endpoint for chat completions
app.post("/chat", async (req, res) => {
  try {
    const { model, messages } = req.body;

    // Validate model
    if (!allowedModels.includes(model)) {
      return res.status(400).json({ error: "Model not allowed. Use a free model from the list." });
    }

    // Forward request to OpenRouter
    const response = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${process.env.OPENROUTER_API_KEY}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ model, messages })
    });

    const data = await response.json();
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Endpoint to list allowed models
app.get("/models", (req, res) => {
  res.json({ models: allowedModels });
});

app.listen(3000, () => console.log("✅ Proxy running on http://localhost:3000"));
