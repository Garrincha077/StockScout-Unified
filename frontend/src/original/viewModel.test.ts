import assert from "node:assert/strict";
import test from "node:test";
import {
  flattenSourceValues,
  filterOriginalRows,
  originalSummary,
  selectOriginalRows,
  type OriginalCandidate,
  type OriginalTabState,
} from "./viewModel.ts";

const rows: OriginalCandidate[] = [
  {
    ticker: "AAA",
    price: 100,
    originalBuyScore: 90,
    originalBuy: true,
    originalRunBuySignal: true,
    originalRR: 3,
    originalTTPasses: 8,
    originalVcpQuality: 82,
    originalEntryQuality: "excellent",
    originalSellScore: 0,
    originalSell: false,
    originalEngine: {
      buy: { reasons: ["Strong trend", "Volume confirmed"], components: { trend: 35 } },
    },
  },
  {
    ticker: "BBB",
    price: 50,
    originalBuyScore: 72,
    originalBuy: true,
    originalRunBuySignal: false,
    originalRR: 1.5,
    originalTTPasses: 7,
    originalVcpQuality: 40,
    originalEntryQuality: "watch",
    originalSellScore: 0,
    originalSell: false,
    originalEngine: { buy: { sourceReason: "Market gate closed" } },
  },
  {
    ticker: "CCC",
    price: 25,
    originalBuyScore: null,
    originalSellScore: 84,
    originalSell: true,
    originalRunSellSignal: true,
    originalSellSeverity: "high",
    stage: 4,
    originalEngine: {
      phase: 4,
      sell: { isSell: true, breakdownLevel: 28, reasons: ["Phase 4"] },
    },
  },
];

const state = (tab: "buy" | "sell"): OriginalTabState => ({
  scope: "emitted",
  query: "",
  sort: { key: "score", direction: "desc" },
  page: 0,
});
test("summary counts emitted signals and top emitted BUY score", () => {
  const summary = originalSummary(rows, {
    originalSignalGate: { spy: { phase: 2, phase_name: "Stage 2", trend: "Bullish" } },
  });
  assert.deepEqual(summary, {
    buySignals: 1,
    sellSignals: 1,
    topScore: 90,
    universe: 3,
    spyPhase: 2,
    spyPhaseName: "Stage 2",
    spyTrend: "Bullish",
  });
});

test("BUY and SELL scopes stay isolated and default to emitted rows", () => {
  const buy = selectOriginalRows(rows, "buy", state("buy"));
  const sell = selectOriginalRows(rows, "sell", state("sell"));
  assert.deepEqual(buy.rows.map((row) => row.ticker), ["AAA"]);
  assert.deepEqual(sell.rows.map((row) => row.ticker), ["CCC"]);
  assert.equal(buy.rows[0]?.originalSellScore, 0);
  assert.equal(sell.rows[0]?.originalBuyScore, null);
});

test("raw and all-scored scopes include the intended underlying candidates", () => {
  assert.deepEqual(filterOriginalRows(rows, "buy", "raw", "").map((row) => row.ticker), ["AAA", "BBB"]);
  assert.deepEqual(filterOriginalRows(rows, "buy", "all", "").map((row) => row.ticker), ["AAA", "BBB"]);
  assert.deepEqual(filterOriginalRows(rows, "sell", "raw", "").map((row) => row.ticker), ["CCC"]);
});

test("default sorting is descending by tab score with deterministic ticker tie-break", () => {
  const buy = selectOriginalRows(rows, "buy", { ...state("buy"), scope: "raw" });
  assert.deepEqual(buy.filtered.map((row) => row.ticker), ["AAA", "BBB"]);
  const sell = selectOriginalRows(rows, "sell", state("sell"));
  assert.deepEqual(sell.filtered.map((row) => row.ticker), ["CCC"]);
});

test("nested source values flatten scalars, arrays and missing values", () => {
  assert.deepEqual(flattenSourceValues({
    score: 4,
    flags: [true, false],
    details: { reason: "watch" },
    missing: null,
  }), [
    { path: "score", value: 4 },
    { path: "flags[0]", value: true },
    { path: "flags[1]", value: false },
    { path: "details.reason", value: "watch" },
    { path: "missing", value: null },
  ]);
});
