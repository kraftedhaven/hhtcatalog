"""Request authentication helpers backed by Supabase Auth."""

from __future__ import annotations

import os

import jwt
from jwt import PyJWKClient
from flask import current_app, g, jsonify, request


def authenticate_request():
    """Validate a Supabase access token against the project's issuer and JWKS."""
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if not supabase_url:
        current_app.logger.error("Supabase Auth is not configured")
        return jsonify({"error": "Authentication is not configured on this server."}), 503

    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return jsonify({"error": "Authentication is required."}), 401

    access_token = authorization.removeprefix("Bearer ").strip()
    if not access_token:
        return jsonify({"error": "Authentication is required."}), 401

    try:
        signing_key = PyJWKClient(f"{supabase_url}/auth/v1/.well-known/jwks.json").get_signing_key_from_jwt(access_token)
        user = jwt.decode(
            access_token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
            issuer=f"{supabase_url}/auth/v1",
        )
    except jwt.PyJWTError:
        return jsonify({"error": "Your session is invalid or has expired."}), 401
    except Exception:
        current_app.logger.exception("Supabase JWKS verification failed")
        return jsonify({"error": "Authentication is temporarily unavailable."}), 503

    if not user.get("sub"):
        return jsonify({"error": "Your session is invalid or has expired."}), 401

    g.supabase_user = user
    return None
