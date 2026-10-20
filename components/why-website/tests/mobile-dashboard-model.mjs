// node --experimental-strip-types --test tests/mobile-dashboard-model.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import * as m from "../components/profile/app/model.ts";

const colors = { green: "G", red: "R", gold: "Y", ash: "A" };

test("long and short P&L and ROE on margin", () => {
  const long = m.openTrade({ market: "BTCUSDT-PERP", direction: "long", qty: "0.01", entry_price: "80000" }, "Yellow Pro", 5);
  const short = m.openTrade({ market: "BTCUSDT-PERP", direction: "short", qty: "-0.01", entry_price: "80000" }, "Yellow Pro", 5);
  const up = m.livePnl(long, 80800), dn = m.livePnl(short, 80800);
  assert.equal(Math.round(up.pnl * 100) / 100, 8);
  assert.equal(Math.round(dn.pnl * 100) / 100, -8);
  // margin = 800 / 5 = 160 → 8 / 160 = 5%
  assert.equal(Math.round(up.pct * 100) / 100, 5);
  assert.equal(Math.round(dn.pct * 100) / 100, -5);
  assert.equal(long.notional, 800);
});

test("backend stop and target levels are carried through", () => {
  const t = m.openTrade({ market: "ETHUSDT-PERP", direction: "short", qty: "0.1", entry_price: "2500", stop_loss: "2520", take_profit: "2450", leverage: "4" }, "Yellow Pro", 10);
  assert.equal(t.stopLoss, 2520); assert.equal(t.takeProfit, 2450); assert.equal(t.leverage, 4);
  const none = m.openTrade({ market: "ETHUSDT-PERP", direction: "long", qty: "0.1", entry_price: "2500" }, "Yellow Pro", 10);
  assert.equal(none.stopLoss, null); assert.equal(none.takeProfit, null);
});

test("zero and unknown are never green or red; true minus sign", () => {
  assert.equal(m.tone(0), "zero"); assert.equal(m.tone(null), "zero"); assert.equal(m.tone(0.004), "zero");
  assert.equal(m.tone(-0.01), "down"); assert.equal(m.tone(1), "up");
  assert.equal(m.usd(-1.28, true), "−$1.28"); assert.equal(m.usd(1.84, true), "+$1.84"); assert.equal(m.usd(0, true), "$0.00");
  assert.equal(m.pct(3.064), "+3.06%"); assert.equal(m.pct(-1.1), "−1.10%");
});

test("open total: no trades is $0.00, any unknown makes it unknown", () => {
  assert.equal(m.openTotal([]), 0);
  assert.equal(m.openTotal([1, null]), null);
  assert.equal(m.openTotal([1, -0.5]), 0.5);
});

test("today equals the sum of today's History rows in the local timezone", () => {
  const now = Date.parse("2026-10-17T15:00:00") / 1000; // local time of the test process
  const start = m.localDayStart(now);
  const rows = [
    { id: "a", closed_ts: start + 3600, pnl: 1.52 }, { id: "b", closed_ts: start + 7200, pnl: 1.92 },
    { id: "c", closed_ts: start + 100, pnl: -0.4 }, { id: "d", closed_ts: start - 60, pnl: -10.6 },
    { id: "e", closed_ts: start + 50, pnl: 0 },
  ].map(r => m.closedTrade({ market: "SUIUSDT-PERP", direction: "long", qty: 1, entry_price: 1, ...r }, "Yellow Pro", 5));
  const t = m.today(rows, now, 100);
  const history = rows.filter(r => r.closedAt >= start).reduce((a, r) => a + r.pnl, 0);
  assert.equal(Math.round(t.net * 100), Math.round(history * 100));
  assert.equal(t.won, 2); assert.equal(t.lost, 1); assert.equal(t.trades.length, 4); assert.equal(t.pending, 0);
  assert.equal(t.complete, true);
});

test("today flags pending results and an incomplete window", () => {
  const now = Date.parse("2026-10-17T15:00:00") / 1000, start = m.localDayStart(now);
  const rows = [{ id: "a", closed_ts: start + 10, pnl: null }, { id: "b", closed_ts: start + 20, pnl: 2 }]
    .map(r => m.closedTrade({ market: "X", direction: "long", ...r }, "Yellow Pro", 5));
  const t = m.today(rows, now, 2);
  assert.equal(t.pending, 1); assert.equal(t.net, 2); assert.equal(t.complete, false);
  const s = m.summary(rows);
  assert.equal(s.realized, null); assert.equal(s.pending, 1);
});

test("result classification", () => {
  for (const r of ["target", "TARGET", "NET_TARGET_IOC", "take_profit"]) assert.equal(m.resultOf(r), "target");
  for (const r of ["stop", "STOP_EXIT", "EMERGENCY_STOP", "stop_loss"]) assert.equal(m.resultOf(r), "stop");
  for (const r of ["venue_closed", "manual", null]) assert.equal(m.resultOf(r), "closed");
});

test("markers snap to candle opens, drop pre-history, sorted, correct shapes", () => {
  const closed = [m.closedTrade({ market: "BTCUSDT-PERP", direction: "long", qty: 0.01, entry_price: 80000, leverage: 5, pnl: 2, reason: "target", opened_ts: 1000130, closed_ts: 1003700 }, "Yellow Pro", 5),
    m.closedTrade({ market: "BTCUSDT-PERP", direction: "short", qty: 0.01, entry_price: 80000, leverage: 5, pnl: -1, reason: "STOP_EXIT", opened_ts: 990000, closed_ts: 1002000 }, "Yellow Pro", 5),
    m.closedTrade({ market: "ETHUSDT-PERP", direction: "long", opened_ts: 1000200, closed_ts: 1000500 }, "Yellow Pro", 5)];
  const open = [m.openTrade({ market: "BTCUSDT-PERP", direction: "short", qty: 0.01, entry_price: 80000, opened_ts: 1004000 }, "Yellow Pro", 4)];
  const out = m.markers(closed, open, "BTCUSDT-PERP", "5m", 999900, colors);
  assert.ok(out.every(x => x.time % 300 === 0));
  assert.deepEqual(out.map(x => x.time), [...out.map(x => x.time)].sort((a, b) => a - b));
  assert.equal(out.length, 4); // short entry at 990000 is before the first candle
  const [longEntry] = out;
  assert.deepEqual([longEntry.shape, longEntry.position, longEntry.color, longEntry.text], ["arrowUp", "belowBar", "G", "Long"]);
  const tp = out.find(x => x.text.startsWith("TP"));
  assert.deepEqual([tp.shape, tp.position, tp.color], ["arrowDown", "aboveBar", "Y"]);
  const sl = out.find(x => x.text.startsWith("SL"));
  assert.deepEqual([sl.shape, sl.position, sl.color], ["arrowUp", "belowBar", "A"]);
  assert.equal(out.at(-1).text, "Short 4x · open");
});

test("range widening keeps entry, TP and SL visible", () => {
  assert.deepEqual(m.widenRange({ minValue: 100, maxValue: 110 }, [95, 105, 120, null]), { minValue: 95, maxValue: 120 });
});
