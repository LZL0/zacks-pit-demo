"""HTTP API and MCP server return the same typed answers."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp import Client

from pitdemo.api.http import create_app
from pitdemo.api.mcp_server import MAX_ITEMS, create_server
from pitdemo.schemas import AdjustmentExplanation, ConsensusResult, CorporateActionList
from pitdemo.service import Service


@pytest.fixture(scope="module")
def http(service: Service) -> TestClient:
    return TestClient(create_app(service))


def test_http_consensus(http: TestClient) -> None:
    r = http.get("/consensus", params={"ticker": "NVDA", "period": "FY2025", "as_of": "2024-05-31"})
    assert r.status_code == 200
    body = ConsensusResult.model_validate(r.json())
    assert body.security.security_id == "SEC-0001"
    assert 2.0 < body.mean < 3.0  # type: ignore[operator]
    assert body.synthetic_estimates is True
    assert body.target_basis.startswith("latest known share basis")


def test_http_errors_are_typed(http: TestClient) -> None:
    r = http.get("/consensus", params={"ticker": "ZZZZ", "period": "FY2025", "as_of": "2024-05-31"})
    assert r.status_code == 404
    assert r.json()["error"] == "NotFound"
    r = http.get("/consensus", params={"ticker": "NVDA", "period": "next", "as_of": "2024-05-31"})
    assert r.status_code == 400
    assert r.json()["error"] == "InvalidRequest"
    r = http.get("/consensus", params={"ticker": "NVDA", "period": "FY2025", "as_of": "May 31"})
    assert r.status_code == 400


def test_http_series_and_actions(http: TestClient) -> None:
    r = http.get(
        "/consensus/series",
        params={"ticker": "NVDA", "period": "FY2025", "start": "2024-06-03", "end": "2024-06-14"},
    )
    assert r.status_code == 200
    points = r.json()["points"]
    assert len(points) == 10
    assert all(not p["insufficient"] for p in points)
    r = http.get("/corporate-actions", params={"ticker": "SIVBQ"})
    actions = CorporateActionList.model_validate(r.json())
    assert [a.action_type for a in actions.actions] == ["DELISTING"]
    assert actions.security.ticker == "SIVBQ"
    r = http.get(
        "/consensus/series",
        params={"ticker": "NVDA", "period": "FY1", "start": "2024-01-02", "end": "2024-03-01"},
    )
    assert r.status_code == 400  # relative labels jump; series must use fixed labels


def test_http_universe(http: TestClient) -> None:
    r = http.get("/universe", params={"date": "2023-03-01"})
    assert {"security_id": "SEC-0005", "ticker": "SIVB"} in r.json()


def test_openapi_has_typed_models(http: TestClient) -> None:
    spec = http.get("/openapi.json").json()
    assert {"ConsensusResult", "ErrorResponse", "Contributor"} <= set(spec["components"]["schemas"])


def _mcp(service: Service, name: str, args: dict[str, Any]) -> Any:
    async def go() -> Any:
        async with Client(create_server(service)) as c:
            return await c.call_tool(name, args)

    return asyncio.run(go())


def test_mcp_lists_three_tools(service: Service) -> None:
    async def go() -> list[str]:
        async with Client(create_server(service)) as c:
            return sorted(t.name for t in (await c.list_tools()).tools)

    assert asyncio.run(go()) == ["explain_adjustment", "get_consensus", "list_corporate_actions"]


def test_mcp_headline_question(service: Service, http: TestClient) -> None:
    """'What was NVDA's FY2025 EPS consensus on 2024-05-31, split-adjusted?'"""
    r = _mcp(
        service,
        "get_consensus",
        {"ticker": "NVDA", "fiscal_period": "FY2025", "as_of": "2024-05-31"},
    )
    assert not r.is_error
    result = ConsensusResult.model_validate(r.structured_content)
    assert result.basis == "adjusted"
    assert result.as_of.date().isoformat() == "2024-05-31"
    assert "Mean of" in result.method
    assert len(result.contributors) <= MAX_ITEMS
    via_http = http.get(
        "/consensus", params={"ticker": "NVDA", "period": "FY2025", "as_of": "2024-05-31"}
    ).json()
    assert via_http["mean"] == result.mean


def test_mcp_typed_errors(service: Service) -> None:
    r = _mcp(
        service,
        "get_consensus",
        {"ticker": "SIVB", "fiscal_period": "FY2023", "as_of": "2023-06-01"},
    )
    assert r.is_error
    payload = json.loads(r.content[0].text.split(": ", 1)[1])
    assert payload["error"] == "NotFound"
    r = _mcp(
        service,
        "get_consensus",
        {"ticker": "SEC-0005", "fiscal_period": "FY2023", "as_of": "2023-06-01"},
    )
    assert r.is_error
    assert "InsufficientEstimates" in r.content[0].text


def test_mcp_explain(service: Service) -> None:
    r = _mcp(
        service,
        "explain_adjustment",
        {"ticker": "NVDA", "date": "2024-06-14", "fiscal_period": "FY2025"},
    )
    exp = AdjustmentExplanation.model_validate(r.structured_content)
    text = "\n".join(exp.narrative)
    assert "SPLIT ratio 10" in text
    assert "sec.gov" in text
    assert exp.naive_mean is not None
    assert exp.naive_mean > 3 * exp.consensus_adjusted.mean


def test_mcp_list_corporate_actions(service: Service) -> None:
    r = _mcp(service, "list_corporate_actions", {"ticker": "GE"})
    actions = CorporateActionList.model_validate(r.structured_content)
    assert [a.action_type for a in actions.actions] == ["REVERSE_SPLIT", "SPINOFF", "SPINOFF"]


def test_http_validation_errors_are_typed(http: TestClient) -> None:
    r = http.get(
        "/consensus",
        params={"ticker": "NVDA", "period": "FY2025", "as_of": "2024-05-31", "basis": "raw"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "InvalidRequest"
    r = http.get("/consensus", params={"ticker": "NVDA"})
    assert r.status_code == 400
    assert "as_of" in r.json()["message"]


def test_http_revisions_accepts_relative_and_untidy_labels(http: TestClient) -> None:
    for period in ("FY1", " fy2025 "):
        r = http.get(
            "/revisions", params={"ticker": "NVDA", "period": period, "as_of": "2024-06-30"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["fiscal_period"] == "FY2025"
    r = http.get("/revisions", params={"ticker": "NVDA", "period": "next", "as_of": "2024-06-30"})
    assert r.status_code == 400


def test_python_sdk_raises_its_own_error_for_untyped_bodies() -> None:
    import sys

    import httpx

    from pitdemo.sdkgen import SDK

    sys.path.insert(0, str(SDK / "python"))
    from pitdemo_sdk import PitApiError, PitClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("ticker") == "HTML":
            return httpx.Response(502, text="<html>Bad gateway</html>")
        body = {"error": "NotFound", "message": "nope", "hint": None}
        return httpx.Response(404, json=body)

    client = PitClient("http://test", transport=httpx.MockTransport(handler))
    with pytest.raises(PitApiError) as typed:
        client.consensus("ZZZ", "FY2025", "2024-05-31")
    assert typed.value.error is not None
    assert typed.value.error.error.value == "NotFound"
    with pytest.raises(PitApiError) as untyped:
        client.consensus("HTML", "FY2025", "2024-05-31")
    assert untyped.value.error is None
    assert untyped.value.status == 502
    assert "Bad gateway" in untyped.value.body
