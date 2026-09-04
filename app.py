import mimetypes
import os
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS

from hht_app.ebay_auth import EbayAuthError, ebay_authorization_url, exchange_authorization_code, seller_access_token
from hht_app.ebay_drafts import EbayDraftError, create_ebay_draft
from hht_app.providers import ProviderError, UploadedImage, analyze_images, configured_providers, demo_mode
from hht_app.schema import HEADERS, export_ebay_csv, export_ebay_draft_csv, normalize_listing


PORT = int(os.environ.get("PORT", 8080))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10"))
HEIC_IMAGE_TYPES = {"image/heic", "image/heif"}
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", *HEIC_IMAGE_TYPES}

mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("image/heif", ".heif")

app = Flask(__name__, static_folder="frontend/dist", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
CORS(app, resources={r"/*": {"origins": os.environ.get("CORS_ORIGINS", "*")}})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "providers": configured_providers(),
        "demo_mode": demo_mode(),
        "csv_columns": len(HEADERS),
    })


@app.route("/analyze", methods=["POST"])
def analyze():
    files = _request_files()
    if not files:
        return jsonify({"error": "No image uploaded. Use multipart form field 'file' with one to five images."}), 400
    try:
        images = [_uploaded_image(file) for file in files]
        result = analyze_images(images, {"seller_defaults": _seller_defaults_from_form()})
        return jsonify({"result": result, "provider": result.get("provider"), "demo": result.get("demo", False)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 415
    except ProviderError as exc:
        body: dict[str, Any] = {"error": "Vision analysis failed" if exc.failures else str(exc), "demo": False}
        if exc.failures:
            body["provider_errors"] = exc.failures
            body["providerFailures"] = exc.failures
        return jsonify(body), exc.status_code
    except Exception:
        return jsonify({"error": "Analysis failed before a listing could be generated. Please retry or check provider configuration."}), 500


@app.route("/bulk-analyze", methods=["POST"])
def bulk_analyze():
    files = _request_files()
    if not files:
        return jsonify({"error": "No image uploaded. Use multipart form field 'file' or 'files'."}), 400
    results = []
    for file in files:
        try:
            result = analyze_images([_uploaded_image(file)], {"seller_defaults": _seller_defaults_from_form()})
            results.append({"filename": file.filename, "status": "ok", "result": result})
        except Exception as exc:
            results.append({"filename": file.filename, "status": "error", "error": str(exc)})
    return jsonify({"count": len(results), "results": results})


@app.route("/export/csv", methods=["POST"])
def export_csv():
    body = request.get_json(silent=True) or {}
    items = body.get("items") or body.get("results") or []
    defaults = body.get("sellerDefaults") or body.get("defaults") or {}
    if not isinstance(items, list):
        return jsonify({"error": "Request body must include an items array."}), 400
    try:
        csv_text = export_ebay_csv(items, defaults)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=hht_ebay_listings.csv"},
    )


@app.route("/export/draft-csv", methods=["POST"])
def export_draft_csv():
    body = request.get_json(silent=True) or {}
    items = body.get("items") or body.get("results") or []
    if not isinstance(items, list):
        return jsonify({"error": "Request body must include an items array."}), 400
    try:
        csv_text = export_ebay_draft_csv(items)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=hht_ebay_draft_template.csv"},
    )


@app.route("/normalize", methods=["POST"])
def normalize():
    body = request.get_json(silent=True) or {}
    return jsonify({"result": normalize_listing(body)})


@app.route("/api/ebay/oauth/start", methods=["GET"])
def ebay_oauth_start():
    try:
        state = os.environ.get("EBAY_AUTH_STATE", "").strip() or None
        return jsonify({"authorizationUrl": ebay_authorization_url(state), "stateRequired": bool(state)})
    except EbayAuthError as exc:
        return jsonify({"error": exc.safe_message, "provider_errors": [exc.to_public()]}), exc.status_code


