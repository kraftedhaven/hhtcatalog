"""Request authentication helpers backed by Supabase Auth."""

from __future__ import annotations

import os

import requests
from flask import current_app, g, jsonify, request


def authenticate_request():
    """Validate the caller's bearer token with Supabase before API access."""
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    publishable_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
    if not supabase_url or not publishable_key:
        current_app.logger.error("Supabase Auth is not configured")
        return jsonify({"error": "Authentication is not configured on this server."}), 503

    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return jsonify({"error": "Authentication is required."}), 401

    access_token = authorization.removeprefix("Bearer ").strip()
    if not access_token:
        return jsonify({"error": "Authentication is required."}), 401

    try:
        response = requests.get(
            f"{supabase_url}/auth/v1/user",
            headers={
                "apikey": publishable_key,
                "Authorization": f"Bearer {access_token}",
            },
            timeout=5,
        )
    except requests.RequestException:
        current_app.logger.exception("Supabase Auth verification failed")
        return jsonify({"error": "Authentication is temporarily unavailable."}), 503

    if response.status_code != 200:
        return jsonify({"error": "Your session is invalid or has expired."}), 401

    try:
        user = response.json()
    except ValueError:
        return jsonify({"error": "Your session is invalid or has expired."}), 401
    if not user.get("id"):
        return jsonify({"error": "Your session is invalid or has expired."}), 401

    g.supabase_user = user
    return None
