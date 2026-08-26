from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from stock_scout.data.base import (
    BaseDataProvider,
    OHLCVFrame,
    ProviderHealth,
    Quote,
    normalize_ohlcv,
)
from stock_scout.utils.logging import get_logger
from stock_scout.utils.paths import safe_ticker_filename

log = get_logger(__name__)


class CSVDataProvider(BaseDataProvider):
    """Fallback provider that reads local CSVs. Layout:
        {csv_dir}/{TICKER}.csv  (daily bars)
        {csv_dir}/weekly/{TICKER}.csv  (weekly bars, optional)

    Expected columns (case-insensitive): date, open, high, low, close, volume
    """

    name = "csv"

    def __init__(self, csv_dir: str | Path):
        self.csv_dir = Path(csv_dir)
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        (self.csv_dir / "weekly").mkdir(parents=True, exist_ok=True)

    def get_universe(self) -> list[str]:
        return sorted(p.stem for p in self.csv_dir.glob("*.csv"))

    def _read(self, path: Path) -> OHLCVFrame:
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_csv(path)
        # Find the date column (case-insensitive)
        date_col = next((c for c in df.columns if c.lower() == "date"), None)
        if date_col is None:
            log.warning("csv.no_date_col", path=str(path))
            return pd.DataFrame()
        df[date_col] = pd.to_datetime(df[date_col])
        df = df.set_index(date_col)
        return normalize_ohlcv(df)

    def get_daily_ohlcv(
        self, ticker: str, start: date, end: date, adjusted: bool = True
    ) -> OHLCVFrame:
        p = self.csv_dir / f"{safe_ticker_filename(ticker)}.csv"
        df = self._read(p)
        if df.empty:
            return df
        return df.loc[str(start) : str(end)]

    def get_weekly_ohlcv(self, ticker: str, start: date, end: date) -> OHLCVFrame:
        p = self.csv_dir / "weekly" / f"{safe_ticker_filename(ticker)}.csv"
        df = self._read(p)
        if not df.empty:
            return df.loc[str(start) : str(end)]
        # If no native weekly CSV, derive from daily.
        daily = self.get_daily_ohlcv(ticker, start, end)
        if daily.empty:
            return daily
        agg = daily.resample("W-FRI").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        return agg.dropna(how="all")

    def get_latest_quote(self, ticker: str) -> Quote | None:
        p = self.csv_dir / f"{safe_ticker_filename(ticker)}.csv"
        df = self._read(p)
        if df.empty:
            return None
        last = df.iloc[-1]
        price = float(last["close"]) if pd.notna(last["close"]) else None
        if price is None:
            return None
        return Quote(
            ticker=ticker,
            price=price,
            volume=int(last["volume"]) if pd.notna(last["volume"]) else None,
            timestamp=datetime.now(timezone.utc),
            provider=self.name,
        )

    def validate_symbol(self, ticker: str) -> bool:
        p = self.csv_dir / f"{safe_ticker_filename(ticker)}.csv"
        return p.exists()

    def health_check(self) -> ProviderHealth:
        count = len(self.get_universe())
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            detail=f"csv_dir={self.csv_dir} files={count}",
        )
