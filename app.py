import mimetypes
import os
import html
from typing import Any

from flask import Flask, Response, g, jsonify, request, send_from_directory
from flask_cors import CORS

from hht_app.ebay_auth import EbayAuthError, ebay_authorization_url, exchange_authorization_code, reauthorization_required, required_user_scopes, seller_access_token
from hht_app.ebay_drafts import EbayDraftError, create_ebay_draft, update_ebay_offer
from hht_app.ebay_feed import EbayFeedError, get_feed_result_file, get_feed_task, seller_hub_feed_type, upload_seller_hub_draft_csv
from hht_app.ebay_taxonomy import category_aspects, suggest_category
from hht_app import commerce_agent
from hht_app.providers import ProviderError, UploadedImage, analyze_images, configured_providers, demo_mode
from hht_app.schema import HEADERS, export_ebay_csv, export_ebay_draft_csv, normalize_listing
from hht_app.photo_quality import assess_image
from hht_app.supabase_auth import authenticate_request


PORT = int(os.environ.get("PORT", 8080))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10"))
HEIC_IMAGE_TYPES = {"image/heic", "image/heif"}
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", *HEIC_IMAGE_TYPES}

mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("image/heif", ".heif")

app = Flask(__name__, static_folder="frontend/dist", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
CORS(
    app,
    resources={r"/*": {"origins": os.environ.get("CORS_ORIGINS", "https://hht.ebbiehq.me")}},
    allow_headers=["Content-Type", "Authorization"],
)

PUBLIC_AUTH_PATHS = {"/", "/health", "/api/ebay/oauth/start", "/api/ebay/oauth/callback"}


def _requires_seller_auth() -> bool:
    path = request.path
    # Publishing is intentionally not implemented; its permanent 404 leaks no
    # seller data and cannot reach an eBay mutation function.
    if path.startswith("/api/ebay/offers/") and path.endswith("/publish"):
        return False
    if path in {"/api/commerce/rotation/approve", "/api/commerce/recommendations/bulk-approve", "/api/ebay/drafts", "/api/ebay/draft-feed"}:
        return True
    if path.startswith("/api/commerce/actions/") and path.endswith(("/apply", "/rollback")):
        return True
    if path.startswith("/api/commerce/recommendations/") and request.method == "POST":
        return True
    return path.startswith("/api/ebay/offers/")


@app.before_request
def require_supabase_auth():
    if request.method == "OPTIONS" or request.endpoint == "static":
        return None
    if request.path in PUBLIC_AUTH_PATHS:
        return None
    if not _requires_seller_auth():
        return None
    response = authenticate_request()
    if response is not None:
        return response
    try:
        seller = commerce_agent.ensure_seller_identity(str(g.supabase_user["sub"]))
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    g.seller_id = seller["id"]
    g.auth_user_id = str(g.supabase_user["sub"])
    return None


@app.errorhandler(PermissionError)
def forbidden(error):
    return jsonify({"error": str(error) or "You do not have access to this seller account."}), 403


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "providers": configured_providers(),
        "demo_mode": demo_mode(),
        "csv_columns": len(HEADERS),
        "seller_hub_feed_type": seller_hub_feed_type(),
    })


