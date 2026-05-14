from __future__ import annotations

from authlib.integrations.starlette_client import OAuth

from ..config import get_settings

_oauth: OAuth | None = None


def get_oauth() -> OAuth:
    """Return a singleton Authlib OAuth registry configured for Kantata."""
    global _oauth
    if _oauth is None:
        settings = get_settings()
        registry = OAuth()
        registry.register(
            name="kantata",
            client_id=settings.kantata_client_id,
            client_secret=settings.kantata_client_secret,
            authorize_url=settings.kantata_authorize_url,
            access_token_url=settings.kantata_token_url,
            client_kwargs={"scope": ""},  # Kantata applies the scopes from app registration
        )
        _oauth = registry
    return _oauth
