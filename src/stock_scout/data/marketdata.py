from __future__ import annotations

import inspect
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from stock_scout.config.schema import Settings
from stock_scout.data.base import BaseDataProvider, ProviderError
from stock_scout.data.factory import build_provider
from stock_scout.data.market_store import MarketDataStore, RepairTarget, market_store_path
from stock_scout.data.universe import build_universe_registry
from stock_scout.utils.dates import history_start, last_trading_day
from stock_scout.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class MarketDataSyncResult:
    attempted: int = 0
    skipped_current: int = 0
    stored_tickers: int = 0
    stored_bars: int = 0
    failed: int = 0
    partial: bool = False
    stop_reason: str = ""
    deferred_tickers: int = 0
    deferred_sample: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    rate_limit_suspected: bool = False
    provider_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "attempted": self.attempted,
            "skipped_current": self.skipped_current,
            "stored_tickers": self.stored_tickers,
            "stored_bars": self.stored_bars,
            "failed": self.failed,
            "partial": self.partial,
            "stop_reason": self.stop_reason,
            "deferred_tickers": self.deferred_tickers,
            "deferred_sample": list(self.deferred_sample),
            "duration_seconds": round(self.duration_seconds, 2),
            "rate_limit_suspected": self.rate_limit_suspected,
            "provider_counts": dict(self.provider_counts),
        }


@dataclass
class MarketDataRepairResult:
    dry_run: bool = False
    attempted: int = 0
    repaired_tickers: int = 0
    repaired_bars: int = 0
    still_failed: int = 0
    skipped_current: int = 0
    provider_counts: dict[str, int] = field(default_factory=dict)
    targets: list[RepairTarget] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self, *, include_targets: bool = False) -> dict:
        out = {
            "dry_run": self.dry_run,
            "attempted": self.attempted,
            "repaired_tickers": self.repaired_tickers,
            "repaired_bars": self.repaired_bars,
            "still_failed": self.still_failed,
            "skipped_current": self.skipped_current,
            "provider_counts": dict(self.provider_counts),
        }
        if include_targets:
            out["targets"] = [_repair_target_payload(t) for t in self.targets]
        if self.failures:
            out["failures"] = list(self.failures)
        return out


def _repair_target_payload(target: RepairTarget) -> dict[str, str]:
    return {
        "ticker": target.ticker,
        "start": target.start.isoformat(),
        "end": target.end.isoformat(),
        "reason": target.reason,
    }


def _today_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sync_universe_registry(settings: Settings, store: MarketDataStore | None = None) -> tuple[int, list[str]]:
    own = store is None
    store = store or MarketDataStore.from_settings(settings)
    try:
        registry = build_universe_registry(settings.universe, settings.project_root)
        written = store.upsert_security_registry(registry.records)
        return written, registry.tickers
    finally:
        if own:
            store.close()


def _seed_ipo_tickers(settings: Settings, years: int = 6) -> list[str]:
    seed_path = settings.project_root / settings.ipo.seed_file
    if not seed_path.exists():
        return []
    try:
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("marketdata.ipo_seed_read_failed", error=repr(e))
        return []
    min_year = date.today().year - max(1, years) + 1
    tickers: list[str] = []
    for year, names in (seed.items() if isinstance(seed, dict) else []):
        try:
            if int(year) < min_year:
                continue
        except (TypeError, ValueError):
            continue
        tickers.extend(str(t).upper().strip() for t in names or [] if str(t).strip())
    return sorted(set(tickers))


def eligible_scope_tickers(store: MarketDataStore, settings: Settings, *, ipo_years: int = 6) -> list[str]:
    """The tickers a scan actually reads, plus what it needs to read them.

    `scan` covers 5877 tickers while the scan itself only looks at the ~2444
    that pass the liquidity gate, so the nightly network delta spends most of
    its budget on data nothing consumes — and then runs out before it reaches
    the tickers that matter, which is what left the store stale and pushed
    `bulk_prewarm` onto the network on 2026-07-25.

    Non-eligible tickers are not abandoned: the Stooq archive re-import rewrites
    full history for the whole universe, so the network delta only has to bridge
    the gap for what gets scanned. Benchmarks and IPO seeds are added explicitly
    — benchmarks are ETFs, so `include_in_scan` is false for them and the
    eligible query would never return them, and a fresh IPO has no liquidity
    snapshot yet by definition.
    """
    eligible = store.scan_universe_tickers(eligible_only=True)
    benchmarks = [str(t).upper() for t in settings.marketdata.benchmarks]
    return sorted({t for t in eligible + benchmarks + _seed_ipo_tickers(settings, ipo_years) if t})


