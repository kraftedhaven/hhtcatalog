import os
import uuid
import boto3
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from appwrite.client import Client
from appwrite.services.databases import Databases

app = Flask(__name__)
CORS(app)

# Wrap initialization in a function to prevent startup crashes if env vars are missing
def get_clients():
    # Credentials
    gemini_key = os.environ.get('GEMINI_API_KEY')
    aw_project = os.environ.get('APPWRITE_PROJECT_ID')
    aw_key = os.environ.get('APPWRITE_API_KEY')
    
    # Initialize Gemini
    if gemini_key:
        genai.configure(api_key=gemini_key)
    
    # Initialize Appwrite
    client = Client()
    client.set_endpoint(os.environ.get('APPWRITE_ENDPOINT', 'https://cloud.appwrite.io/v1'))
    if aw_project: client.set_project(aw_project)
    if aw_key: client.set_key(aw_key)
    
    return Databases(client)

# Global DB client
try:
    databases = get_clients()
except Exception as e:
    print(f"Warning: Clients not fully initialized: {e}")

@app.route('/', methods=['GET'])
def health_check():
    return "HHT Agent is Online", 200

@app.route('/analyze', methods=['POST'])
def analyze():
    # ... (Keep your existing /analyze logic here)
    pass

if __name__ == '__main__':
    # DO App Platform uses the PORT env var
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
