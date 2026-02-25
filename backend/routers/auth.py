"""
Authentication routes — Zerodha OAuth flow.

Flow:
  1. GET  /auth/login-url    → returns the Kite login URL
  2. User visits the URL, logs in, and Kite redirects to /auth/callback?request_token=...
  3. GET  /auth/callback     → exchanges token, stores access token in memory
  4. POST /auth/token        → alternative: caller provides access_token directly
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from kite_client import kite_client
from config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenBody(BaseModel):
    access_token: str


@router.get("/login-url")
async def get_login_url():
    """Return the Zerodha Kite login URL for the OAuth flow."""
    return {"login_url": kite_client.login_url()}


@router.get("/callback")
async def oauth_callback(request_token: str = Query(..., description="Token from Zerodha redirect")):
    """
    Called after the user logs in via Kite.
    Exchanges the request_token for an access_token and initialises the client.
    """
    try:
        access_token = await kite_client.generate_session(request_token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {exc}") from exc

    kite_client.initialise(access_token)
    settings.KITE_ACCESS_TOKEN = access_token
    return {"status": "authenticated", "access_token": access_token}


@router.post("/token")
async def set_token(body: TokenBody):
    """
    Directly set an access token (useful when you already have one from a previous session).
    """
    kite_client.initialise(body.access_token)
    settings.KITE_ACCESS_TOKEN = body.access_token
    return {"status": "ok"}


@router.get("/status")
async def auth_status():
    return {"authenticated": kite_client.is_ready}
