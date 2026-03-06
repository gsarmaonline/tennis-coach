"""
Authentication routes:
  GET /auth/google           — start OAuth flow
  GET /auth/callback         — handle OAuth callback
  GET /auth/me               — return current user info
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from api.auth.dependencies import get_current_user
from api.auth.google import build_auth_url, exchange_code, verify_id_token
from api.auth.jwt import create_access_token
from api.models import UserResponse
from api.services import user_store
from api.services.redis_client import get_redis_client
from api.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _state_key(state: str) -> str:
    return f"oauth_state:{state}"


@router.get("/google")
async def login_with_google():
    """Generate a CSRF state token, store it in Redis, and redirect to Google consent."""
    state = secrets.token_urlsafe(32)
    r = get_redis_client()
    r.set(_state_key(state), "1", ex=settings.oauth_state_ttl)
    return RedirectResponse(url=build_auth_url(state))


@router.get("/callback")
async def oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
):
    """
    Handle Google's redirect after user consent.
    Exchange the code, find-or-create the user, sign a JWT, and redirect to
    the frontend with the token.
    """
    r = get_redis_client()
    state_key = _state_key(state)
    if not r.get(state_key):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    r.delete(state_key)

    # Exchange authorization code for tokens
    tokens = await exchange_code(code)
    id_token = tokens.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="No id_token in Google response.")

    # Verify and extract claims
    claims = await verify_id_token(id_token)
    google_id = claims["sub"]
    email = claims["email"]
    name = claims["name"]
    picture = claims["picture"]

    # Find or create user
    user = user_store.get_user_by_google_id(google_id)
    if user is None:
        user = user_store.create_user(google_id, email, name, picture)
    else:
        user_store.update_user(
            user["user_id"],
            email=email,
            name=name,
            picture=picture,
            last_login=datetime.now(timezone.utc).isoformat(),
        )
        user = user_store.get_user(user["user_id"])

    jwt = create_access_token(user["user_id"], email)
    redirect_url = f"{settings.frontend_url}/auth/callback?token={jwt}"
    return RedirectResponse(url=redirect_url)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Return the authenticated user's profile."""
    user = user_store.get_user(current_user["sub"])
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return UserResponse(
        user_id=user["user_id"],
        email=user["email"],
        name=user["name"],
        picture=user["picture"],
        created_at=datetime.fromisoformat(user["created_at"]),
        last_login=datetime.fromisoformat(user["last_login"]),
    )
