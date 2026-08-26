from __future__ import annotations

from typing import TYPE_CHECKING

from stock_scout.config.schema import Settings

if TYPE_CHECKING:
    from stock_scout.data.base import BaseDataProvider


def build_provider(name: str, settings: Settings) -> "BaseDataProvider":
    """Instantiate a data provider by short name.

    Lazy imports keep optional SDK dependencies from being required unless
    the corresponding provider is requested.
    """
    name = (name or "").lower().strip()

    if name == "yfinance":
        from stock_scout.data.providers.yfinance_provider import YFinanceDataProvider

        return YFinanceDataProvider(settings.yfinance)

    if name == "csv":
        from stock_scout.data.providers.csv_provider import CSVDataProvider

        csv_dir = settings.project_root / "data" / "csv"
        return CSVDataProvider(csv_dir=csv_dir)

    if name == "ibkr":
        from stock_scout.data.providers.ibkr_provider import IBKRDataProvider

        return IBKRDataProvider(settings.ibkr)

    if name == "alpaca":
        from stock_scout.data.providers.alpaca_provider import AlpacaDataProvider
        from stock_scout.config.loader import load_env

        return AlpacaDataProvider(settings.alpaca, load_env())

    if name == "tiingo":
        from stock_scout.data.providers.tiingo_provider import TiingoDataProvider
        from stock_scout.config.loader import load_env

        return TiingoDataProvider(settings.tiingo, load_env())

    if name == "fmp":
        from stock_scout.data.providers.fmp_provider import FMPDataProvider
        from stock_scout.config.loader import load_env

        return FMPDataProvider(settings.fmp, load_env())

    if name == "stooq":
        from stock_scout.data.providers.stooq_provider import StooqDataProvider

        return StooqDataProvider(
            timeout_seconds=settings.yfinance.request_timeout_seconds,
        )

    if name == "twelvedata":
        from stock_scout.data.providers.twelve_data_provider import TwelveDataDataProvider
        from stock_scout.config.loader import load_env

        return TwelveDataDataProvider(settings.twelvedata, load_env())

    raise ValueError(f"Unknown provider: {name!r}")
