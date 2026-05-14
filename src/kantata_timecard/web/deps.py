from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..crypto import get_cipher
from ..db import get_session
from ..kantata_client import KantataClient
from ..models import KantataUser, OAuthToken

REFRESH_BUFFER = timedelta(seconds=60)


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> KantataUser:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not signed in")
    user = await session.get(KantataUser, user_id)
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")
    return user


def _is_expiring_soon(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc) + REFRESH_BUFFER


async def _refresh_token(session: AsyncSession, token: OAuthToken, cipher) -> str:
    if token.refresh_token_enc is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="access token expired and no refresh token on file; please sign in again",
        )
    settings = get_settings()
    refresh_token = cipher.decrypt(token.refresh_token_enc)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            settings.kantata_token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.kantata_client_id,
                "client_secret": settings.kantata_client_secret,
            },
        )
    if resp.is_error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"token refresh failed: {resp.text[:200]}",
        )
    payload = resp.json()
    token.access_token_enc = cipher.encrypt(payload["access_token"])
    if payload.get("refresh_token"):
        token.refresh_token_enc = cipher.encrypt(payload["refresh_token"])
    if payload.get("expires_in"):
        token.expires_at = datetime.fromtimestamp(
            time.time() + int(payload["expires_in"]), tz=timezone.utc
        )
    await session.commit()
    return payload["access_token"]


async def kantata_client_for_user(
    user: KantataUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AsyncIterator[KantataClient]:
    result = await session.execute(select(OAuthToken).where(OAuthToken.user_id == user.id))
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="no token on file")

    cipher = get_cipher()
    if _is_expiring_soon(token.expires_at):
        access_token = await _refresh_token(session, token, cipher)
    else:
        access_token = cipher.decrypt(token.access_token_enc)

    settings = get_settings()
    async with KantataClient(access_token, api_base=settings.kantata_api_base) as client:
        yield client
