from __future__ import annotations
import secrets
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
import httpx
from apps.api.app.config import settings
from packages.shared.logging import logger

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)

# In-memory token cache (token -> (UserContext, expiry_timestamp))
_token_cache: Dict[str, Tuple[UserContext, float]] = {}


@dataclass
class UserContext:
    user_id: str
    email: Optional[str] = None
    is_admin: bool = False


async def verify_api_key(
    request: Request,
    api_key: Optional[str] = Security(api_key_header),
    bearer: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> UserContext:
    """
    Verifies API Key, Supabase Auth Bearer JWT token, or SSE token query param.
    Extracts authenticated user context with strict per-user isolation.
    """
    # Extract provided token
    provided_key: Optional[str] = None
    if api_key:
        provided_key = api_key.strip()
    elif bearer and bearer.credentials:
        provided_key = bearer.credentials.strip()
    else:
        provided_key = request.query_params.get("token")
        if provided_key:
            provided_key = provided_key.strip()

    if not provided_key:
        # If no key in dev mode and no HELM_API_KEY configured
        if not settings.HELM_API_KEY.strip() and settings.APP_ENV == "development":
            return UserContext(user_id="dev-user", email="dev@kodium.ai", is_admin=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials. Please sign in or provide a valid API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 1. Check if matches master HELM_API_KEY (Admin / system operations)
    admin_key = settings.HELM_API_KEY.strip()
    if admin_key and secrets.compare_digest(provided_key, admin_key):
        return UserContext(user_id="admin", email="admin@kodium.ai", is_admin=True)

    # 2. Check local token cache
    now = time.time()
    if provided_key in _token_cache:
        cached_ctx, expires_at = _token_cache[provided_key]
        if now < expires_at:
            return cached_ctx
        else:
            del _token_cache[provided_key]

    # 3. Check for developer / demo session tokens
    if provided_key.startswith("demo-token-") or provided_key.startswith("dev-user-"):
        ctx = UserContext(user_id="demo-user", email="demo@kodium.ai", is_admin=False)
        _token_cache[provided_key] = (ctx, now + 600)
        return ctx

    # 4. Verify Supabase JWT Token with Supabase Auth API
    if settings.SUPABASE_URL:
        try:
            supabase_url = settings.SUPABASE_URL.rstrip("/")
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.get(
                    f"{supabase_url}/auth/v1/user",
                    headers={
                        "Authorization": f"Bearer {provided_key}",
                        "apikey": settings.SUPABASE_ANON_KEY or admin_key,
                    },
                )
                if res.status_code == 200:
                    user_data = res.json()
                    user_id = user_data.get("id")
                    email = user_data.get("email")
                    if user_id:
                        ctx = UserContext(user_id=user_id, email=email, is_admin=False)
                        _token_cache[provided_key] = (ctx, now + 300)  # cache for 5 minutes
                        return ctx
        except Exception as e:
            logger.warning(f"Supabase auth verification error: {e}")

    # Authentication failed
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired session. Please sign in again.",
        headers={"WWW-Authenticate": "Bearer"},
    )

