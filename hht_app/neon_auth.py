"""Verify the branch-scoped JWT issued by Neon Auth."""

import os
from functools import lru_cache
from urllib.parse import urlsplit

import jwt
from flask import g, jsonify, request
from jwt import PyJWKClient


def auth_url():
    url = os.environ.get("NEON_AUTH_URL", "").strip().rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme == "https" and parsed.hostname
            and parsed.hostname.endswith(".neon.tech")
            and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
            and parsed.path.endswith("/auth")):
        return url
    return ""


@lru_cache(maxsize=4)
def _jwks(url):
    return PyJWKClient(f"{url}/.well-known/jwks.json", timeout=5)


def authenticate_request():
    url = auth_url()
    if not url:
        return jsonify(error="Authentication is not configured on this server."), 503

    authorization = request.authorization
    if not authorization or authorization.type != "bearer" or not authorization.token:
        return jsonify(error="Authentication is required."), 401

    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    try:
        key = _jwks(url).get_signing_key_from_jwt(authorization.token)
        claims = jwt.decode(
            authorization.token,
            key.key,
            algorithms=["EdDSA"],
            issuer=origin,
            audience=origin,
            options={"require": ["sub", "exp", "iss", "aud"]},
        )
    except jwt.PyJWKClientConnectionError:
        return jsonify(error="Authentication is temporarily unavailable."), 503
    except jwt.PyJWTError:
        return jsonify(error="Your session is invalid or has expired."), 401

    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        return jsonify(error="Your session is invalid or has expired."), 401
    g.neon_user = claims
    return None
