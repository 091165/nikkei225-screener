// node tests/js/holdings_logic.test.js
const assert = require('assert');
const L = require('../../web/holdings_logic.js');

// 条件E: ラダーの表（Python 側 tests/test_holdings_backtest.py と同じケース）
for (const [gain, exp] of [[-15, null], [-10, null], [-9.9, -10], [0, -10], [29.9, -10], [30, 0], [49.9, 0],
                           [50, 10], [99.9, 10], [100, 20], [300, 20]]) {
  assert.strictEqual(L.ladderStop(gain), exp, `ladder ${gain}`);
}

// 様子見期間 → 逆指値 → 切り上げのみ → 損切り
let r = L.recommend({entryPrice: 1000, entryDate: '2024-01-10', asOf: '2024-03-01', price: 1200});
assert.strictEqual(r.action, 'wait'); assert.ok(r.daysLeft > 0);
r = L.recommend({entryPrice: 1000, entryDate: '2024-01-10', asOf: '2024-04-15', price: 1600});
assert.strictEqual(r.action, 'set_stop'); assert.strictEqual(r.stopPct, 10); assert.strictEqual(r.stopPrice, 1100);
r = L.recommend({entryPrice: 1000, entryDate: '2024-01-10', asOf: '2024-05-15', price: 1100, prevStop: 10});
assert.strictEqual(r.stopPct, 10, '逆指値は下げない');
r = L.recommend({entryPrice: 1000, entryDate: '2024-01-10', asOf: '2024-04-15', price: 880});
assert.strictEqual(r.action, 'cut_loss');
r = L.recommend({entryPrice: 1000, entryDate: '2024-01-10', asOf: '2024-06-15', price: 880, prevStop: 0});
assert.strictEqual(r.action, 'set_stop', '一度逆指値を置いたら損切りではなく逆指値で管理'); assert.strictEqual(r.stopPct, 0);

// 呼値
assert.strictEqual(L.roundToTick(1234.7, true), 1234);
assert.strictEqual(L.roundToTick(3456, true), 3455);
assert.strictEqual(L.roundToTick(12345, true), 12340);
assert.strictEqual(L.roundToTick(123.456, false), 123.45);

// 月末の扱い（1/31 + 1か月 = 2月末）
assert.strictEqual(L.addMonths(new Date(2024, 0, 31), 1).getDate(), 29);

// 条件F
const detail = {
  B: {ath_date: '2024-01-05'},
  crash_points: [{date: '2023-08-01', low: 900}, {date: '2024-08-05', low: 850}],
  D: {revenue_yoy_latest: 5, net_income_yoy_latest: -3, quarterly_yoy: [{revenue_yoy: 2, net_income_yoy: 1}]},
  chart: {c: Array(260).fill(100).concat([150, 210])},
};
const keys = L.sellAlerts({entryPrice: 1000, entryDate: '2024-03-01', price: 2100, shares: 200, asOf: '2024-09-01'}, detail).map(a => a.key);
for (const k of ['no_ath', 'lower_low', 'earnings', 'overheat', 'half_profit']) assert.ok(keys.includes(k), k);
const k100 = L.sellAlerts({entryPrice: 1000, entryDate: '2024-03-01', price: 2100, shares: 100, asOf: '2024-09-01'}, null).map(a => a.key);
assert.deepStrictEqual(k100, ['hold']);

// 含み益の定着
assert.deepStrictEqual(L.settledStats([0, 10, -10, null]), {n: 4, settled: 2, ratio: 50});
console.log('holdings_logic: all tests passed');
