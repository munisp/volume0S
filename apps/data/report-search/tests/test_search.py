import pytest

from search import ReportSearch, SearchError, SearchRequest, build_query


class Transport:
    def __init__(self, hits=None):
        self.hits = hits or []
        self.calls = []

    async def search(self, index, body):
        self.calls.append((index, body))
        return {"hits": {"hits": self.hits}}


def request(**changes):
    values = dict(kind="reports", query="retainer revenue", start_date="2026-10-01", end_date="2026-10-28")
    values.update(changes)
    return SearchRequest(**values)


def test_query_has_server_side_tenant_date_index_and_source_guards():
    index, body = build_query(request(), "tenant-a")
    assert index == "volumeos-reports-v1"
    assert {"term": {"tenant_id.keyword": "tenant-a"}} in body["query"]["bool"]["filter"]
    assert body["size"] == 50
    assert body["track_total_hits"] is False
    assert "script" not in str(body)
    assert body["_source"] == ["title", "offer", "summary", "event_date", "report_id", "tenant_id"]


@pytest.mark.asyncio
async def test_search_post_filters_unexpected_cross_tenant_hits_and_extra_fields():
    transport = Transport([
        {"_source": {"tenant_id": "tenant-b", "title": "other tenant"}},
        {"_source": {"tenant_id": "tenant-a", "title": "approved", "report_id": "r1", "private_note": "drop"}},
    ])
    result = await ReportSearch(transport).search(request(), "tenant-a")
    assert result == {
        "rows": [{"tenant_id": "tenant-a", "title": "approved", "report_id": "r1"}],
        "row_count": 1, "authoritative": False,
    }


@pytest.mark.parametrize("changes", [
    {"kind": "bronze"}, {"query": ""}, {"query": "x" * 257}, {"size": 0}, {"size": 101},
    {"start_date": "2025-01-01"}, {"end_date": "not-a-date"},
])
def test_bounded_search_contract(changes):
    with pytest.raises(SearchError):
        request(**changes)


def test_tenant_claim_cannot_be_injected():
    with pytest.raises(SearchError):
        build_query(request(), "tenant-a' OR 1=1")
