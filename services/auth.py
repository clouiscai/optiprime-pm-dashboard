import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from secrets import compare_digest, token_urlsafe

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


load_dotenv()
security = HTTPBearer(auto_error=False)
TOKEN_PREFIX = "op1"
DEFAULT_ADMIN_TOKEN = "change-this-admin-token"
DEFAULT_VIEWER_TOKEN = "change-this-viewer-token"
DEFAULT_ADMIN_PASSWORD = "change-this-admin-password"
DEFAULT_VIEWER_PASSWORD = "change-this-viewer-password"


def expected_token() -> str:
    return os.getenv("ROBOTX_TOKEN", DEFAULT_ADMIN_TOKEN)


def viewer_token() -> str:
    return os.getenv("OPTIPRIME_VIEWER_TOKEN", DEFAULT_VIEWER_TOKEN)


def expected_username() -> str:
    return os.getenv("OPTIPRIME_USERNAME", "OptiPrime")


def expected_password() -> str:
    return os.getenv("OPTIPRIME_PASSWORD", DEFAULT_ADMIN_PASSWORD)


def viewer_username() -> str:
    return os.getenv("OPTIPRIME_VIEWER_USERNAME", "OptiPrime_profs")


def viewer_password() -> str:
    return os.getenv("OPTIPRIME_VIEWER_PASSWORD", DEFAULT_VIEWER_PASSWORD)


def is_production() -> bool:
    return bool(os.getenv("VERCEL")) or os.getenv("OPTIPRIME_ENV", "").lower() == "production"


def static_tokens_enabled() -> bool:
    return os.getenv("OPTIPRIME_ALLOW_STATIC_TOKENS", "").lower() in {"1", "true", "yes"}


def session_duration_seconds() -> int:
    try:
        minutes = int(os.getenv("OPTIPRIME_SESSION_MINUTES", "120"))
    except ValueError:
        minutes = 120
    return max(5, min(minutes, 720)) * 60


def session_signing_secret() -> bytes:
    secret = os.getenv("OPTIPRIME_SESSION_SECRET", "")
    if is_production() and len(secret) < 32:
        raise RuntimeError("Secure session signing is not configured")
    if not secret:
        secret = expected_token()
    return secret.encode("utf-8")


def credentials_role(username: str, password: str) -> str | None:
    admin_password = expected_password()
    read_only_password = viewer_password()
    if is_production() and admin_password == DEFAULT_ADMIN_PASSWORD:
        return None
    if compare_digest(username, expected_username()) and compare_digest(password, admin_password):
        return "admin"
    if is_production() and read_only_password == DEFAULT_VIEWER_PASSWORD:
        return None
    if compare_digest(username, viewer_username()) and compare_digest(password, read_only_password):
        return "viewer"
    return None


def _encode_segment(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_segment(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_session_token(username: str, role: str) -> str:
    now = int(time.time())
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + session_duration_seconds(),
        "jti": token_urlsafe(16),
    }
    payload_segment = _encode_segment(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    message = f"{TOKEN_PREFIX}.{payload_segment}".encode("ascii")
    signature = _encode_segment(hmac.new(session_signing_secret(), message, hashlib.sha256).digest())
    return f"{TOKEN_PREFIX}.{payload_segment}.{signature}"


def session_for_token(token: str) -> dict[str, str | int] | None:
    if len(token) > 4096:
        return None
    try:
        prefix, payload_segment, signature = token.split(".", 2)
        if prefix != TOKEN_PREFIX:
            return None
        message = f"{prefix}.{payload_segment}".encode("ascii")
        expected_signature = _encode_segment(hmac.new(session_signing_secret(), message, hashlib.sha256).digest())
        if not compare_digest(signature, expected_signature):
            return None
        payload = json.loads(_decode_segment(payload_segment))
        now = int(time.time())
        if payload.get("role") not in {"admin", "viewer"}:
            return None
        if not isinstance(payload.get("sub"), str) or not payload["sub"]:
            return None
        if not isinstance(payload.get("exp"), int) or payload["exp"] <= now:
            return None
        if not isinstance(payload.get("iat"), int) or payload["iat"] > now + 60:
            return None
        return payload
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, RuntimeError, binascii.Error, UnicodeDecodeError):
        return None


def account_for_credentials(username: str, password: str) -> dict[str, str] | None:
    role = credentials_role(username, password)
    if not role:
        return None
    return {"user": username, "token": issue_session_token(username, role), "role": role}


def verify_credentials(username: str, password: str) -> bool:
    return credentials_role(username, password) is not None


def role_for_token(token: str) -> str | None:
    session = session_for_token(token)
    if session:
        return str(session["role"])
    if static_tokens_enabled():
        if compare_digest(token, expected_token()):
            return "admin"
        if compare_digest(token, viewer_token()):
            return "viewer"
    return None


def require_token(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
    if credentials is None or role_for_token(credentials.credentials) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


def require_write_token(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
    token = require_token(credentials)
    if role_for_token(token) != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is view-only")
    return token


async def require_websocket_token(websocket: WebSocket) -> bool:
    token = websocket.query_params.get("token", "")
    if role_for_token(token) is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False
    return True
