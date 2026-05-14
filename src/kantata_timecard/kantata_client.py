from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class KantataAPIError(Exception):
    def __init__(self, status_code: int, message: str, body: Any = None):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.body = body


class KantataClient:
    """
    Thin async wrapper around the Kantata OX REST API.

    Kantata returns responses shaped like:
        {
          "count": 2,
          "meta": {"count": 2, "page_count": 1, "page_number": 1, "page_size": 20},
          "results": [{"key": "time_entries", "id": "123"}, {"key": "time_entries", "id": "124"}],
          "time_entries": {"123": {...}, "124": {...}},
          "workspaces": {"5": {...}},
          "stories": {"99": {...}},
          ...
        }

    `_hydrate()` flattens this into a list of fully-populated dicts where each
    `*_id` reference is replaced (under a `__includes__` key) with the matching
    object from the top-level dicts, so callers do not need to dereference.
    """

    DEFAULT_PAGE_SIZE = 200  # max permitted by Kantata

    def __init__(
        self,
        access_token: str,
        api_base: str = "https://api.mavenlink.com/api/v1",
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        if not access_token:
            raise ValueError("access_token is required")
        self._access_token = access_token
        self._api_base = api_base.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> KantataClient:
        self._client = httpx.AsyncClient(
            base_url=self._api_base,
            timeout=self._timeout,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
            },
        )
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("KantataClient must be used as an async context manager")

        clean_params = (
            {k: v for k, v in params.items() if v is not None and v != ""} if params else None
        )

        attempt = 0
        while True:
            attempt += 1
            try:
                resp = await self._client.request(method, path, params=clean_params, json=json)
            except httpx.RequestError as e:
                if attempt > self._max_retries:
                    raise KantataAPIError(0, f"network error: {e}") from e
                await asyncio.sleep(0.5 * attempt)
                continue

            if resp.status_code == 429 and attempt <= self._max_retries:
                retry_after = float(resp.headers.get("Retry-After", "2"))
                logger.warning("rate-limited; sleeping %.1fs before retry %d", retry_after, attempt)
                await asyncio.sleep(retry_after)
                continue

            if resp.status_code == 204:
                return {}

            try:
                payload = resp.json()
            except ValueError:
                payload = {"raw": resp.text}

            if resp.is_error:
                message = _extract_error_message(payload) or resp.reason_phrase
                raise KantataAPIError(resp.status_code, message, payload)

            return payload

    @staticmethod
    def _hydrate(payload: dict[str, Any]) -> list[dict[str, Any]]:
        results = payload.get("results", [])
        out: list[dict[str, Any]] = []
        for ref in results:
            key, obj_id = ref.get("key"), ref.get("id")
            bucket = payload.get(key, {})
            obj = bucket.get(obj_id)
            if obj is None:
                continue
            obj = dict(obj)
            obj.setdefault("id", obj_id)
            obj["__includes__"] = {
                k: v for k, v in payload.items() if k not in {"count", "meta", "results", key}
            }
            out.append(obj)
        return out

    async def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> AsyncIterator[dict[str, Any]]:
        page = 1
        params = dict(params or {})
        while True:
            params.update({"page": page, "per_page": page_size})
            payload = await self._request("GET", path, params=params)
            items = self._hydrate(payload)
            for item in items:
                yield item
            meta = payload.get("meta") or {}
            if page >= int(meta.get("page_count", 1)):
                return
            page += 1

    # ---------------- Time entries ----------------

    async def list_time_entries(
        self,
        *,
        user_ids: list[int] | None = None,
        workspace_id: int | None = None,
        date_from: str | None = None,  # YYYY-MM-DD
        date_to: str | None = None,  # YYYY-MM-DD
        include_archived: bool = False,
        include: str = "user,workspace,story",
        page_size: int = DEFAULT_PAGE_SIZE,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"include": include, "from_archived_workspaces": include_archived}
        if user_ids:
            params["with_user_ids"] = ",".join(str(u) for u in user_ids)
        if workspace_id:
            params["workspace_id"] = workspace_id
        if date_from and date_to:
            params["date_performed_between"] = f"{date_from}:{date_to}"
        elif date_from:
            params["date_performed_between"] = f"{date_from}:2099-12-31"
        elif date_to:
            params["date_performed_between"] = f"1970-01-01:{date_to}"

        items: list[dict[str, Any]] = []
        async for item in self.paginate("/time_entries.json", params, page_size=page_size):
            items.append(item)
            if max_items is not None and len(items) >= max_items:
                break
        return items

    async def get_time_entry(self, entry_id: int, include: str = "user,workspace,story") -> dict[str, Any]:
        payload = await self._request(
            "GET", f"/time_entries/{entry_id}.json", params={"include": include}
        )
        items = self._hydrate(payload)
        if not items:
            raise KantataAPIError(404, f"time entry {entry_id} not found", payload)
        return items[0]

    async def update_time_entry(self, entry_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        body = {"time_entry": {k: v for k, v in fields.items() if v is not None}}
        payload = await self._request("PUT", f"/time_entries/{entry_id}.json", json=body)
        items = self._hydrate(payload)
        return items[0] if items else {}

    async def create_time_entry(self, fields: dict[str, Any]) -> dict[str, Any]:
        body = {"time_entry": {k: v for k, v in fields.items() if v is not None}}
        payload = await self._request("POST", "/time_entries.json", json=body)
        items = self._hydrate(payload)
        return items[0] if items else {}

    async def delete_time_entry(self, entry_id: int) -> None:
        await self._request("DELETE", f"/time_entries/{entry_id}.json")

    async def delete_time_entries(self, entry_ids: list[int]) -> None:
        """Bulk delete up to 100 entries in a single request."""
        if not entry_ids:
            return
        if len(entry_ids) > 100:
            raise ValueError("Kantata bulk delete supports at most 100 IDs per request")
        await self._request(
            "DELETE",
            "/time_entries.json",
            params={"ids": ",".join(str(i) for i in entry_ids)},
        )

    # ---------------- Lookup helpers (needed for edit form dropdowns) ----------------

    async def get_me(self) -> dict[str, Any]:
        payload = await self._request("GET", "/users/me.json")
        items = self._hydrate(payload)
        return items[0] if items else {}

    async def list_workspaces(self, search: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"include_archived": False}
        if search:
            params["matching"] = search
        items: list[dict[str, Any]] = []
        async for item in self.paginate("/workspaces.json", params):
            items.append(item)
        return items

    async def list_stories(self, workspace_id: int) -> list[dict[str, Any]]:
        params = {"workspace_id": workspace_id}
        items: list[dict[str, Any]] = []
        async for item in self.paginate("/stories.json", params):
            items.append(item)
        return items

    async def list_users(self, search: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if search:
            params["search"] = search
        items: list[dict[str, Any]] = []
        async for item in self.paginate("/users.json", params):
            items.append(item)
        return items


def _extract_error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return first.get("message") or first.get("detail") or str(first)
        return str(first)
    return payload.get("message") or payload.get("error")
