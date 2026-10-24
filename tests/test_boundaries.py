"""Every boundary is checked by a Pydantic model: requests, responses, SDKs, upstream data."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from typing import Any

import httpx
import pydantic
import pytest
from fastapi.testclient import TestClient
from mcp import Client

from pitdemo.api.http import create_app
from pitdemo.api.mcp_server import create_server
from pitdemo.ingest.edgar import load_raw, slim
from pitdemo.ingest.prices import fetch_chart
from pitdemo.ingest.upstream import (
    CompanyFacts,
    EdgarSlim,
    UpstreamFormatError,
    YahooChartResult,
    parse_upstream,
)
from pitdemo.pipeline import RAW_DIR
from pitdemo.schemas import ConsensusQuery
from pitdemo.sdkgen import SDK
from pitdemo.service import Service

sys.path.insert(0, str(SDK / "python"))
from pitdemo_sdk import PitClient

# ---------------------------------------------------------------- requests


@pytest.fixture(scope="module")
def http(service: Service) -> TestClient:
    return TestClient(create_app(service))


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"ticker": "NV DA"}, "'NV DA' is not a ticker such as NVDA"),
        ({"period": "FY3"}, "'FY3' is not a fiscal-year label such as FY2025, or FY1/FY2"),
        ({"as_of": "May 31"}, "'May 31' is not an ISO date"),
        ({"basis": "raw"}, "basis: Input should be 'adjusted' or 'unadjusted'"),
        ({"bogus": "1"}, "bogus: Extra inputs are not permitted"),
    ],
)
def test_http_rejects_bad_requests_in_plain_language(
    http: TestClient, params: dict[str, str], expected: str
) -> None:
    query = {"ticker": "NVDA", "period": "FY2025", "as_of": "2024-05-31", **params}
    r = http.get("/consensus", params=query)
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "InvalidRequest"
    assert expected in body["message"]
    assert "^" not in body["message"]  # no raw regex in messages


def test_http_series_requires_a_fixed_label(http: TestClient) -> None:
    r = http.get(
        "/consensus/series",
        params={"ticker": "NVDA", "period": "FY1", "start": "2024-06-03", "end": "2024-06-14"},
    )
    assert r.status_code == 400
    assert "fixed fiscal-year label" in r.json()["message"]


def test_http_normalizes_case_and_spaces(http: TestClient) -> None:
    r = http.get(
        "/consensus", params={"ticker": " nvda ", "period": " fy2025 ", "as_of": "2024-05-31"}
    )
    assert r.status_code == 200
    assert r.json()["fiscal_period"] == "FY2025"


def test_request_models_are_in_the_schema() -> None:
    schema = json.loads((SDK / "schema.json").read_text())["$defs"]
    for name in ("ConsensusQuery", "SeriesQuery", "RevisionsQuery", "UniverseQuery", "Universe"):
        assert name in schema, name
    assert schema["ConsensusQuery"]["additionalProperties"] is False


def test_mcp_tool_arguments_are_validated(service: Service) -> None:
    async def go() -> tuple[Any, Any]:
        async with Client(create_server(service)) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            bad = await c.call_tool(
                "get_consensus",
                {"ticker": "NVDA", "fiscal_period": "next year", "as_of": "2024-05-31"},
            )
            return tools["get_consensus"].input_schema, bad

    schema, bad = asyncio.run(go())
    assert bad.is_error
    assert "fiscal_period" in bad.content[0].text
    props = json.dumps(schema)
    assert "pattern" in props  # the LLM sees the expected formats


# ------------------------------------------------------------------- SDK


def test_sdk_validates_requests_before_sending() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    client = PitClient("http://test", transport=httpx.MockTransport(handler))
    for kwargs in (
        {"ticker": "NVDA", "period": "next", "as_of": "2024-05-31"},
        {"ticker": "NVDA", "period": "FY2025", "as_of": "31/05/2024"},
        {"ticker": "NV DA", "period": "FY2025", "as_of": "2024-05-31"},
    ):
        with pytest.raises(pydantic.ValidationError):
            client.consensus(**kwargs)
    with pytest.raises(pydantic.ValidationError):
        client.revisions("NVDA", "FY2025", "2024-06-30", lookback_days=0)
    assert calls == []


def test_sdk_parses_responses_strictly(service: Service) -> None:
    good = json.loads(service.get_consensus("NVDA", "FY2025", "2024-05-31").model_dump_json())
    drifted = copy.deepcopy(good)
    drifted["mean"] = str(drifted["mean"])  # a number sent as a string

    def make(body: dict[str, Any]) -> PitClient:
        return PitClient(
            "http://test", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
        )

    assert make(good).consensus("NVDA", "FY2025", "2024-05-31").mean == good["mean"]
    with pytest.raises(pydantic.ValidationError, match="mean"):
        make(drifted).consensus("NVDA", "FY2025", "2024-05-31")


# -------------------------------------------------------------- upstream


def test_committed_edgar_files_match_the_upstream_model() -> None:
    for path in sorted((RAW_DIR / "edgar").glob("CIK*.json")):
        parsed = EdgarSlim.model_validate_json(path.read_text())
        assert parsed.eps_diluted
        assert parsed.filings


def test_companyfacts_shape_change_fails_with_a_clear_message() -> None:
    raw = load_raw(RAW_DIR, 1045810)
    facts = {
        "cik": 1045810,
        "entityName": "NVIDIA CORP",
        "facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": []}}}},
    }
    facts["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD/shares"] = raw["eps_diluted"]
    assert parse_upstream(CompanyFacts, facts, "x").eps_diluted
    assert slim(1045810, facts, [])["entity_name"] == "NVIDIA CORP"

    renamed = copy.deepcopy(facts)
    units = renamed["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]
    units["USD-per-share"] = units.pop("USD/shares")
    with pytest.raises(UpstreamFormatError, match=r"companyfacts for CIK 1045810.*USD/shares"):
        slim(1045810, renamed, [])

    bad_value = copy.deepcopy(facts)
    bad_value["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD/shares"][0]["val"] = (
        "n/a"
    )
    with pytest.raises(UpstreamFormatError, match="val"):
        slim(1045810, bad_value, [])


def test_committed_file_with_a_bad_timestamp_is_rejected(tmp_path: Any) -> None:
    raw = load_raw(RAW_DIR, 1045810)
    raw["filings"][0]["acceptance"] = "2024-05-22 16:21:29"  # not SEC's format
    edgar = tmp_path / "edgar"
    edgar.mkdir()
    (edgar / "CIK0001045810.json").write_text(json.dumps(raw))
    with pytest.raises(UpstreamFormatError, match="acceptance"):
        load_raw(tmp_path, 1045810)


def _yahoo(result: dict[str, Any]) -> httpx.Client:
    body = {"chart": {"result": [result], "error": None}}
    return httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)))


def test_yahoo_payload_is_validated() -> None:
    import datetime as dt

    good = {
        "timestamp": [1718026200, 1718112600],
        "indicators": {"quote": [{"close": [121.79, 120.91], "volume": [1, None]}]},
        "events": {
            "splits": {"1718026200": {"date": 1718026200, "numerator": 10, "denominator": 1}}
        },
    }
    YahooChartResult.model_validate(good)
    f = fetch_chart(_yahoo(good), "NVDA", "SEC-0001", dt.date(2024, 6, 1), dt.date(2024, 6, 30))
    assert f.prices is not None
    assert f.prices.height == 2
    assert [s.ratio for s in f.splits] == [10.0]

    misaligned = copy.deepcopy(good)
    misaligned["indicators"]["quote"][0]["close"] = [121.79]
    with pytest.raises(UpstreamFormatError, match="Yahoo chart for NVDA"):
        fetch_chart(
            _yahoo(misaligned), "NVDA", "SEC-0001", dt.date(2024, 6, 1), dt.date(2024, 6, 30)
        )


def test_query_model_round_trip() -> None:
    q = ConsensusQuery.model_validate(
        {"ticker": "sec-0005", "period": "fy1", "as_of": "2023-02-15"}
    )
    assert (q.ticker, q.period) == ("SEC-0005", "FY1")
