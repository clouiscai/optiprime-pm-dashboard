import os
from secrets import compare_digest

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


load_dotenv()
security = HTTPBearer(auto_error=False)


def expected_token() -> str:
    return os.getenv("ROBOTX_TOKEN", "change-this-admin-token")


def viewer_token() -> str:
    return os.getenv("OPTIPRIME_VIEWER_TOKEN", "change-this-viewer-token")


def expected_username() -> str:
    return os.getenv("OPTIPRIME_USERNAME", "OptiPrime")


def expected_password() -> str:
    return os.getenv("OPTIPRIME_PASSWORD", "change-this-admin-password")


def viewer_username() -> str:
    return os.getenv("OPTIPRIME_VIEWER_USERNAME", "OptiPrime_profs")


def viewer_password() -> str:
    return os.getenv("OPTIPRIME_VIEWER_PASSWORD", "change-this-viewer-password")


def account_for_credentials(username: str, password: str) -> dict[str, str] | None:
    if compare_digest(username, expected_username()) and compare_digest(password, expected_password()):
        return {"user": username, "token": expected_token(), "role": "admin"}
    if compare_digest(username, viewer_username()) and compare_digest(password, viewer_password()):
        return {"user": username, "token": viewer_token(), "role": "viewer"}
    return None


def verify_credentials(username: str, password: str) -> bool:
    return account_for_credentials(username, password) is not None


def role_for_token(token: str) -> str | None:
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
