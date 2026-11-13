"""Tenant-scoped OpenSearch report/log retrieval; never authoritative business state."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_DAYS = 92
INDEXES = {"reports": "volumeos-reports-v1", "logs": "volumeos-logs-v1"}
FIELDS = {
    "reports": ("title", "offer", "summary", "event_date", "report_id", "tenant_id"),
    "logs": ("service", "level", "message", "event_date", "trace_id", "tenant_id"),
}


# What free text is matched against: text and id fields only. Dates are filtered by the range, never text-matched
# (a multi_match over event_date made OpenSearch try to parse "invoice" as a date: every search answered 500).
SEARCH_FIELDS = {
    "reports": ("title", "offer", "summary", "report_id"),
    "logs": ("service", "level", "message", "trace_id"),
}


class SearchError(ValueError):
    pass


@dataclass(frozen=True)
class SearchRequest:
    kind: str
    query: str
    start_date: str
    end_date: str
    size: int = 50

    def __post_init__(self):
        if self.kind not in INDEXES:
            raise SearchError("search kind must be reports or logs")
        if not 1 <= len(self.query) <= 256 or any(ord(char) < 32 for char in self.query):
            raise SearchError("search text must contain 1-256 printable characters")
        if not 1 <= self.size <= 100:
            raise SearchError("search size must be between 1 and 100")
        try:
            start = date.fromisoformat(self.start_date)
            end = date.fromisoformat(self.end_date)
        except ValueError as exc:
            raise SearchError("search dates must be ISO YYYY-MM-DD") from exc
        if not 0 <= (end - start).days <= MAX_DAYS:
            raise SearchError(f"search date range must be between 0 and {MAX_DAYS} days")


def build_insights_query(tenant_id: str, months: int) -> dict[str, Any]:
    if not SAFE_ID.fullmatch(tenant_id):
        raise SearchError("tenant claim is malformed")
    if not 1 <= months <= 24:
        raise SearchError("months must be between 1 and 24")
    return {"size": months, "track_total_hits": False, "sort": [{"event_date": "desc"}],
            "query": {"bool": {"filter": [{"term": {"tenant_id.keyword": tenant_id}}]}},
            "_source": ["month", "title", "summary", "metrics", "updated_at", "tenant_id"]}


class SearchTransport(Protocol):
    async def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]: ...


def build_query(request: SearchRequest, tenant_id: str) -> tuple[str, dict[str, Any]]:
    if not SAFE_ID.fullmatch(tenant_id):
        raise SearchError("tenant claim is malformed")
    body = {
        "size": request.size,
        "track_total_hits": False,
        "query": {"bool": {
            "filter": [
                {"term": {"tenant_id.keyword": tenant_id}},
                {"range": {"event_date": {"gte": request.start_date, "lte": request.end_date}}},
            ],
            "must": [{"multi_match": {"query": request.query, "fields": list(SEARCH_FIELDS[request.kind]),
                                      "type": "best_fields", "lenient": False}}],
            "must_not": [{"exists": {"field": "deleted_at"}}],
        }},
        "_source": list(FIELDS[request.kind]),
        "sort": [{"event_date": {"order": "desc"}}, {"_doc": {"order": "desc"}}],
    }
    return INDEXES[request.kind], body


class ReportSearch:
    def __init__(self, transport: SearchTransport):
        self.transport = transport

    async def insights(self, tenant_id: str, months: int) -> dict[str, Any]:
        """The business's monthly reports (newest first), as written by the insights indexer."""
        try:
            response = await self.transport.search(INDEXES["reports"], build_insights_query(tenant_id, months))
        except SearchError:
            raise
        except Exception as exc:  # an index the indexer hasn't created yet: nothing to show, not an error
            if "index_not_found" in str(exc):
                return {"months": []}
            raise
        rows = []
        for hit in response.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            if source.get("tenant_id") == tenant_id:  # defense in depth, as for search
                rows.append({k: source.get(k) for k in ("month", "title", "summary", "metrics", "updated_at")})
        return {"months": rows}

    async def search(self, request: SearchRequest, tenant_id: str) -> dict[str, Any]:
        index, body = build_query(request, tenant_id)
        response = await self.transport.search(index, body)
        hits = response.get("hits", {}).get("hits", []) if isinstance(response, dict) else []
        if not isinstance(hits, list):
            raise SearchError("OpenSearch returned an invalid hit set")
        rows = []
        for hit in hits:
            source = hit.get("_source", {}) if isinstance(hit, dict) else {}
            # Defense in depth: an incorrectly mapped index must not leak a row even
            # if the cluster ignored the mandatory tenant predicate.
            if not isinstance(source, dict) or source.get("tenant_id") != tenant_id:
                continue
            rows.append({key: source[key] for key in FIELDS[request.kind] if key in source})
            if len(rows) >= request.size:
                break
        return {"rows": rows, "row_count": len(rows), "authoritative": False}
