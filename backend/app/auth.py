from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from fastapi import HTTPException, Request, status
from passlib.context import CryptContext

from app.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_COOKIE_NAME = "leaguedeck_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days


def verify_password(plain_password: str) -> bool:
    return pwd_context.verify(plain_password, get_settings().app_password_hash)


def create_session_token() -> str:
    serializer = URLSafeTimedSerializer(get_settings().secret_key)
    return serializer.dumps({"authenticated": True})


def verify_session_token(token: str) -> bool:
    serializer = URLSafeTimedSerializer(get_settings().secret_key)
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return bool(data.get("authenticated"))


def require_auth(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token or not verify_session_token(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