@app.route("/analyze", methods=["POST"])
def analyze():
    files = _request_files()
    if not files:
        return jsonify({"error": "No image uploaded. Use multipart form field 'file' with one to five images."}), 400
    try:
        images = [_uploaded_image(file) for file in files]
        result = analyze_images(images, {
            "seller_defaults": _seller_defaults_from_form(),
            "try_alternate": _truthy(request.form.get("tryAlternate")),
            "web_request": True,
        })
        return jsonify({"result": result, "provider": result.get("provider"), "demo": result.get("demo", False)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 415
    except ProviderError as exc:
        body: dict[str, Any] = {"error": str(exc.safe_message or exc), "demo": False}
        if exc.failures:
            body["provider_errors"] = exc.failures
            body["providerFailures"] = exc.failures
        if exc.retry_after_seconds is not None:
            body["retry_after_seconds"] = exc.retry_after_seconds
        if exc.can_try_alternate:
            body["can_try_alternate"] = True
            body["alternate_provider"] = exc.alternate_provider
        if exc.all_providers_unavailable:
            body["all_providers_unavailable"] = True
        return jsonify(body), exc.status_code
    except Exception:
        return jsonify({"error": "Analysis failed before a listing could be generated. Please retry or check provider configuration."}), 500


@app.route("/api/nvidia/analyze/start", methods=["POST"])
def nvidia_analyze_start():
    """Queue slow NVIDIA vision work; never hold the browser connection open."""
    files = _request_files()
    if not files:
        return jsonify({"error": "No image uploaded. Use multipart form field 'file' with one to three images."}), 400
    try:
        images = [_uploaded_image(file) for file in files[:3]]
        result = commerce_agent.start_nvidia_vision_job(
            [{"data": image.data, "mimeType": image.mime_type, "filename": image.filename} for image in images],
            _seller_defaults_from_form(),
        )
        result["provider"] = "nvidia_worker"
        result["nextStep"] = "The NVIDIA worker is analyzing the photos. Poll the returned job ID for the reviewed result."
        return jsonify({"result": result}), 202
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("NVIDIA analysis job could not start")
        return jsonify({"error": "NVIDIA analysis could not be queued. Please retry shortly."}), 503


@app.route("/bulk-analyze", methods=["POST"])
def bulk_analyze():
    files = _request_files()
    if not files:
        return jsonify({"error": "No image uploaded. Use multipart form field 'file' or 'files'."}), 400
    item_count = request.form.get("itemCount") or len(files)
    route = commerce_agent.choose_vision_route(item_count, len(files))
    if route["route"] == "nvidia_worker":
        try:
            images = [_uploaded_image(file) for file in files]
            queued = commerce_agent.start_routed_vision_job(
                [{"data": image.data, "mimeType": image.mime_type, "filename": image.filename} for image in images],
                route["itemCount"],
                _seller_defaults_from_form(),
            )
            return jsonify({"route": route, "result": queued, "nextStep": "Poll the returned job ID; heavy analysis is running on the NVIDIA worker."}), 202
        except ValueError as exc:
            return jsonify({"error": str(exc), "route": route}), 400
        except Exception:
            app.logger.exception("Heavy NVIDIA batch could not be queued")
            return jsonify({"error": "Heavy NVIDIA batch could not be queued. Please retry shortly.", "route": route}), 503
    results = []
    for file in files:
        try:
            result = analyze_images([_uploaded_image(file)], {"seller_defaults": _seller_defaults_from_form()})
            results.append({"filename": file.filename, "status": "ok", "result": result})
        except Exception as exc:
            results.append({"filename": file.filename, "status": "error", "error": str(exc)})
    return jsonify({"count": len(results), "route": route, "results": results})


@app.route("/api/photo-quality", methods=["POST"])
def photo_quality():
    files = _request_files()
    if not files:
        return jsonify({"error": "Upload one or more image files."}), 400
    results = []
    for file in files[:5]:
        try:
            results.append(assess_image(file.read(), file.filename or "image"))
        except Exception:
            results.append({"filename": file.filename or "image", "status": "invalid", "score": 0, "issues": ["Photo quality check failed."]})
    return jsonify({"count": len(results), "results": results, "ready": all(item["status"] == "pass" for item in results)})


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
        return jsonify({
            "authorizationUrl": ebay_authorization_url(state),
            "stateRequired": bool(state),
            "requiredScopes": required_user_scopes(),
            "reauthorization": True,
            "nextStep": "Open authorizationUrl, approve eBay access, then Copy the returned EBAY_REFRESH_TOKEN into your host config.",
        })
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
    if request.method == "GET":
        token = html.escape(tokens["refresh_token"])
        body = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>eBay Reconnect Complete</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; padding: 32px; background: #f8fafc; color: #0f172a; }}
    main {{ max-width: 760px; margin: 0 auto; background: white; border: 1px solid #dbe3ef; border-radius: 8px; padding: 24px; }}
    code, textarea {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }}
    textarea {{ width: 100%; min-height: 150px; box-sizing: border-box; margin: 12px 0; padding: 12px; border: 1px solid #cbd5e1; border-radius: 6px; }}
    button {{ background: #0f172a; color: white; border: 0; border-radius: 6px; padding: 10px 14px; cursor: pointer; }}
    .note {{ color: #475569; }}
  </style>
</head>
<body>
  <main>
    <h1>eBay reconnect complete</h1>
    <p>Copy this value into your host config as <code>EBAY_REFRESH_TOKEN</code>, then restart the app.</p>
    <textarea id=\"token\" readonly>{token}</textarea>
    <button onclick=\"navigator.clipboard.writeText(document.getElementById('token').value).then(() => this.textContent = 'Copied')\">Copy refresh token</button>
    <p class=\"note\">Keep this token private. After updating the config var, close this tab.</p>
  </main>
</body>
</html>"""
        return Response(body, mimetype="text/html", headers={"Cache-Control": "no-store"})
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
    return jsonify({"configured": True, "provider": "ebay_oauth", "requiredScopes": required_user_scopes(), "grantedScopesStatus": "unknown", "requiresReauthorization": reauthorization_required(), "reauthorizationMessage": "Reconnect eBay after enabling Analytics/Fulfillment scopes; an existing refresh token cannot gain new scopes." if reauthorization_required() else "The token is configured, but eBay does not expose its granted scope list through this status check. Reauthorize if an Analytics or Fulfillment call returns 401/403."})


@app.route("/api/ebay/categories", methods=["GET"])
def ebay_categories():
    query = str(request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify({"result": {"status": "missing", "suggestions": [], "message": "Enter at least two characters to search eBay categories."}})
    return jsonify({"result": suggest_category(query)})


@app.route("/api/ebay/categories/<category_id>/aspects", methods=["GET"])
def ebay_category_aspects(category_id):
    return jsonify({"result": category_aspects(category_id)})


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


@app.route("/api/ebay/offers/<offer_id>", methods=["PUT"])
def ebay_offer_update(offer_id):
    return jsonify({
        "error": "Direct offer updates are disabled. Use the seller-owned approved-action route so ownership and stale-state checks run before eBay is called."
    }), 403


@app.route("/api/ebay/offers/<path:_removed>/publish", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def ebay_offer_publish_removed(_removed):
    return jsonify({"error": "Not found"}), 404


@app.route("/api/ebay/draft-feed", methods=["POST"])
def ebay_draft_feed():
    body = request.get_json(silent=True) or {}
    items = body.get("items") or body.get("queue") or []
    if not isinstance(items, list):
        return jsonify({"error": "Request body must include an items array."}), 400
    try:
        result = upload_seller_hub_draft_csv(items)
    except EbayFeedError as exc:
        return jsonify({"error": exc.safe_message, "provider_errors": [exc.to_public()]}), exc.status_code
    return jsonify({"result": result})


@app.route("/api/ebay/feed/tasks/<task_id>", methods=["GET"])
def ebay_feed_task(task_id):
    try:
        result = get_feed_task(task_id)
    except EbayFeedError as exc:
        return jsonify({"error": exc.safe_message, "provider_errors": [exc.to_public()]}), exc.status_code
    return jsonify({"result": result})


@app.route("/api/ebay/feed/tasks/<task_id>/result", methods=["GET"])
def ebay_feed_result(task_id):
    try:
        content, content_type, filename = get_feed_result_file(task_id)
    except EbayFeedError as exc:
        return jsonify({"error": exc.safe_message, "provider_errors": [exc.to_public()]}), exc.status_code
    response = app.response_class(content, mimetype=content_type.split(";", 1)[0])
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/commerce/dashboard", methods=["GET"])
def commerce_dashboard():
    try:
        return jsonify({"result": commerce_agent.dashboard(getattr(g, "seller_id", None))})
    except Exception:
        return jsonify({"error": "Commerce Agent database is unavailable. Verify DATABASE_URL and Supabase connectivity."}), 503


@app.route("/api/commerce/listings", methods=["GET"])
def commerce_listings():
    return jsonify({"result": commerce_agent.list_listings({"status": request.args.get("status", "")}, getattr(g, "seller_id", None))})


@app.route("/api/commerce/enrich", methods=["POST"])
def commerce_enrich():
    body = request.get_json(silent=True) or {}
    listing_ids = body.get("listingIds") or body.get("listing_ids") or []
    if not isinstance(listing_ids, list):
        return jsonify({"error": "listingIds must be an array of eBay listing IDs."}), 400
    try:
        return jsonify({"result": commerce_agent.enrich_listings(listing_ids)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("Commerce Agent enrichment failed")
        return jsonify({"error": "Commerce Agent enrichment failed. Check the Heroku logs for the diagnostic."}), 502


@app.route("/api/commerce/enrich/start", methods=["POST"])
def commerce_enrich_start():
    body = request.get_json(silent=True) or {}
    listing_ids = body.get("listingIds") or body.get("listing_ids") or []
    if not isinstance(listing_ids, list):
        return jsonify({"error": "listingIds must be an array of eBay listing IDs."}), 400
    try:
        return jsonify({"result": commerce_agent.start_enrichment_job(listing_ids)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("Commerce Agent enrichment job could not start")
        return jsonify({"error": "Commerce Agent enrichment could not start."}), 503


@app.route("/api/commerce/enrich/full/start", methods=["POST"])
def commerce_enrich_full_start():
    """Queue checkpointed GetItem enrichment for the active local catalog only."""
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"result": commerce_agent.start_full_catalog_enrichment_job(bool(body.get("resumeFailed")))})
    except Exception:
        app.logger.exception("Commerce Agent full enrichment job could not start")
        return jsonify({"error": "Commerce Agent full enrichment could not start."}), 503


@app.route("/api/catalog/enriched", methods=["GET"])
def enriched_catalog():
    """Serve successful read-only enrichment records in fixed review pages."""
    try:
        return jsonify({"result": commerce_agent.enriched_catalog_page(request.args.get("page", 1), request.args.get("pageSize", 25))})
    except Exception:
        app.logger.exception("Enriched catalog page could not be loaded")
        return jsonify({"error": "Enriched catalog records are unavailable."}), 503


@app.route("/api/commerce/import", methods=["POST"])
def commerce_import():
    try:
        return jsonify({"result": commerce_agent.import_listings()})
    except (EbayDraftError, ValueError) as exc:
        body = {"error": getattr(exc, "safe_message", str(exc))}
        if hasattr(exc, "to_public"):
            body["provider_errors"] = [exc.to_public()]
        return jsonify(body), getattr(exc, "status_code", 400)
    except Exception:
        app.logger.exception("Commerce Agent listing import failed")
        return jsonify({"error": "Commerce Agent listing import failed. Check the Heroku logs for the diagnostic."}), 502


@app.route("/api/commerce/import-active", methods=["POST"])
def commerce_import_active():
    try:
        return jsonify({"result": commerce_agent.import_active_listings()})
    except Exception:
        app.logger.exception("Commerce Agent active listing import failed")
        return jsonify({"error": "Active listing import failed. Check the Heroku logs for the diagnostic."}), 502


@app.route("/api/commerce/import-active/start", methods=["POST"])
def commerce_import_active_start():
    try:
        return jsonify({"result": commerce_agent.start_active_import_job()})
    except Exception:
        app.logger.exception("Commerce Agent active listing job could not start")
        return jsonify({"error": "Active listing import could not start."}), 503


@app.route("/api/commerce/performance/sync/start", methods=["POST"])
def commerce_performance_sync_start():
    body = request.get_json(silent=True) or {}
    listing_ids = body.get("listingIds") or body.get("listing_ids")
    if listing_ids is not None and not isinstance(listing_ids, list):
        return jsonify({"error": "listingIds must be an array when supplied."}), 400
    try:
        return jsonify({"result": commerce_agent.start_performance_sync_job(body.get("days", 30), listing_ids)})
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("Commerce performance sync could not start")
        return jsonify({"error": "Performance sync could not start."}), 503


@app.route("/api/commerce/performance", methods=["GET"])
def commerce_performance():
    try:
        return jsonify({"result": commerce_agent.performance_dashboard()})
    except Exception:
        app.logger.exception("Commerce performance dashboard failed")
        return jsonify({"error": "Performance data is unavailable."}), 503


@app.route("/api/commerce/performance/orders/sync/start", methods=["POST"])
def commerce_fulfillment_sync_start():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"result": commerce_agent.start_fulfillment_sync_job(body.get("days", 90))})
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("Commerce fulfillment sync could not start")
        return jsonify({"error": "Fulfillment sync could not start."}), 503


@app.route("/api/commerce/rotation/approve", methods=["POST"])
def commerce_rotation_approve():
    body = request.get_json(silent=True) or {}
    action_ids = body.get("actionIds") or body.get("action_ids") or []
    if not isinstance(action_ids, list):
        return jsonify({"error": "actionIds must be an array."}), 400
    try:
        return jsonify({"result": commerce_agent.approve_rotation_actions(action_ids, g.seller_id, g.auth_user_id)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/commerce/jobs/<job_id>", methods=["GET"])
def commerce_job(job_id):
    job = commerce_agent.active_import_job(job_id)
    if not job:
        return jsonify({"error": "Commerce job not found."}), 404
    result = job.get("result_json", "{}")
    if isinstance(result, str):
        try:
            import json
            result = json.loads(result or "{}")
        except ValueError:
            result = {}
    job["result"] = result
    job.pop("result_json", None)
    return jsonify({"result": job})


@app.route("/api/commerce/audit", methods=["POST"])
def commerce_audit():
    try:
        return jsonify({"result": commerce_agent.audit_all(getattr(g, "seller_id", None))})
    except Exception as exc:
        app.logger.exception("Commerce Agent audit failed")
        return jsonify({"error": "Commerce Agent audit failed. Check the Heroku logs for the diagnostic."}), 502


@app.route("/api/commerce/audit/start", methods=["POST"])
def commerce_audit_start():
    try:
        return jsonify({"result": commerce_agent.start_audit_job()})
    except Exception:
        app.logger.exception("Commerce Agent audit job could not start")
        return jsonify({"error": "Commerce Agent audit could not start."}), 503


@app.route("/api/commerce/recommendations", methods=["GET"])
def commerce_recommendations():
    return jsonify({"result": commerce_agent.recommendations(request.args.get("status", ""), getattr(g, "seller_id", None))})


@app.route("/api/commerce/recommendations/page", methods=["GET"])
def commerce_recommendations_page():
    seller_id = getattr(g, "seller_id", None)
    args = [request.args.get("status", ""), request.args.get("page", 1), request.args.get("pageSize", 25)]
    if seller_id:
        args.append(seller_id)
    return jsonify({"result": commerce_agent.recommendations_page(
        *args
    )})


@app.route("/api/commerce/recommendations/<recommendation_id>", methods=["GET"])
def commerce_recommendation(recommendation_id):
    result = commerce_agent.get_recommendation(recommendation_id, getattr(g, "seller_id", None))
    if not result:
        return jsonify({"error": "Recommendation not found."}), 404
    return jsonify({"result": result})


@app.route("/api/commerce/recommendations/<recommendation_id>/explain", methods=["GET"])
def commerce_recommendation_explain(recommendation_id):
    try:
        return jsonify({"result": commerce_agent.explain_recommendation(recommendation_id, getattr(g, "seller_id", None))})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/api/commerce/recommendations/<recommendation_id>/approve", methods=["POST"])
def commerce_approve(recommendation_id):
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"result": commerce_agent.approve_recommendation(
            recommendation_id, body.get("approved"), g.seller_id, g.auth_user_id
        )})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/commerce/recommendations/bulk-approve", methods=["POST"])
def commerce_bulk_approve():
    body = request.get_json(silent=True) or {}
    recommendation_ids = body.get("recommendationIds") or body.get("recommendation_ids") or []
    if not isinstance(recommendation_ids, list):
        return jsonify({"error": "recommendationIds must be an array."}), 400
    try:
        return jsonify({"result": commerce_agent.bulk_approve(recommendation_ids, g.seller_id, g.auth_user_id)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/commerce/recommendations/<recommendation_id>/<decision>", methods=["POST"])
def commerce_recommendation_decision(recommendation_id, decision):
    status = {"reject": "Rejected", "skip": "Skipped"}.get(str(decision).lower())
    if not status:
        return jsonify({"error": "Decision must be reject or skip."}), 400
    try:
        return jsonify({"result": commerce_agent.set_recommendation_status(recommendation_id, status, g.seller_id)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/commerce/actions/<action_id>/apply", methods=["POST"])
def commerce_apply(action_id):
    try:
        return jsonify({"result": commerce_agent.apply_action(action_id, g.seller_id, g.auth_user_id)})
    except (EbayDraftError, ValueError) as exc:
        body = {"error": getattr(exc, "safe_message", str(exc))}
        if hasattr(exc, "to_public"):
            body["provider_errors"] = [exc.to_public()]
        return jsonify(body), getattr(exc, "status_code", 400)


@app.route("/api/commerce/actions/<action_id>/rollback", methods=["POST"])
def commerce_rollback(action_id):
    try:
        return jsonify({"result": commerce_agent.rollback_action(action_id, g.seller_id, g.auth_user_id)})
    except (EbayDraftError, ValueError) as exc:
        body = {"error": getattr(exc, "safe_message", str(exc))}
        if hasattr(exc, "to_public"):
            body["provider_errors"] = [exc.to_public()]
        return jsonify(body), getattr(exc, "status_code", 400)


@app.route("/api/commerce/history", methods=["GET"])
def commerce_history():
    return jsonify({"result": commerce_agent.history(getattr(g, "seller_id", None))})


@app.route("/api/commerce/settings", methods=["GET", "PUT"])
def commerce_settings():
    try:
        result = commerce_agent.update_settings(request.get_json(silent=True) or {}) if request.method == "PUT" else commerce_agent.settings()
        return jsonify({"result": result})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        return jsonify({"error": "Commerce Agent database is unavailable. Verify DATABASE_URL and Supabase connectivity."}), 503


@app.errorhandler(413)
def too_large(_err):
    return jsonify({"error": f"Uploaded image is too large. Limit is {MAX_UPLOAD_MB} MB."}), 413


@app.errorhandler(404)
def not_found(_err):
    path = request.path.rstrip("/") or "/"
    # Serve the Svelte shell only for extensionless client-side routes. Missing
    # files and unknown API endpoints must remain 404s instead of looking valid
    # to scanners probing for credential files.
    server_prefixes = ("/api/", "/analyze/", "/bulk-analyze/", "/export/", "/health/", "/normalize/")
    if path.startswith(server_prefixes) or os.path.splitext(path)[1]:
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
    if not isinstance(parsed, dict):
        return {}
    hints = parsed.get("analysisHints")
    if isinstance(hints, dict):
        allowed = {"brand", "model", "itemType", "category", "searchTerms"}
        parsed["analysisHints"] = {
            key: str(hints.get(key) or "").strip()[:160]
            for key in allowed
            if str(hints.get(key) or "").strip()
        }
    else:
        parsed.pop("analysisHints", None)
    return parsed


def _truthy(raw: str | None) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    print(f"HHT Catalog starting on :{PORT}")
    app.run(host="0.0.0.0", port=PORT)
