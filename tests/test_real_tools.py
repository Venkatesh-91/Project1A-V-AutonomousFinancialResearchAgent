"""
test_real_tools.py

Tests the Day 5 real tool implementations WITHOUT making any real network
calls -- every external dependency (httpx, yfinance, ddgs) is monkeypatched
with realistic fake responses. This verifies the parsing/error-handling
logic is correct, independent of network availability, rate limits, or API
changes on the provider's end.

Also verifies the fallback-chain wiring in build_default_registry(): when
a real tool's implementation raises, the registered mock fallback should
take over automatically and the result should report fallback_used=True.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from tools import financial_api, news_sentiment, sec_edgar, web_search
from tools.http_utils import RateLimiter, TTLCache, ttl_cache
from tools.tool_registry import build_default_registry


# ---------------------------------------------------------------------- #
# http_utils: RateLimiter and ttl_cache
# ---------------------------------------------------------------------- #
def test_rate_limiter_enforces_minimum_interval():
    import time

    limiter = RateLimiter(min_interval_seconds=0.2)
    start = time.monotonic()
    limiter.wait()
    limiter.wait()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.2


def test_ttl_cache_returns_cached_value_within_ttl():
    call_count = {"n": 0}

    @ttl_cache(ttl_seconds=60)
    def expensive(x):
        call_count["n"] += 1
        return x * 2

    assert expensive(5) == 10
    assert expensive(5) == 10  # should hit cache, not re-call
    assert call_count["n"] == 1


def test_ttl_cache_expires(monkeypatch):
    import tools.http_utils as http_utils_module

    fake_time = {"now": 0.0}
    monkeypatch.setattr(http_utils_module.time, "monotonic", lambda: fake_time["now"])

    cache = TTLCache()
    cache.set("key", "value", ttl_seconds=10)
    assert cache.get("key") == "value"

    fake_time["now"] = 11.0
    assert cache.get("key") is None


# ---------------------------------------------------------------------- #
# sec_edgar.py (real) -- mocked httpx
# ---------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def clear_sec_edgar_caches():
    """Each test gets a clean cache so results from one test don't leak
    into another via the module-level ttl_cache decorators."""
    sec_edgar._get_ticker_to_cik_map.cache.clear()
    sec_edgar._get_submissions.cache.clear()
    yield


def _mock_httpx_response(json_data):
    mock_response = MagicMock()
    mock_response.json.return_value = json_data
    mock_response.raise_for_status.return_value = None
    return mock_response


def test_sec_edgar_run_success(monkeypatch):
    ticker_map_response = _mock_httpx_response(
        {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
    )
    submissions_response = _mock_httpx_response(
        {
            "filings": {
                "recent": {
                    "form": ["10-K", "8-K"],
                    "filingDate": ["2024-11-01", "2024-09-01"],
                    "accessionNumber": ["0000320193-24-000123", "0000320193-24-000099"],
                    "primaryDocument": ["aapl-10k.htm", "aapl-8k.htm"],
                }
            }
        }
    )

    call_sequence = [ticker_map_response, submissions_response]

    def fake_get(url, headers=None, timeout=None):
        return call_sequence.pop(0)

    monkeypatch.setattr(httpx, "get", fake_get)

    result = sec_edgar.run(ticker="AAPL", filing_type="10-K")

    assert result.success is True
    assert result.source_tier == 1
    assert result.data["ticker"] == "AAPL"
    assert result.data["accession_number"] == "0000320193-24-000123"
    assert "sec.gov/Archives" in result.data["filing_url"]


def test_sec_edgar_run_unknown_ticker(monkeypatch):
    ticker_map_response = _mock_httpx_response(
        {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
    )
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: ticker_map_response)

    result = sec_edgar.run(ticker="ZZZZ", filing_type="10-K")

    assert result.success is False
    assert "No CIK found" in result.error


def test_sec_edgar_run_no_matching_filing(monkeypatch):
    ticker_map_response = _mock_httpx_response(
        {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
    )
    submissions_response = _mock_httpx_response(
        {"filings": {"recent": {"form": ["8-K"], "filingDate": ["2024-09-01"],
                                 "accessionNumber": ["0000320193-24-000099"],
                                 "primaryDocument": ["aapl-8k.htm"]}}}
    )
    call_sequence = [ticker_map_response, submissions_response]
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: call_sequence.pop(0))

    result = sec_edgar.run(ticker="AAPL", filing_type="10-K")

    assert result.success is False
    assert "No '10-K' filing found" in result.error


def test_sec_edgar_network_failure_raises_for_fallback_to_catch(monkeypatch):
    """A raw network exception should propagate out of run() so the
    ToolRegistry's fallback-chain mechanism (which catches Exception) can
    route to the mock -- run() itself should not swallow this."""

    def fake_get(*args, **kwargs):
        raise httpx.ConnectError("simulated network failure")

    monkeypatch.setattr(httpx, "get", fake_get)

    with pytest.raises(httpx.ConnectError):
        sec_edgar.run(ticker="AAPL", filing_type="10-K")


# ---------------------------------------------------------------------- #
# financial_api.py (real) -- mocked yfinance
# ---------------------------------------------------------------------- #
def test_financial_api_run_success(monkeypatch):
    import pandas as pd

    fake_ticker = MagicMock()
    fake_ticker.info = {
        "regularMarketPrice": 230.0,
        "operatingMargins": 0.315,
        "trailingPE": 35.2,
    }
    fake_ticker.income_stmt = pd.DataFrame(
        {pd.Timestamp("2024-09-30"): {"Total Revenue": 391035000000, "Net Income": 93736000000}}
    )
    fake_ticker.balance_sheet = pd.DataFrame()
    fake_ticker.cashflow = pd.DataFrame()

    financial_api._fetch_ticker_data.cache.clear()
    monkeypatch.setattr(financial_api.yf, "Ticker", lambda ticker: fake_ticker)

    result = financial_api.run(ticker="AAPL", statement_type="income_statement", period="annual")

    assert result.success is True
    assert result.source_tier == 2
    assert result.data["revenue_usd"] == 391035000000
    assert result.data["operating_margin_pct"] == 31.5


def test_financial_api_run_handles_missing_ticker_gracefully(monkeypatch):
    fake_ticker = MagicMock()
    fake_ticker.info = {}  # no market price data -- simulates an invalid ticker

    financial_api._fetch_ticker_data.cache.clear()
    monkeypatch.setattr(financial_api.yf, "Ticker", lambda ticker: fake_ticker)

    result = financial_api.run(ticker="ZZZZ", statement_type="income_statement", period="annual")

    assert result.success is False
    assert result.error is not None


# ---------------------------------------------------------------------- #
# web_search.py (real) -- mocked ddgs
# ---------------------------------------------------------------------- #
def test_web_search_run_success(monkeypatch):
    fake_results = [
        {"title": "Apple Q4 Earnings", "href": "https://example.com/1", "body": "Apple reported..."},
        {"title": "Apple Stock News", "href": "https://example.com/2", "body": "Shares rose..."},
    ]

    web_search._search.cache.clear()

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results):
            return fake_results

    monkeypatch.setattr(web_search, "DDGS", FakeDDGS)

    result = web_search.run(query="Apple earnings", num_results=2)

    assert result.success is True
    assert len(result.data["results"]) == 2
    assert result.data["results"][0]["title"] == "Apple Q4 Earnings"


def test_web_search_run_no_results(monkeypatch):
    web_search._search.cache.clear()

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results):
            return []

    monkeypatch.setattr(web_search, "DDGS", FakeDDGS)

    result = web_search.run(query="a query with no results")

    assert result.success is False


# ---------------------------------------------------------------------- #
# news_sentiment.py (real) -- mocked ddgs + real TextBlob scoring
# ---------------------------------------------------------------------- #
def test_news_sentiment_run_success(monkeypatch):
    fake_articles = [
        {
            "title": "Company reports excellent record profits",
            "body": "Investors are thrilled with the outstanding results.",
            "url": "https://example.com/a",
            "date": "2025-01-01",
        },
        {
            "title": "Company faces disappointing lawsuit setback",
            "body": "Shareholders are concerned about the terrible news.",
            "url": "https://example.com/b",
            "date": "2025-01-02",
        },
    ]

    news_sentiment._fetch_news.cache.clear()

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def news(self, query, max_results):
            return fake_articles

    monkeypatch.setattr(news_sentiment, "DDGS", FakeDDGS)

    result = news_sentiment.run(query="Test Company", num_articles=2)

    assert result.success is True
    assert result.data["num_articles_analyzed"] == 2
    assert len(result.data["article_summaries"]) == 2
    # one clearly positive, one clearly negative headline -- overall should
    # not be wildly skewed to either extreme
    labels = {a["sentiment_label"] for a in result.data["article_summaries"]}
    assert "positive" in labels or "negative" in labels


# ---------------------------------------------------------------------- #
# Fallback chain integration: real tool fails -> mock takes over
# ---------------------------------------------------------------------- #
def test_registry_falls_back_to_mock_when_real_sec_edgar_fails(monkeypatch):
    """
    End-to-end fallback test: force the real sec_edgar implementation to
    raise, and confirm the registry automatically routes to
    sec_edgar_mock.py and returns a successful (fallback) result.
    """

    def fake_get(*args, **kwargs):
        raise httpx.ConnectError("simulated outage")

    monkeypatch.setattr(httpx, "get", fake_get)

    registry = build_default_registry()
    result = registry.dispatch("sec_filing_search", ticker="AAPL", filing_type="10-K")

    assert result.fallback_used is True
    # sec_edgar_mock.py has AAPL/10-K in its mock data, so this should succeed
    assert result.success is True
    assert result.data["ticker"] == "AAPL"
