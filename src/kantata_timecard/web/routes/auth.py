from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from ...config import get_settings
from ...crypto import get_cipher
from ...db import get_session
from ...kantata_client import KantataClient
from ...models import KantataUser, OAuthToken
from ..oauth import get_oauth

router = APIRouter()


@router.get("/login")
async def login(request: Request):
    oauth = get_oauth()
    redirect_uri = get_settings().kantata_oauth_redirect_url
    return await oauth.kantata.authorize_redirect(request, redirect_uri)


@router.get("/oauth/callback")
async def oauth_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    oauth = get_oauth()
    token = await oauth.kantata.authorize_access_token(request)

    access_token = token["access_token"]
    refresh_token = token.get("refresh_token")
    expires_in = token.get("expires_in")
    expires_at = (
        datetime.fromtimestamp(time.time() + int(expires_in), tz=timezone.utc)
        if expires_in
        else None
    )
    scope = token.get("scope")

    settings = get_settings()
    async with KantataClient(access_token, api_base=settings.kantata_api_base) as client:
        me = await client.get_me()

    kantata_user_id = int(me.get("id"))
    email = me.get("email_address") or me.get("email") or ""
    full_name = me.get("full_name") or me.get("name") or email

    result = await session.execute(
        select(KantataUser).where(KantataUser.kantata_user_id == kantata_user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = KantataUser(
            kantata_user_id=kantata_user_id,
            email=email,
            full_name=full_name,
        )
        session.add(user)
        await session.flush()
    else:
        user.email = email
        user.full_name = full_name
    user.last_login_at = datetime.now(timezone.utc)

    cipher = get_cipher()
    result = await session.execute(select(OAuthToken).where(OAuthToken.user_id == user.id))
    token_row = result.scalar_one_or_none()
    if token_row is None:
        token_row = OAuthToken(user_id=user.id)
        session.add(token_row)
    token_row.access_token_enc = cipher.encrypt(access_token)
    token_row.refresh_token_enc = cipher.encrypt(refresh_token) if refresh_token else None
    token_row.expires_at = expires_at
    token_row.scope = scope

    await session.commit()

    request.session["user_id"] = user.id
    request.session["user_name"] = user.full_name
    return RedirectResponse(url="/entries", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)
