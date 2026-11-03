"""Regression for hosted runner FTP timeouts: official HTTPS, no partial universes."""

import pandas as pd

from src.data.universe_fetcher import USStockUniverseFetcher


NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


def _nasdaq():
    return pd.DataFrame({
        "Symbol": ["AAPL", "TEST"],
        "Security Name": ["Apple Inc. Common Stock", "Test Security"],
        "Test Issue": ["N", "Y"],
    })


def _other():
    return pd.DataFrame({
        "ACT Symbol": ["IBM"],
        "Security Name": ["IBM Common Stock"],
        "Test Issue": ["N"],
    })


def test_official_exchange_files_use_https_without_changing_symbol_filter(monkeypatch, tmp_path):
    seen = []

    def fake_csv(url, sep):
        seen.append((url, sep))
        return _nasdaq() if url == NASDAQ_URL else _other()

    monkeypatch.setattr(pd, "read_csv", fake_csv)
    fetcher = USStockUniverseFetcher(str(tmp_path))

    assert fetcher.fetch_universe(force_refresh=True) == ["AAPL", "IBM"]
    assert seen == [(NASDAQ_URL, "|"), (OTHER_URL, "|")]
    assert fetcher.cache_file.exists()


def test_does_not_cache_partial_universe_on_single_directory_timeout(monkeypatch, tmp_path):
    def fake_csv(url, sep):
        if url == OTHER_URL:
            raise TimeoutError("official directory fetch timeout")
        return _nasdaq()

    monkeypatch.setattr(pd, "read_csv", fake_csv)
    fetcher = USStockUniverseFetcher(str(tmp_path))
    assert fetcher.fetch_universe(force_refresh=True) == []
    assert not fetcher.cache_file.exists()
