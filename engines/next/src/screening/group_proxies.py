"""Liquid ETF proxies used for data-first group leadership.

These are behavioral proxies, not official GICS classifications. A stock is mapped
to the sector/industry proxy whose SPY-relative daily returns correlate most closely
with the stock over the recent six-month window. This keeps the feature fast,
transparent and independent of paid metadata APIs.
"""

SECTOR_PROXIES = {
    "XLC": "Communication Services",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLE": "Energy",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLK": "Technology",
    "XLU": "Utilities",
}

INDUSTRY_PROXIES = {
    "SMH": "Semiconductors",
    "IGV": "Software",
    "XBI": "Biotechnology",
    "IHI": "Medical Devices",
    "IHF": "Health Care Providers",
    "KRE": "Regional Banks",
    "KBE": "Banks",
    "XRT": "Retail",
    "ITB": "Homebuilders",
    "IYT": "Transportation",
    "XOP": "Oil & Gas Exploration",
    "OIH": "Oil Services",
    "GDX": "Gold Miners",
    "COPX": "Copper Miners",
    "ITA": "Aerospace & Defense",
    "JETS": "Airlines",
    "TAN": "Solar",
    "LIT": "Lithium & Batteries",
}

ALL_PROXY_TICKERS = tuple(dict.fromkeys([*SECTOR_PROXIES, *INDUSTRY_PROXIES]))