def scope_tickers(settings: Settings, *, scope: str = "scan", ipo_years: int = 6) -> list[str]:
    registry = build_universe_registry(settings.universe, settings.project_root)
    common = registry.tickers
    benchmarks = [str(t).upper() for t in settings.marketdata.benchmarks]
    if scope == "scan":
        tickers = common + benchmarks + _seed_ipo_tickers(settings, ipo_years)
    elif scope == "common":
        tickers = common
    elif scope == "benchmarks":
        tickers = benchmarks
    elif scope == "eligible":
        store = MarketDataStore.from_settings(settings, read_only=True, lock_timeout_seconds=10.0)
        try:
            return eligible_scope_tickers(store, settings, ipo_years=ipo_years)
        finally:
            store.close()
    else:
        raise ValueError("--scope must be scan, common, benchmarks, or eligible")
    return sorted({t for t in tickers if t})


def _provider_chain(settings: Settings, provider_names: Iterable[str] | None = None) -> list[BaseDataProvider]:
    names = list(provider_names or ["yfinance", "tiingo", "stooq"])
    out: list[BaseDataProvider] = []
    for name in names:
        try:
            prov = build_provider(name, settings)
        except Exception as e:  # noqa: BLE001
            log.warning("marketdata.provider_unavailable", provider=name, error=repr(e))
            continue
        if all(prov.name != p.name for p in out):
            out.append(prov)
    return out


def _is_adjusted(settings: Settings) -> bool:
    """Whether fetches should fold in dividends, per the store's price basis.

    Writing the wrong basis into a split-only store splices a 10-36% jump into
    dividend payers, so this is read from config rather than assumed.
    """
    return settings.marketdata.price_basis == "split_div"


def _effective_start(store: MarketDataStore, ticker: str, start: date) -> date:
    ipo = store.ipo_date(ticker)
    return max(start, ipo) if ipo else start


def _upsert_result(
    store: MarketDataStore,
    ticker: str,
    df: pd.DataFrame,
    provider: BaseDataProvider,
    start: date,
    end: date,
    result: MarketDataSyncResult,
    adjusted: bool = True,
) -> bool:
    if df is None or df.empty:
        return False
    clipped = df.copy()
    try:
        idx_dates = pd.to_datetime(clipped.index).date
        mask = [(start <= d <= end) for d in idx_dates]
        clipped = clipped.loc[mask]
    except Exception:  # noqa: BLE001 - normalize/upsert still handles unusual frames
        clipped = df
    if clipped.empty:
        return False
    n = store.upsert_ohlcv(ticker, clipped, provider.name, adjusted=adjusted)
    if n <= 0:
        return False
    result.stored_tickers += 1
    result.stored_bars += n
    result.provider_counts[provider.name] = result.provider_counts.get(provider.name, 0) + 1
    store.mark_sync_state(provider.name, ticker, "daily", status="ok", start=start, end=end)
    return True


def _budget_too_thin(
    elapsed_seconds: float,
    slowest_batch_seconds: float,
    time_budget_seconds: float,
) -> bool:
    """True when the next batch would probably run past the budget.

    A budget checked only *between* units of work does not bind. It is honoured
    right up to the moment a batch starts at 880s of a 900s budget - and then
    that batch runs as long as it likes.

    On 2026-07-30 that is exactly what happened. Individual fetches were timing
    out at ~10s each (`curl: (28) Connection timed out after 10015
    milliseconds`), which is the shape that makes one batch take minutes. The
    refresh sailed past its own 900s budget, and the nightly script's external
    1080s timeout killed it instead - mid-write, leaving a WAL that could not be
    replayed and a night that failed silently.

    Comparing the remaining budget against the slowest batch seen so far turns
    the budget into something that stops the process, and stops it *cleanly*: a
    partial result with the unprocessed tickers deferred, rather than a
    `TerminateProcess` in the middle of a DuckDB write.

    Before any batch has been timed there is nothing to predict from, so this
    returns False and defers to the plain elapsed check rather than guessing.
    """
    if not (time_budget_seconds and time_budget_seconds > 0):
        return False
    if slowest_batch_seconds <= 0:
        return False
    return (elapsed_seconds + slowest_batch_seconds) >= time_budget_seconds


