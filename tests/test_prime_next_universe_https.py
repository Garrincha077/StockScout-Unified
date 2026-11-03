"""No drift in the pinned Next universe: HTTPS is transport-only and fail-closed."""

import pytest

from scripts.prime_next_universe_https import (
    OFFICIAL_DIRECTORY_TRANSPORTS,
    ensure_complete_directory,
    official_https_url,
)


def test_only_two_official_nasdaq_urls_are_remapped():
    assert len(OFFICIAL_DIRECTORY_TRANSPORTS) == 2
    for old, new in OFFICIAL_DIRECTORY_TRANSPORTS.items():
        assert old.startswith("ftp://ftp.nasdaqtrader.com/symboldirectory/")
        assert new.startswith("https://www.nasdaqtrader.com/dynamic/SymDir/")
        assert old.rsplit("/", 1)[-1] == new.rsplit("/", 1)[-1]
        assert official_https_url(old) == new
    assert official_https_url("https://example.test/market.csv") == (
        "https://example.test/market.csv"
    )


@pytest.mark.parametrize("nasdaq,other,symbols", [
    (0, 9, ["IBM"]),
    (7, 0, ["AAPL"]),
    (0, 0, []),
    (7, 9, []),
])
def test_refuse_partial_or_empty_directory_and_discard_partial_cache(
    tmp_path, nasdaq, other, symbols
):
    cache_path = tmp_path / "us_stock_universe.pkl"
    cache_path.write_bytes(b"partial-cache-not-a-valid-universe")
    info = {"metadata": {"nasdaq_count": nasdaq, "other_count": other}}
    with pytest.raises(RuntimeError, match="fail closed"):
        ensure_complete_directory(info, symbols, cache_path)
    assert not cache_path.exists()


def test_complete_directory_keeps_cache(tmp_path):
    cache_path = tmp_path / "us_stock_universe.pkl"
    cache_path.write_bytes(b"cache")
    info = {"metadata": {"nasdaq_count": 15, "other_count": 12}}
    ensure_complete_directory(info, ["AAPL", "IBM"], cache_path)
    assert cache_path.read_bytes() == b"cache"