@app.route("/api/ebay/oauth/callback", methods=["GET", "POST"])
def ebay_oauth_callback():
    payload = request.get_json(silent=True) or {}
    code = payload.get("code") or request.form.get("code") or request.args.get("code")
    state = payload.get("state") or request.form.get("state") or request.args.get("state")
    expected_state = os.environ.get("EBAY_AUTH_STATE", "").strip()
    if expected_state and state != expected_state:
        return jsonify({"error": "Invalid eBay OAuth state."}), 400
    try:
        tokens = exchange_authorization_code(str(code or ""))
    except EbayAuthError as exc:
        return jsonify({"error": exc.safe_message, "provider_errors": [exc.to_public()]}), exc.status_code
    return jsonify({
        "status": "ok",
        "message": "Copy refreshToken into Heroku Config Var EBAY_REFRESH_TOKEN, then remove this setup response from your history.",
        "expiresIn": tokens["expires_in"],
        "tokenType": tokens["token_type"],
        "refreshToken": tokens["refresh_token"],
    })


@app.route("/api/ebay/oauth/status", methods=["GET"])
def ebay_oauth_status():
    try:
        seller_access_token()
    except EbayAuthError as exc:
        return jsonify({"configured": False, "provider_errors": [exc.to_public()]}), exc.status_code
    return jsonify({"configured": True, "provider": "ebay_oauth"})


@app.route("/api/ebay/drafts", methods=["POST"])
def ebay_drafts():
    body = request.get_json(silent=True) or {}
    item = body.get("item") or body.get("listing") or body
    if not isinstance(item, dict):
        return jsonify({"error": "Request body must include an item object."}), 400
    try:
        result = create_ebay_draft(item)
    except EbayDraftError as exc:
        return jsonify({"error": exc.safe_message, "provider_errors": [exc.to_public()]}), exc.status_code
    return jsonify({"result": result})


@app.errorhandler(413)
def too_large(_err):
    return jsonify({"error": f"Uploaded image is too large. Limit is {MAX_UPLOAD_MB} MB."}), 413


@app.errorhandler(404)
def not_found(_err):
    if request.path in {"/analyze", "/bulk-analyze", "/export/csv", "/export/draft-csv", "/health", "/normalize", "/api/ebay/oauth/start", "/api/ebay/oauth/callback", "/api/ebay/oauth/status", "/api/ebay/drafts"}:
        return jsonify({"error": "Not found"}), 404
    return _serve_frontend()


@app.route("/")
def index():
    return _serve_frontend()


def _serve_frontend():
    index_path = os.path.join(app.static_folder or "", "index.html")
    if os.path.exists(index_path):
        return send_from_directory(app.static_folder, "index.html")
    return jsonify({"status": "Backend running", "frontend": "not built"})


def _request_files():
    files = request.files.getlist("file")
    files.extend(request.files.getlist("files"))
    return [file for file in files if file and file.filename]


def _uploaded_image(file) -> UploadedImage:
    mime_type = _mime_type(file)
    if mime_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError(f"Unsupported file type '{file.content_type or file.filename}'. Upload JPEG, PNG, WebP, GIF, or HEIC.")
    data = file.read()
    if not data:
        raise ValueError("Empty image upload.")
    return UploadedImage(data=data, mime_type=mime_type, filename=file.filename or "image.jpg")


def _mime_type(file) -> str:
    if file.content_type in ALLOWED_IMAGE_TYPES:
        return file.content_type
    guessed, _ = mimetypes.guess_type(file.filename or "")
    return guessed or file.content_type or ""


def _seller_defaults_from_form() -> dict[str, str]:
    raw = request.form.get("sellerDefaults")
    if not raw:
        return {}
    import json

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


if __name__ == "__main__":
    print(f"HHT Catalog starting on :{PORT}")
    app.run(host="0.0.0.0", port=PORT)
