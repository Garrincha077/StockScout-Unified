"""Cross-asset proxy universe for the Returns Leaderboard ("Macro" tab).

Each entry maps a human-readable asset label to a liquid, USD-denominated proxy
ticker that yfinance can fetch (ETFs, FX pairs `…USD=X`, crypto `…-USD`, futures
`…=F`). The list is intentionally editable — add/remove rows freely.

`invert=True` is a reserve flag for FX pairs quoted the "wrong" way (e.g. if a
`USD…=X` pair must be flipped to express the currency's return *in USD*). All
current FX proxies use `…USD=X`, so they need no inversion.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MacroAsset:
    label: str
    ticker: str
    asset_class: str
    invert: bool = False


# Order roughly mirrors the reference "Returns Leaderboard (in USD)" chart.
MACRO_ASSETS: list[MacroAsset] = [
    # --- Equities ---
    MacroAsset("Emerging Markets", "EEM", "Equities"),
    MacroAsset("NASDAQ 100", "QQQ", "Equities"),
    MacroAsset("Magnificent 7", "MAGS", "Equities"),
    MacroAsset("US (S&P 500)", "SPY", "Equities"),
    MacroAsset("Global (ACWI)", "ACWI", "Equities"),
    MacroAsset("Japan", "EWJ", "Equities"),
    MacroAsset("US Small Cap", "IWM", "Equities"),
    MacroAsset("Euro Area", "FEZ", "Equities"),
    MacroAsset("Germany", "EWG", "Equities"),
    MacroAsset("China", "MCHI", "Equities"),
    MacroAsset("United Kingdom", "EWU", "Equities"),
    # --- Commodities ---
    MacroAsset("Industrial Metals", "DBB", "Commodities"),
    MacroAsset("Silver", "SLV", "Commodities"),
    MacroAsset("Gold", "GLD", "Commodities"),
    MacroAsset("WTI Crude Oil", "CL=F", "Commodities"),
    MacroAsset("Commodities (broad)", "DBC", "Commodities"),
    # --- Fixed Income ---
    MacroAsset("US IG Corp", "LQD", "Fixed Income"),
    MacroAsset("US High Yield", "HYG", "Fixed Income"),
    MacroAsset("US T-Bills", "BIL", "Fixed Income"),
    MacroAsset("US TIPS", "TIP", "Fixed Income"),
    MacroAsset("US Treasuries", "IEF", "Fixed Income"),
    # --- Real Estate ---
    MacroAsset("US REITs", "VNQ", "Real Estate"),
    # --- FX (return of the currency in USD) ---
    MacroAsset("US Dollar Index", "UUP", "FX"),
    MacroAsset("Euro", "EURUSD=X", "FX"),
    MacroAsset("British Pound", "GBPUSD=X", "FX"),
    MacroAsset("Canadian Dollar", "CADUSD=X", "FX"),
    MacroAsset("Japanese Yen", "JPYUSD=X", "FX"),
    # --- Crypto ---
    MacroAsset("Bitcoin", "BTC-USD", "Crypto"),
    MacroAsset("Ethereum", "ETH-USD", "Crypto"),
]


# Ordered list of asset classes (for grouping / legend in UI & Telegram).
ASSET_CLASS_ORDER: list[str] = [
    "Equities",
    "Fixed Income",
    "Commodities",
    "FX",
    "Real Estate",
    "Crypto",
]
