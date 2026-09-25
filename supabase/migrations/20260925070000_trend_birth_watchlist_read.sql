-- Allow the OIDC-scoped server-only operations function to read owner watchlist tickers.
grant select on stockscout_unified_api.unified_watchlist_items to service_role;
