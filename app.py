import os
import json
import boto3
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from appwrite.client import Client
from appwrite.services.databases import Databases

app = Flask(__name__)
CORS(app)

# Configure AI
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")

# Configure DigitalOcean Spaces (S3)
s3_client = boto3.client(
    "s3",
    region_name=os.environ.get("DO_SPACES_REGION", "nyc3"),
    endpoint_url=f"https://{os.environ.get('DO_SPACES_REGION', 'nyc3')}.digitaloceanspaces.com",
    aws_access_key_id=os.environ.get("DO_SPACES_KEY"),
    aws_secret_access_key=os.environ.get("DO_SPACES_SECRET"),
)

# Configure Appwrite
client = Client()
client.set_endpoint(os.environ.get("APPWRITE_ENDPOINT", "https://nyc.cloud.appwrite.io/v1"))
client.set_project(os.environ.get("APPWRITE_PROJECT_ID", "6a7ce37f000ac07f7ca5"))
client.set_key(os.environ.get("APPWRITE_API_KEY"))
databases = Databases(client)

SYSTEM_PROMPT = """
You are an expert vintage and streetwear reseller assistant for Hidden Haven Threads.
Analyze the garment in the image and return ONLY a valid JSON object:
{
  "title": "Optimized eBay Title (Brand, Era, Item, Color, Size)",
  "price": "Suggested resale price float (e.g. 38.00)",
  "condition_id": "3000",
  "category": "eBay Category String (e.g. Men's Vintage Clothing > Sweaters)",
  "description": "Formatted item description with key details, style cues, and estimated sizing."
}
"""

@app.route("/analyze", methods=["POST"])
def analyze_item():
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400
    
    image_file = request.files["image"]
    filename = image_file.filename
    image_bytes = image_file.read()

    # 1. Upload to DigitalOcean Space
    bucket_name = os.environ.get("DO_SPACES_BUCKET")
    s3_client.put_object(
        Bucket=bucket_name,
        Key=filename,
        Body=image_bytes,
        ACL="public-read",
        ContentType=image_file.content_type
    )
    image_url = f"https://{bucket_name}.{os.environ.get('DO_SPACES_REGION')}.digitaloceanspaces.com/{filename}"

    # 2. Run Vision AI Model
    response = model.generate_content([
        SYSTEM_PROMPT,
        {"mime_type": image_file.content_type, "data": image_bytes}
    ])
    
    clean_text = response.text.replace("```json", "").replace("```", "").strip()
    parsed_data = json.loads(clean_text)
    parsed_data["image_url"] = image_url

    # 3. Save to Appwrite inventory table
    doc = databases.create_document(
        database_id="default",
        collection_id="inventory",
        document_id="unique()",
        data={
            "title": parsed_data.get("title", ""),
            "price": str(parsed_data.get("price", "0.00")),
            "condition_id": str(parsed_data.get("condition_id", "3000")),
            "category": parsed_data.get("category", ""),
            "description": parsed_data.get("description", "")
        }
    )

    return jsonify({"success": True, "document": doc, "listing": parsed_data})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
