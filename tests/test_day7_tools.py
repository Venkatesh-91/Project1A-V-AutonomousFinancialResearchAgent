"""
test_day7_tools.py

Tests for Day 7's real (non-mock) implementations of earnings_transcript,
company_profile, peer_comparison, and fact_checker. Every external
dependency (yfinance, ddgs, httpx) is monkeypatched with realistic fake
responses -- no real network calls, consistent with this project's other
real-tool tests (see tests/test_real_tools.py from Day 5).
"""

from unittest.mock import MagicMock

import httpx
import pytest

from tools import company_profile, earnings, fact_checker, peer_comparison
from tools.tool_registry import build_default_registry


# ---------------------------------------------------------------------- #
# company_profile.py (real) -- mocked yfinance
# ---------------------------------------------------------------------- #
def test_company_profile_run_success(monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.info = {
        "regularMarketPrice": 230.0,
        "longName": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "marketCap": 3_400_000_000_000,
        "longBusinessSummary": "Apple designs and sells consumer electronics.",
        "companyOfficers": [
            {"name": "Tim Cook", "title": "CEO"},
            {"name": "Luca Maestri", "title": "CFO"},
        ],
        "website": "https://www.apple.com",
        "city": "Cupertino",
        "state": "CA",
        "country": "United States",
    }

    company_profile._fetch_profile.cache.clear()
    monkeypatch.setattr(company_profile.yf, "Ticker", lambda ticker: fake_ticker)

    result = company_profile.run(ticker="AAPL")

    assert result.success is True
    assert result.data["name"] == "Apple Inc."
    assert result.data["sector"] == "Technology"
    assert len(result.data["key_executives"]) == 2
    assert result.data["key_executives"][0]["name"] == "Tim Cook"


def test_company_profile_run_handles_invalid_ticker(monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.info = {}

    company_profile._fetch_profile.cache.clear()
    monkeypatch.setattr(company_profile.yf, "Ticker", lambda ticker: fake_ticker)

    result = company_profile.run(ticker="ZZZZ")
    assert result.success is False


# ---------------------------------------------------------------------- #
# earnings.py (real) -- mocked ddgs + httpx
# ---------------------------------------------------------------------- #
def test_earnings_run_success(monkeypatch):
    fake_search_results = [
        {"title": "AAPL Q3 2024 Earnings Call Transcript", "href": "https://example.com/transcript"},
    ]

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results):
            return fake_search_results

    fake_html_response = MagicMock()
    fake_html_response.text = "<html><body><p>" + ("Earnings call content. " * 50) + "</p></body></html>"
    fake_html_response.raise_for_status.return_value = None

    earnings._search_for_transcript.cache.clear()
    monkeypatch.setattr(earnings, "DDGS", FakeDDGS)
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: fake_html_response)

    result = earnings.run(ticker="AAPL", quarter="Q3", year=2024)

    assert result.success is True
    assert result.data["ticker"] == "AAPL"
    assert "Earnings call content" in result.data["transcript_excerpt"]
    assert result.data["source_url"] == "https://example.com/transcript"


def test_earnings_run_no_search_results(monkeypatch):
    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results):
            return []

    earnings._search_for_transcript.cache.clear()
    monkeypatch.setattr(earnings, "DDGS", FakeDDGS)

    result = earnings.run(ticker="AAPL", quarter="Q3", year=2024)
    assert result.success is False


# ---------------------------------------------------------------------- #
# peer_comparison.py (real) -- mocked yfinance, curated map
# ---------------------------------------------------------------------- #
def test_peer_comparison_run_success_for_mapped_ticker(monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.info = {
        "regularMarketPrice": 400.0,
        "marketCap": 3_000_000_000_000,
        "trailingPE": 35.0,
        "operatingMargins": 0.40,
        "revenueGrowth": 0.12,
    }

    peer_comparison._fetch_metric_snapshot.cache.clear()
    monkeypatch.setattr(peer_comparison.yf, "Ticker", lambda ticker: fake_ticker)

    result = peer_comparison.run(ticker="AAPL", num_peers=2)

    assert result.success is True
    assert result.data["ticker"] == "AAPL"
    assert len(result.data["peers"]) == 2
    for peer_ticker in result.data["peers"]:
        assert result.data["comparison"][peer_ticker]["operating_margin_pct"] == 40.0


def test_peer_comparison_run_fails_gracefully_for_unmapped_ticker():
    result = peer_comparison.run(ticker="ZZZZ_NOT_MAPPED")
    assert result.success is False
    assert "curated peer mapping" in result.error


# ---------------------------------------------------------------------- #
# fact_checker.py (real) -- mocked ddgs
# ---------------------------------------------------------------------- #
def test_fact_checker_run_well_corroborated(monkeypatch):
    fake_results = [
        {"title": "Apple revenue grew 15%", "body": "Apple reported revenue growth of 15% year over year."},
        {"title": "AAPL Q3 results", "body": "Revenue growth was strong at 15% for the quarter."},
    ]

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results):
            return fake_results

    fact_checker._search_claim.cache.clear()
    monkeypatch.setattr(fact_checker, "DDGS", FakeDDGS)

    result = fact_checker.run(claim="Apple's revenue grew 15% year over year")

    assert result.success is True
    assert result.data["confidence_score"] > 0.5
    assert result.data["verification_status"] in ("well_corroborated", "partially_corroborated")


def test_fact_checker_run_no_results_found(monkeypatch):
    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results):
            return []

    fact_checker._search_claim.cache.clear()
    monkeypatch.setattr(fact_checker, "DDGS", FakeDDGS)

    result = fact_checker.run(claim="Some obscure unverifiable claim")
    assert result.success is True
    assert result.data["confidence_score"] == 0.0
    assert result.data["verification_status"] == "no_corroboration_found"


# ---------------------------------------------------------------------- #
# Registry-level: confirm all 12 tools present and fallbacks wired
# ---------------------------------------------------------------------- #
def test_default_registry_has_all_twelve_tools():
    registry = build_default_registry()
    names = set(registry.list_tool_names())
    expected = {
        "sec_filing_search", "financial_data_api", "web_search", "news_sentiment",
        "earnings_transcript", "company_profile", "peer_comparison",
        "calculation_engine", "fact_checker", "report_generator",
        "vector_db_search", "vector_db_store",
    }
    assert names == expected


def test_peer_comparison_falls_back_to_mock_for_unmapped_ticker_via_registry():
    """
    End-to-end: dispatching an unmapped ticker through the real registry
    should trigger the fallback chain and succeed via the mock, rather
    than surfacing the real tool's failure to the caller.
    """
    registry = build_default_registry()
    result = registry.dispatch("peer_comparison", ticker="ZZZZ_NOT_MAPPED")
    assert result.fallback_used is True
    assert result.success is True  # peer_comparison_mock.py handles any ticker
