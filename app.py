import os
import uuid
import boto3
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from appwrite.client import Client
from appwrite.services.databases import Databases

app = Flask(__name__)
CORS(app, resources={r"/*": {
    "origins": os.environ.get("CORS_ORIGINS", "*")
}})

# --- 1. CONFIGURATION (From DigitalOcean Environment Variables) ---
GEMINI_KEY = os.environ.get('GEMINI_API_KEY')
DO_SPACES_KEY = os.environ.get('DO_SPACES_KEY')
DO_SPACES_SECRET = os.environ.get('DO_SPACES_SECRET')
DO_SPACES_REGION = os.environ.get('DO_SPACES_REGION', 'nyc3')
DO_SPACES_BUCKET = os.environ.get('DO_SPACES_BUCKET')
# Example endpoint: https://nyc3.digitaloceanspaces.com
DO_SPACES_ENDPOINT = f'https://{DO_SPACES_REGION}.digitaloceanspaces.com'

APPWRITE_ENDPOINT = os.environ.get('APPWRITE_ENDPOINT', 'https://cloud.appwrite.io/v1')
APPWRITE_PROJECT = os.environ.get('APPWRITE_PROJECT_ID')
APPWRITE_KEY = os.environ.get('APPWRITE_API_KEY')
APPWRITE_DB = os.environ.get('APPWRITE_DATABASE_ID', 'default')
APPWRITE_COLL = os.environ.get('APPWRITE_COLLECTION_ID', 'inventory')

# Initialize Clients
genai.configure(api_key=GEMINI_KEY)
session = boto3.session.Session()
s3_client = session.client('s3',
    region_name=DO_SPACES_REGION,
    endpoint_url=DO_SPACES_ENDPOINT,
    aws_access_key_id=DO_SPACES_KEY,
    aws_secret_access_key=DO_SPACES_SECRET
)

appwrite_client = Client()
appwrite_client.set_endpoint(APPWRITE_ENDPOINT).set_project(APPWRITE_PROJECT).set_key(APPWRITE_KEY)
databases = Databases(appwrite_client)

@app.route('/analyze', methods=['POST'])
def analyze():
    uploaded_files = request.files.getlist('file') or request.files.getlist('files')
    if not uploaded_files:
        return jsonify({"error": "No files uploaded"}), 400

    files = uploaded_files
    image_data_list = []
    public_urls = []

    try:
        # STEP 1: Upload to DigitalOcean Spaces
        for file in files:
            file_ext = os.path.splitext(file.filename)[1]
            unique_name = f"uploads/{uuid.uuid4()}{file_ext}"
            
            s3_client.upload_fileobj(
                file, 
                DO_SPACES_BUCKET, 
                unique_name,
                ExtraArgs={'ACL': 'public-read', 'ContentType': file.content_type}
            )
            
            url = f"https://{DO_SPACES_BUCKET}.{DO_SPACES_REGION}.digitaloceanspaces.com/{unique_name}"
            public_urls.append(url)
            
            # Prepare image for Gemini (reset pointer first)
            file.seek(0)
            image_data_list.append({
                "mime_type": file.content_type,
                "data": file.read()
            })

        # STEP 2: Ask Gemini to Analyze
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = "Analyze these vintage garment photos. Return ONLY raw JSON with keys: title, price, condition_id, category, description."
        
        response = model.generate_content([prompt] + image_data_list)
        # Basic cleanup of AI markdown response
        clean_json = response.text.replace('```json', '').replace('```', '').strip()
        analysis = jsonify().get_json() # placeholder
        import json
        analysis = json.loads(clean_json)

        # STEP 3: Save to Appwrite
        item_id = str(uuid.uuid4())[:8]
        doc_data = {
            "title": analysis.get('title'),
            "price": float(analysis.get('price', 0)),
            "category": analysis.get('category'),
            "description": analysis.get('description'),
            "image_urls": public_urls, # Storing array of DO Space links
            "item_id": f"HHT-{item_id}"
        }

        databases.create_document(APPWRITE_DB, APPWRITE_COLL, 'unique()', doc_data)

        # STEP 4: Return result to Bootstrap UI
        return jsonify(analysis)

    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
