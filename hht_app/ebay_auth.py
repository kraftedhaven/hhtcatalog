import base64
import os
import time
from typing import Any
from urllib.parse import urlencode

import requests


DEFAULT_EBAY_USER_SCOPES = [
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
]
TOKEN_CACHE_SKEW_SECONDS = 60
DEFAULT_TIMEOUT_SECONDS = 8.0

_token_cache: dict[str, Any] = {"access_token": "", "expires_at": 0.0, "environment": "", "refresh_token": ""}


class EbayAuthError(RuntimeError):
    def __init__(self, status_code: int, category: str, message: str, code: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.category = category
        self.safe_message = message
        self.code = code

    def to_public(self) -> dict[str, Any]:
        return {
            "provider": "ebay_oauth",
            "status": self.status_code,
            "category": self.category,
            "retryable": self.status_code in {429, 500, 502, 503, 504},
            "message": self.safe_message,
            "code": self.code,
        }


def ebay_authorization_url(state: str | None = None) -> str:
    client_id = _client_id()
    redirect_uri = _oauth_redirect_uri()
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(_user_scopes()),
    }
    if state:
        query["state"] = state
    return f"{_auth_base_url()}/oauth2/authorize?{urlencode(query)}"


def exchange_authorization_code(code: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    code = str(code or "").strip()
    if not code:
        raise EbayAuthError(400, "invalid_request", "Missing eBay authorization code.")
    response = _post_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _oauth_redirect_uri(),
        },
        timeout,
    )
    body = _token_body(response)
    refresh_token = str(body.get("refresh_token") or "")
    access_token = str(body.get("access_token") or "")
    if not refresh_token or not access_token:
        raise EbayAuthError(502, "malformed_json", "eBay OAuth did not return the expected tokens.")
    _cache_access_token(access_token, int(body.get("expires_in") or 0), refresh_token)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": int(body.get("expires_in") or 0),
        "token_type": str(body.get("token_type") or "User Access Token"),
    }


def seller_access_token(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    refresh_token = _refresh_token()
    now = time.monotonic()
    if (
        _token_cache["access_token"]
        and _token_cache["environment"] == _environment()
        and _token_cache["refresh_token"] == refresh_token
        and float(_token_cache["expires_at"]) > now
    ):
        return str(_token_cache["access_token"])
    return refresh_seller_access_token(timeout)


def refresh_seller_access_token(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    refresh_token = _refresh_token()
    response = _post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(_user_scopes()),
        },
        timeout,
    )
    body = _token_body(response)
    access_token = str(body.get("access_token") or "")
    if not access_token:
        raise EbayAuthError(502, "malformed_json", "eBay OAuth did not return an access token.")
    _cache_access_token(access_token, int(body.get("expires_in") or 0), refresh_token)
    return access_token


def clear_seller_token_cache() -> None:
    _token_cache.update({"access_token": "", "expires_at": 0.0, "environment": "", "refresh_token": ""})


def _post_token(data: dict[str, str], timeout: float) -> requests.Response:
    try:
        response = requests.post(
            f"{_api_base_url()}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {_basic_credentials()}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=data,
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise EbayAuthError(504, "timeout", "eBay OAuth request timed out.") from exc
    except requests.RequestException as exc:
        raise EbayAuthError(502, "transport", "eBay OAuth request failed.") from exc
    if response.status_code >= 400:
        code, detail = _oauth_error_info(response)
        raise EbayAuthError(response.status_code, _category_for_status(response.status_code), _safe_error_message(response.status_code, code, detail), code)
    return response


def _token_body(response: requests.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise EbayAuthError(502, "malformed_json", "eBay OAuth returned invalid JSON.") from exc
    if not isinstance(body, dict):
        raise EbayAuthError(502, "malformed_json", "eBay OAuth returned an unexpected response.")
    return body


def _cache_access_token(access_token: str, expires_in: int, refresh_token: str) -> None:
    _token_cache.update({
        "access_token": access_token,
        "expires_at": time.monotonic() + max(0, expires_in - TOKEN_CACHE_SKEW_SECONDS),
        "environment": _environment(),
        "refresh_token": refresh_token,
    })


def _client_id() -> str:
    return _required_env("EBAY_CLIENT_ID")


def _client_secret() -> str:
    return _required_env("EBAY_CLIENT_SECRET")


def _oauth_redirect_uri() -> str:
    return _optional_env("EBAY_RUNAME") or _required_env("EBAY_REDIRECT_URI")


def _refresh_token() -> str:
    return _required_env("EBAY_REFRESH_TOKEN")


def _required_env(name: str) -> str:
    value = _optional_env(name)
    if not value:
        raise EbayAuthError(503, "configuration", f"{name} is not configured.")
    return value


def _optional_env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _basic_credentials() -> str:
    return base64.b64encode(f"{_client_id()}:{_client_secret()}".encode("utf-8")).decode("ascii")


def _user_scopes() -> list[str]:
    configured = os.environ.get("EBAY_USER_SCOPES", "").strip()
    return configured.split() if configured else DEFAULT_EBAY_USER_SCOPES


def _environment() -> str:
    return os.environ.get("EBAY_ENVIRONMENT", "production").strip().lower()


def _api_base_url() -> str:
    return "https://api.sandbox.ebay.com" if _environment() == "sandbox" else "https://api.ebay.com"


def _auth_base_url() -> str:
    return "https://auth.sandbox.ebay.com" if _environment() == "sandbox" else "https://auth.ebay.com"


def _category_for_status(status_code: int) -> str:
    if status_code in {401, 403}:
        return "authentication"
    if status_code == 429:
        return "rate_limit"
    if status_code >= 500:
        return "server_error"
    return "request_error"


def _oauth_error_info(response: requests.Response) -> tuple[str, str]:
    try:
        body = response.json()
    except ValueError:
        return "", ""
    if not isinstance(body, dict):
        return "", ""
    code = str(body.get("error") or body.get("errorId") or "")[:32]
    detail = str(body.get("error_description") or body.get("message") or "")[:160]
    return code, detail


def _safe_error_message(status_code: int, code: str, detail: str) -> str:
    suffix = f" ({code})." if code else "."
    if status_code in {401, 403}:
        return f"eBay OAuth authentication failed{suffix}"
    if status_code == 429:
        return f"eBay OAuth rate limit reached{suffix}"
    if status_code >= 500:
        return f"eBay OAuth service failed{suffix}"
    return f"eBay OAuth request was rejected{suffix}"
