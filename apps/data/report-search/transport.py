"""Authenticated OpenSearch transport with no index or query DSL from callers."""
from __future__ import annotations

import os
from typing import Any


class OpenSearchTransport:
    def __init__(self, client: Any):
        self.client = client

    @classmethod
    def from_env(cls) -> "OpenSearchTransport":
        from opensearchpy import AsyncOpenSearch

        url = os.environ.get("OPENSEARCH_URL", "")
        username = os.environ.get("OPENSEARCH_USERNAME", "")
        password = os.environ.get("OPENSEARCH_PASSWORD", "")
        if not url.startswith(("https://", "http://")) or not username or not password:
            raise RuntimeError("OPENSEARCH_URL, OPENSEARCH_USERNAME, and OPENSEARCH_PASSWORD are required")
        client = AsyncOpenSearch(
            hosts=[url], http_auth=(username, password), use_ssl=url.startswith("https://"),
            verify_certs=True, timeout=10, max_retries=2, retry_on_timeout=True,
        )
        return cls(client)

    async def ready(self) -> None:
        if not await self.client.ping():
            raise RuntimeError("OpenSearch is unavailable")

    async def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self.client.search(index=index, body=body)

    async def close(self) -> None:
        await self.client.close()
