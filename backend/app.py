import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from google.genai import Client

app = Flask(__name__)
CORS(app)

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
client = Client(api_key=GEMINI_KEY)

@app.route("/analyze", methods=["POST"])
def analyze():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    image_bytes = file.read()

    try:
        result = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=[
                "Analyze this image and return structured JSON.",
                {
                    "mime_type": "image/jpeg",
                    "data": image_bytes
                }
            ]
        )

        return jsonify({"result": result.text})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/")
def home():
    return jsonify({
        "status": "Backend running",
        "gemini": GEMINI_KEY is not None
    })
