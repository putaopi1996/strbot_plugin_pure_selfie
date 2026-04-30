"""
Minimal network helpers for the group-selfie-only plugin.
"""

from __future__ import annotations

import asyncio

import aiohttp

from astrbot.api import logger

from .net_safety import URLFetchPolicy, ensure_url_allowed


_http_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


async def _get_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        async with _session_lock:
            if _http_session is None or _http_session.closed:
                timeout = aiohttp.ClientTimeout(total=30, connect=10)
                connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
                _http_session = aiohttp.ClientSession(
                    timeout=timeout,
                    connector=connector,
                )
    return _http_session


async def close_session() -> None:
    global _http_session
    if _http_session is not None and not _http_session.closed:
        await _http_session.close()
    _http_session = None


async def download_image(url: str, retries: int = 3) -> bytes | None:
    session = await _get_session()
    policy = URLFetchPolicy(
        allow_private=False,
        trusted_origins=frozenset(),
        allowed_hosts=frozenset(),
        dns_timeout_seconds=2.0,
    )
    max_redirects = 5
    max_bytes = 50 * 1024 * 1024

    for attempt in range(retries):
        try:
            current = str(url or "").strip()
            redirects = 0
            while True:
                await ensure_url_allowed(current, policy=policy)
                async with session.get(current, allow_redirects=False) as resp:
                    if resp.status in {301, 302, 303, 307, 308}:
                        if redirects >= max_redirects:
                            raise RuntimeError("Too many redirects")
                        location = (resp.headers.get("location") or "").strip()
                        if not location:
                            raise RuntimeError("Redirect without location")
                        current = (
                            aiohttp.client.URL(current)
                            .join(aiohttp.client.URL(location))
                            .human_repr()
                        )
                        redirects += 1
                        continue

                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")

                    total = 0
                    chunks: list[bytes] = []
                    async for chunk in resp.content.iter_chunked(1024 * 256):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise RuntimeError("Image too large")
                        chunks.append(chunk)
                    return b"".join(chunks)
        except asyncio.TimeoutError:
            logger.warning("[download_image] timeout: %s", url)
        except Exception as exc:
            if attempt + 1 >= retries:
                logger.error("[download_image] failed: url=%s err=%s", url, exc)
            else:
                await asyncio.sleep(1)
    return None
