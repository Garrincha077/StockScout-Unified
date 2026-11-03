"""Preload the pinned Next scanner's unchanged stock-universe cache over HTTPS.

Nasdaq Trader serves the exact same two official symbol directories via HTTPS.
The pinned engine still performs all its own parsing, filtering and sorting.
No synthetic prices, adjusted universes or partial-exchange fallback is allowed.
"""

import sys
from pathlib import Path

import pandas as pd

# Explicit official transport aliases; do not substitute a third-party universe.
OFFICIAL_DIRECTORY_TRANSPORTS = {
    "ftp://ftp.nasdaqtrader.com/symboldirectory/nasdaqlisted.txt": (
        "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
    ),
    "ftp://ftp.nasdaqtrader.com/symboldirectory/otherlisted.txt": (
        "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
    ),
}


def official_https_url(url):
    """Only remap the two exact Nasdaq Trader directory endpoints."""
    return OFFICIAL_DIRECTORY_TRANSPORTS.get(url, url)


def ensure_complete_directory(info, symbols, cache_path):
    """Refuse any missing exchange, even if the legacy fetcher cached one."""
    source_counts = (info.get("metadata") or {})
    nasdaq = int(source_counts.get("nasdaq_count") or 0)
    other = int(source_counts.get("other_count") or 0)
    if not symbols or not nasdaq or not other:
        Path(cache_path).unlink(missing_ok=True)
        raise RuntimeError(
            "Official Nasdaq Trader universe incomplete; fail closed "
            f"(nasdaq_rows={nasdaq}, other_rows={other}, symbols={len(symbols)})"
        )
    print(
        "Official Nasdaq Trader HTTPS directories validated: "
        f"nasdaq_rows={nasdaq}, other_rows={other}, "
        f"filtered_symbols={len(symbols)}; pinned Next filtering unchanged."
    )


def main():
    # Run from engines/next to preserve the pinned engine's relative cache path.
    root = Path(__file__).resolve().parents[1]
    engine_root = root / "engines" / "next"
    if Path.cwd().resolve() != engine_root.resolve():
        raise RuntimeError("HTTPS prefetch must run with working-directory=engines/next")
    sys.path.insert(0, str(engine_root))
    from src.data.universe_fetcher import USStockUniverseFetcher

    original_read_csv = pd.read_csv

    def official_read_csv(filepath_or_buffer, *args, **kwargs):
        if isinstance(filepath_or_buffer, str):
            filepath_or_buffer = official_https_url(filepath_or_buffer)
        return original_read_csv(filepath_or_buffer, *args, **kwargs)

    # Scope transport adaptation to this one prefetch; never patch production code.
    pd.read_csv = official_read_csv
    try:
        fetcher = USStockUniverseFetcher()
        symbols = fetcher.fetch_universe(force_refresh=True)
        metadata = (
            fetcher.get_universe_info() if fetcher.cache_file.exists() else {}
        )
        ensure_complete_directory(metadata, symbols, fetcher.cache_file)
    finally:
        pd.read_csv = original_read_csv


if __name__ == "__main__":
    main()