def _looks_like_rate_limit_message(value: object) -> bool:
    msg = str(value or "").lower()
    return "too many requests" in msg or "rate limit" in msg or "rate-limit" in msg or "429" in msg


def _defer_tickers(
    store: MarketDataStore,
    provider_name: str,
    tickers: Iterable[str],
    *,
    start: date,
    end: date,
    reason: str,
    result: MarketDataSyncResult,
) -> None:
    unique = sorted({_ticker for _ticker in (str(t).upper().strip() for t in tickers) if _ticker})
    if not unique:
        return
    result.deferred_tickers += len(unique)
    for ticker in unique[:20]:
        if ticker not in result.deferred_sample:
            result.deferred_sample.append(ticker)
    for ticker in unique:
        store.mark_sync_state(provider_name, ticker, "daily", status="deferred", start=start, end=end, error=reason)


def backfill_marketdata(
    settings: Settings,
    *,
    scope: str = "scan",
    years: int = 10,
    ipo_years: int = 6,
    limit: int = 0,
    update_only: bool = False,
    batch_size: int = 100,
    provider_names: Iterable[str] | None = None,
    batch_delay_seconds: float = 0.0,
    # Three consecutive batches that store nothing is a throttle, not a run of
    # delisted tickers. Measured 2026-07-25: two concurrent bulk downloads
    # returned half their frames empty and four returned none at all — Yahoo
    # sheds load by answering with nothing rather than by erroring, so without
    # this the delta grinds through every remaining batch producing no data and
    # reports success. 0 disables the check.
    max_empty_batches: int = 3,
    time_budget_seconds: float = 0.0,
    allow_partial: bool = False,
    bulk_empty_action: str | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> MarketDataSyncResult:
    """Populate or update local daily OHLCV.

    The job is resumable because each ticker/date upsert is idempotent and every
    provider failure is recorded in provider_sync_state instead of aborting the run.
    """
    started_at = time.monotonic()
    store = MarketDataStore.from_settings(settings)
    # A caller that accepts partial results should not be handed an exception
    # when the provider throttles — that is precisely the partial case.
    bulk_empty_action = str(
        bulk_empty_action or ("stop-partial" if allow_partial else "fail")
    ).strip().lower()
    if bulk_empty_action not in {"fail", "stop-partial"}:
        raise ValueError("--bulk-empty-action must be fail or stop-partial")

    adjusted = _is_adjusted(settings)

    def finish(result: MarketDataSyncResult) -> MarketDataSyncResult:
        result.duration_seconds = time.monotonic() - started_at
        return result

    def budget_exhausted() -> bool:
        return bool(time_budget_seconds and time_budget_seconds > 0 and (time.monotonic() - started_at) >= time_budget_seconds)

    # The longest single batch seen so far, so the budget can be checked against
    # what is about to be started rather than only against what has finished.
    slowest_batch_seconds = 0.0

    def budget_too_thin_for_another_batch() -> bool:
        return _budget_too_thin(
            time.monotonic() - started_at, slowest_batch_seconds, time_budget_seconds
        )

    def mark_partial(
        result: MarketDataSyncResult,
        *,
        reason: str,
        provider_name: str,
        tickers: Iterable[str],
        start: date,
        end: date,
        rate_limit: bool = False,
    ) -> None:
        result.partial = True
        result.stop_reason = reason
        result.rate_limit_suspected = result.rate_limit_suspected or rate_limit
        _defer_tickers(store, provider_name, tickers, start=start, end=end, reason=reason, result=result)

    try:
        _, common_tickers = sync_universe_registry(settings, store)
        if scope == "scan":
            tickers = sorted(
                set(common_tickers + [str(t).upper() for t in settings.marketdata.benchmarks] + _seed_ipo_tickers(settings, ipo_years))
            )
        elif scope == "common":
            tickers = sorted(set(common_tickers))
        elif scope == "eligible":
            # Reuse the open store rather than scope_tickers(), which would open
            # a second connection against a file this one already holds.
            tickers = eligible_scope_tickers(store, settings, ipo_years=ipo_years)
        else:
            tickers = scope_tickers(settings, scope=scope, ipo_years=ipo_years)
        if limit and limit > 0:
            tickers = tickers[:limit]
        end = last_trading_day()
        base_start = history_start(years, end)
        by_start: dict[date, list[str]] = defaultdict(list)
        result = MarketDataSyncResult(attempted=len(tickers))
        # Resolve every ticker's latest bar in one grouped query. Per-ticker
        # max(date) against the unindexed ohlcv_daily is a full scan each, which
        # made the nightly `marketdata update` spend minutes deciding it had
        # nothing to do.
        latest_by_ticker: dict[str, date] = {}
        if update_only:
            latest_by_ticker = store.latest_bar_dates(tickers)
        for ticker in tickers:
            start = _effective_start(store, ticker, base_start)
            if update_only:
                latest = latest_by_ticker.get(str(ticker).strip().upper())
                if latest is not None and latest >= end:
                    result.skipped_current += 1
                    continue
                if latest is not None:
                    start = max(start, latest + timedelta(days=1))
            else:
                missing = store.missing_ranges(ticker, start, end)
                if not missing:
                    result.skipped_current += 1
                    continue
                # Fetch from the first gap through the end; the upsert dedupes.
                start = missing[0].start
            if start <= end:
                by_start[start].append(ticker)

        providers = _provider_chain(settings, provider_names)
        if not providers:
            raise RuntimeError("No market-data providers are available")

        for start, group in sorted(by_start.items()):
            remaining = list(group)
            first = providers[0]
            bulk = getattr(first, "get_bulk_daily_ohlcv", None)
            if bulk is not None:
                post_bulk: list[str] = []
                empty_bulk_streak = 0
                effective_batch_size = max(1, batch_size)
                total_batches = (len(remaining) + effective_batch_size - 1) // effective_batch_size
                for i in range(0, len(remaining), effective_batch_size):
                    if budget_exhausted() or budget_too_thin_for_another_batch():
                        if not allow_partial:
                            raise RuntimeError("market-data update time budget exhausted")
                        unprocessed = remaining[i:]
                        mark_partial(
                            result,
                            reason="time_budget_exhausted",
                            provider_name=first.name,
                            tickers=unprocessed,
                            start=start,
                            end=end,
                        )
                        return finish(result)
                    batch = remaining[i : i + effective_batch_size]
                    batch_no = (i // effective_batch_size) + 1
                    if progress_callback:
                        progress_callback(
                            {
                                "event": "bulk_batch_start",
                                "provider": first.name,
                                "start": start.isoformat(),
                                "end": end.isoformat(),
                                "batch": batch_no,
                                "batches": total_batches,
                                "tickers": len(batch),
                            }
                        )
                    batch_started_at = time.monotonic()
                    try:
                        frames = (
                            bulk(batch, start, end, adjusted=adjusted)
                            if "adjusted" in inspect.signature(bulk).parameters
                            else bulk(batch, start, end)
                        )
                    except Exception as e:  # noqa: BLE001
                        # Timed in `finally` rather than here: a batch that fails
                        # slowly - which is what a wall of curl timeouts looks
                        # like - is exactly the one the budget needs to know
                        # about, and it leaves by this path.
                        log.warning("marketdata.bulk_failed", provider=first.name, count=len(batch), error=str(e)[:200])
                        post_bulk.extend(batch)
                        empty_bulk_streak += 1
                        if max_empty_batches and empty_bulk_streak >= max_empty_batches:
                            if allow_partial and bulk_empty_action == "stop-partial":
                                mark_partial(
                                    result,
                                    reason=f"{first.name}_empty_batches",
                                    provider_name=first.name,
                                    tickers=remaining[i:],
                                    start=start,
                                    end=end,
                                    rate_limit=_looks_like_rate_limit_message(e),
                                )
                                return finish(result)
                            raise RuntimeError(
                                f"{first.name} returned {empty_bulk_streak} empty/failed bulk batch(es); "
                                "likely rate-limited. Retry later or lower --batch-size."
                            ) from e
                        if batch_delay_seconds > 0:
                            time.sleep(batch_delay_seconds)
                        continue
                    finally:
                        # Wall clock on purpose, deliberate delay included: the
                        # budget is wall clock, so anything the loop spends per
                        # batch is what the next batch has to fit inside.
                        slowest_batch_seconds = max(
                            slowest_batch_seconds, time.monotonic() - batch_started_at
                        )
                    stored_this_batch = 0
                    for ticker in batch:
                        df = frames.get(ticker) if isinstance(frames, dict) else None
                        if _upsert_result(store, ticker, df, first, start, end, result, adjusted):
                            stored_this_batch += 1
                        else:
                            post_bulk.append(ticker)
                    if stored_this_batch == 0 and batch:
                        empty_bulk_streak += 1
                    else:
                        empty_bulk_streak = 0
                    if progress_callback:
                        progress_callback(
                            {
                                "event": "bulk_batch_done",
                                "provider": first.name,
                                "start": start.isoformat(),
                                "end": end.isoformat(),
                                "batch": batch_no,
                                "batches": total_batches,
                                "tickers": len(batch),
                                "stored_tickers": stored_this_batch,
                                "failed_or_missing": len(batch) - stored_this_batch,
                                "empty_streak": empty_bulk_streak,
                            }
                        )
                    if max_empty_batches and empty_bulk_streak >= max_empty_batches:
                        if allow_partial and bulk_empty_action == "stop-partial":
                            mark_partial(
                                result,
                                reason=f"{first.name}_empty_batches",
                                provider_name=first.name,
                                tickers=remaining[i + effective_batch_size :],
                                start=start,
                                end=end,
                                rate_limit=True,
                            )
                            _defer_tickers(
                                store,
                                first.name,
                                post_bulk,
                                start=start,
                                end=end,
                                reason=f"{first.name}_missing_or_empty_before_stop",
                                result=result,
                            )
                            return finish(result)
                        raise RuntimeError(
                            f"{first.name} returned {empty_bulk_streak} empty bulk batch(es); "
                            "likely rate-limited. Retry later or lower --batch-size."
                        )
                    if batch_delay_seconds > 0 and batch_no < total_batches:
                        time.sleep(batch_delay_seconds)
                remaining = post_bulk

            fallback_providers = providers[1:] if bulk is not None else providers
            if remaining and allow_partial and not fallback_providers:
                mark_partial(
                    result,
                    reason="no_fallback_provider_in_partial_mode",
                    provider_name=first.name,
                    tickers=remaining,
                    start=start,
                    end=end,
                )
                continue

            for ticker in remaining:
                if budget_exhausted():
                    if not allow_partial:
                        raise RuntimeError("market-data update time budget exhausted")
                    mark_partial(
                        result,
                        reason="time_budget_exhausted",
                        provider_name=providers[-1].name,
                        tickers=remaining[remaining.index(ticker) :],
                        start=start,
                        end=end,
                    )
                    return finish(result)
                stored = False
                for provider in fallback_providers:
                    try:
                        df = provider.get_daily_ohlcv(ticker, start, end, adjusted=adjusted)
                    except ProviderError as e:
                        store.mark_sync_state(provider.name, ticker, "daily", status="failed", start=start, end=end, error=str(e))
                        continue
                    except Exception as e:  # noqa: BLE001
                        store.mark_sync_state(provider.name, ticker, "daily", status="failed", start=start, end=end, error=repr(e))
                        continue
                    if _upsert_result(store, ticker, df, provider, start, end, result, adjusted):
                        stored = True
                        break
                if not stored:
                    result.failed += 1
                    # Attribute the final miss to the last configured provider for audit.
                    last = providers[-1]
                    store.mark_sync_state(
                        last.name,
                        ticker,
                        "daily",
                        status="failed",
                        start=start,
                        end=end,
                        error="no provider returned OHLCV",
                    )
        return finish(result)
    finally:
        store.close()


def _target_repaired(store: MarketDataStore, target: RepairTarget) -> bool:
    if target.reason in {"no_ohlcv", "missing_latest"}:
        latest = store.latest_bar_date(target.ticker)
        return latest is not None and latest >= target.end
    if target.reason == "eligible_gaps":
        return not store.missing_ranges(target.ticker, target.start, target.end)
    return False


def repair_marketdata(
    settings: Settings,
    *,
    mode: str = "all",
    scope: str = "scan",
    years: int = 10,
    limit: int = 250,
    provider_names: Iterable[str] | None = None,
    sleep_seconds: float = 1.2,
    time_budget_seconds: float = 0.0,
    dry_run: bool = False,
    progress_callback: Callable[[dict], None] | None = None,
) -> MarketDataRepairResult:
    """Targeted market-data repair for stale/missing local DuckDB coverage."""
    started_at = time.monotonic()
    store = MarketDataStore.from_settings(settings, read_only=dry_run, lock_timeout_seconds=10.0 if dry_run else 60.0)
    try:
        end = last_trading_day()
        targets = store.repair_targets(
            mode=mode,
            scope=scope,
            coverage_years=years,
            latest_expected=end,
            limit=limit,
        )
        result = MarketDataRepairResult(dry_run=dry_run, attempted=len(targets), targets=targets)
        if dry_run or not targets:
            return result

        providers = _provider_chain(settings, provider_names or ["tiingo", "stooq", "yfinance"])
        if not providers:
            raise RuntimeError("No market-data providers are available")
        adjusted = _is_adjusted(settings)

        for index, target in enumerate(targets, start=1):
            if time_budget_seconds and time_budget_seconds > 0 and (time.monotonic() - started_at) >= time_budget_seconds:
                result.failures.append(
                    {
                        "ticker": target.ticker,
                        "reason": target.reason,
                        "error": "repair time budget exhausted",
                    }
                )
                break
            if _target_repaired(store, target):
                result.skipped_current += 1
                continue
            if progress_callback:
                progress_callback(
                    {
                        "event": "repair_target_start",
                        "index": index,
                        "total": len(targets),
                        **_repair_target_payload(target),
                    }
                )
            repaired = False
            last_error = "no provider returned OHLCV"
            for provider in providers:
                try:
                    df = provider.get_daily_ohlcv(target.ticker, target.start, target.end, adjusted=adjusted)
                except ProviderError as e:
                    last_error = str(e)
                    store.mark_sync_state(
                        provider.name,
                        target.ticker,
                        "daily",
                        status="failed",
                        start=target.start,
                        end=target.end,
                        error=last_error,
                    )
                    continue
                except Exception as e:  # noqa: BLE001
                    last_error = repr(e)
                    store.mark_sync_state(
                        provider.name,
                        target.ticker,
                        "daily",
                        status="failed",
                        start=target.start,
                        end=target.end,
                        error=last_error,
                    )
                    continue
                if df is None or df.empty:
                    last_error = "empty OHLCV"
                    store.mark_sync_state(
                        provider.name,
                        target.ticker,
                        "daily",
                        status="failed",
                        start=target.start,
                        end=target.end,
                        error=last_error,
                    )
                    continue
                n = store.upsert_ohlcv(target.ticker, df, provider.name, adjusted=adjusted)
                if n <= 0:
                    last_error = "normalized OHLCV was empty"
                    store.mark_sync_state(
                        provider.name,
                        target.ticker,
                        "daily",
                        status="failed",
                        start=target.start,
                        end=target.end,
                        error=last_error,
                    )
                    continue
                store.mark_sync_state(provider.name, target.ticker, "daily", status="ok", start=target.start, end=target.end)
                if _target_repaired(store, target):
                    result.repaired_tickers += 1
                    result.repaired_bars += n
                    result.provider_counts[provider.name] = result.provider_counts.get(provider.name, 0) + 1
                    repaired = True
                    if progress_callback:
                        progress_callback(
                            {
                                "event": "repair_target_done",
                                "ticker": target.ticker,
                                "provider": provider.name,
                                "bars": n,
                                "repaired": True,
                            }
                        )
                    break
                last_error = "provider returned partial OHLCV"
            if not repaired:
                result.still_failed += 1
                result.failures.append({"ticker": target.ticker, "reason": target.reason, "error": last_error[:200]})
                if progress_callback:
                    progress_callback(
                        {
                            "event": "repair_target_done",
                            "ticker": target.ticker,
                            "provider": "",
                            "bars": 0,
                            "repaired": False,
                            "error": last_error[:200],
                        }
                    )
            if sleep_seconds > 0 and index < len(targets):
                time.sleep(sleep_seconds)
        return result
    finally:
        store.close()


def refresh_marketdata(
    settings: Settings,
    *,
    mode: str | None = None,
    archive_dir: str | Path | None = None,
    force: bool = False,
    after_close: bool = False,
    provider_names: Iterable[str] | None = None,
    time_budget_seconds: float = 0.0,
    allow_partial: bool = False,
    scope: str | None = None,
) -> dict:
    """Bring the store up to date from the Stooq archive, the network, or both.

    In `auto`, the archive is imported only when it is actually newer than the
    store, and the network then covers whatever days remain. Both sources are on
    the same split-only basis, so combining them cannot create a seam — which is
    what makes "user forgot to re-download" a non-event: the import is skipped,
    the delta still runs, and readiness reports the staleness.
    """
    from stock_scout.data.stooq_archive import archive_state

    chosen = (mode or settings.marketdata.refresh_mode or "auto").strip().lower()
    if chosen not in ("archive", "network", "auto"):
        raise ValueError("--mode must be archive, network, or auto")
    root = Path(archive_dir or settings.marketdata.stooq_archive_dir)
    if not root.is_absolute():
        root = settings.project_root / root

    if after_close:
        log.info("marketdata.refresh.after_close", mode=chosen)
    out: dict = {"mode": chosen, "archive_dir": str(root)}
    state = archive_state(root)
    archive_max = state.get("archive_max_date")
    out["archive_max_date"] = archive_max.isoformat() if archive_max else None
    out["archive_files"] = state.get("us_files", 0)

    def store_snapshot() -> tuple[date | None, dict | None]:
        # A read-only DuckDB connection cannot create a new file.  Refresh is
        # also the bootstrap command, so the first network refresh must reach
        # backfill_marketdata(), which creates the store in read-write mode.
        if not market_store_path(settings).exists():
            return None, None
        store = MarketDataStore.from_settings(settings, read_only=True, lock_timeout_seconds=10.0)
        try:
            return store.latest_bar_date_overall(), store.archive_import_state()
        finally:
            store.close()

    store_max, imported = store_snapshot()
    out["store_max_date_before"] = store_max.isoformat() if store_max else None
    last_import_max: date | None = None
    if imported and imported.get("archive_max_date"):
        try:
            last_import_max = date.fromisoformat(str(imported["archive_max_date"]))
        except ValueError:
            last_import_max = None
    out["last_imported_archive_max_date"] = (
        last_import_max.isoformat() if last_import_max else None
    )

    if chosen in ("archive", "auto"):
        if not state.get("exists"):
            out["archive"] = "skipped: directory not found"
            if chosen == "archive" and not force:
                raise RuntimeError(f"Stooq archive not found at {root}")
        elif archive_max is None:
            out["archive"] = "skipped: no readable bars in archive"
        else:
            age_days = (last_trading_day() - archive_max).days
            out["archive_age_days"] = age_days
            too_old = age_days > int(settings.marketdata.archive_max_age_days)
            # Compare against what was last imported, not against the store's
            # newest bar. The store's max date moves every night from the
            # network, so "archive newer than store" goes false permanently
            # after the first delta — and a freshly downloaded archive then
            # never gets imported again. The store silently drifts onto
            # network-sourced data and the archive stops being the authority,
            # which is the one thing the split-only design depends on.
            unimported = last_import_max is None or archive_max > last_import_max
            if chosen == "auto" and not unimported:
                out["archive"] = "skipped: this archive has already been imported"
            elif too_old and not force:
                out["archive"] = f"skipped: archive is {age_days}d old (use --force)"
            else:
                out["archive"] = (
                    "would import (run `marketdata import-stooq` then rebuild)"
                )
                out["archive_import_required"] = True

    # Bundles before the network, always. A bundle is the same day the delta
    # would assemble, on the same basis, for seconds instead of ~20 minutes —
    # measured 2026-07-25 at 22,081 rows in 3.96s against 2,195 stale tickers.
    # Whatever it covers, the delta then has nothing left to fetch.
    if chosen in ("archive", "auto"):
        try:
            from stock_scout.data.stooq_archive import import_daily_bundles

            dirs = [
                d if Path(d).is_absolute() else settings.project_root / d
                for d in (settings.marketdata.stooq_bundle_dirs or [])
            ]
            bundles = import_daily_bundles(settings, dirs, skip_imported=True)
            out["bundles"] = {
                "rows_written": bundles.get("rows_written", 0),
                "tickers": bundles.get("tickers", 0),
                "max_date": bundles.get("max_date"),
                "files": len(bundles.get("files", [])),
                "skipped": len(bundles.get("skipped_files", [])),
            }
        except Exception as e:  # noqa: BLE001 - a bad bundle must not stop the night
            log.warning("marketdata.bundle_import_failed", error=repr(e))
            out["bundles"] = {"error": repr(e)}
    else:
        out["bundles"] = "skipped"

    if chosen in ("network", "auto"):
        net_scope = (scope or settings.marketdata.refresh_scope or "eligible").strip().lower()
        out["network_scope"] = net_scope
        result = backfill_marketdata(
            settings,
            scope=net_scope,
            update_only=True,
            provider_names=provider_names,
            time_budget_seconds=time_budget_seconds,
            allow_partial=allow_partial,
        )
        out["network"] = result.to_dict()
    else:
        out["network"] = "skipped"

    after, _ = store_snapshot()
    out["store_max_date_after"] = after.isoformat() if after else None
    return out


def compute_liquidity_snapshots(settings: Settings) -> tuple[int, int]:
    store = MarketDataStore.from_settings(settings)
    try:
        tickers = store.scan_universe_tickers(eligible_only=False)
        as_of = last_trading_day()
        rows: list[dict] = []
        eligible_count = 0
        # One windowed query for the whole universe instead of a read per
        # ticker; the loop below now only applies thresholds.
        metrics = store.liquidity_metrics(
            tickers, lookback_bars=max(60, settings.prefilter.min_history_days)
        )
        for ticker in tickers:
            m = metrics.get(str(ticker).strip().upper())
            reasons: list[str] = []
            if m is None or not m["bars_available"]:
                reasons.append("missing_ohlcv")
                last_close = avg_vol = avg_dvol = 0.0
                bars = 0
            else:
                last_close = float(m["last_close"])
                avg_vol = float(m["avg_volume_50d"])
                avg_dvol = float(m["avg_dollar_volume_50d"])
                bars = int(m["bars_available"])
                if bars < settings.prefilter.min_history_days:
                    reasons.append(f"bars<{settings.prefilter.min_history_days}")
                if last_close < settings.prefilter.min_price:
                    reasons.append(f"price<{settings.prefilter.min_price}")
                if avg_vol < settings.prefilter.min_avg_volume_50d:
                    reasons.append(f"avg_vol_50d<{settings.prefilter.min_avg_volume_50d}")
                if avg_dvol < settings.prefilter.min_avg_dollar_volume_50d:
                    reasons.append(f"avg_$vol_50d<{settings.prefilter.min_avg_dollar_volume_50d:.0f}")
            eligible = not reasons
            if eligible:
                eligible_count += 1
            rows.append(
                {
                    "ticker": ticker,
                    "last_close": last_close,
                    "avg_volume_50d": avg_vol,
                    "avg_dollar_volume_50d": avg_dvol,
                    "bars_available": bars,
                    "eligible": eligible,
                    "reason": ",".join(reasons),
                }
            )
        store.write_liquidity_snapshot(rows, as_of)
        return len(rows), eligible_count
    finally:
        store.close()


def sync_ipo_cache_to_store(settings: Settings) -> int:
    path = settings.project_root / "data" / "ipo_cache.parquet"
    if not path.exists():
        return 0
    store = MarketDataStore.from_settings(settings)
    try:
        df = pd.read_parquet(path)
        if df.empty:
            return 0
        return store.upsert_ipo_dates(df.to_dict("records"))
    finally:
        store.close()
