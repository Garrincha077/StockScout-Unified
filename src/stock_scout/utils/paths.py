from __future__ import annotations

from pathlib import Path


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_ticker_filename(ticker: str) -> str:
    """Make a ticker safe for use as a filename on Windows.

    Replaces '/', '\\', '.', ':' with '_'. Yahoo uses dots for class shares
    (BRK.B), IBKR uses '.' or ' ' — we normalise on disk.
    """
    return ticker.replace("/", "_").replace("\\", "_").replace(".", "_").replace(":", "_").upper()


def cache_path_for(base_dir: str | Path, provider: str, ticker: str, frequency: str = "daily") -> Path:
    """Return the parquet path for a given provider/ticker/frequency."""
    base = Path(base_dir) / provider / frequency
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{safe_ticker_filename(ticker)}.parquet"
