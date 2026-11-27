// 保有銘柄の逆指値（条件E）と売り検討アラート（条件F）の計算。
// Python 側（longterm/holdings.py）と同じ規則。ブラウザと Node（テスト）の両方で使う。
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.HoldingsLogic = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  const DEFAULT_CFG = {
    wait_months: 3, cut_loss_pct: -10,
    ladder: [{min_gain: 100, stop: 20}, {min_gain: 50, stop: 10}, {min_gain: 30, stop: 0}, {min_gain: -10, stop: -10}],
    half_profit_gain: 100, half_profit_min_shares: 200, no_ath_months: 6,
    overheat: {month_multiple: 2.0, year_multiple: 10.0},
  };
  const JP_TICKS = [[3000, 1], [5000, 5], [30000, 10], [50000, 50], [300000, 100], [500000, 500],
                    [3000000, 1000], [5000000, 5000], [30000000, 10000], [Infinity, 50000]];

  function tickSize(price, jp) {
    if (!jp) return 0.01;
    for (const [upper, t] of JP_TICKS) if (price <= upper) return t;
    return 50000;
  }
  function roundToTick(price, jp) {
    const t = tickSize(price, jp);
    return Math.round(Math.floor(price / t + 1e-9) * t * 100) / 100;
  }
  function addMonths(date, n) {
    const d = new Date(date.getTime());
    const day = d.getDate();
    d.setMonth(d.getMonth() + n);
    if (d.getDate() < day) d.setDate(0);   // 月末調整（例: 1/31 + 1か月 = 2月末）
    return d;
  }
  function parseDate(s) { const [y, m, d] = String(s).slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); }
  function days(a, b) { return Math.round((b - a) / 86400000); }

  // 含み損益（%）→ 逆指値（買値からの%）。損切り水準以下なら null
  function ladderStop(gainPct, cfg = DEFAULT_CFG) {
    if (gainPct <= cfg.cut_loss_pct) return null;
    for (const s of cfg.ladder) if (gainPct >= s.min_gain) return s.stop;
    return cfg.ladder[cfg.ladder.length - 1].stop;
  }

  // 条件E: 推奨アクション。prevStop は過去に到達した最大の逆指値（%）。逆指値は切り上げのみ
  function recommend({entryPrice, entryDate, asOf, price, prevStop = null, jp = true}, cfg = DEFAULT_CFG) {
    const ed = parseDate(entryDate), now = asOf instanceof Date ? asOf : parseDate(asOf);
    const gain = (price / entryPrice - 1) * 100;
    const waitEnd = addMonths(ed, cfg.wait_months);
    if (now < waitEnd) return {action: 'wait', gain, daysLeft: days(now, waitEnd), stopPct: prevStop, stopPrice: null};
    let rate = ladderStop(gain, cfg);
    if (rate === null && prevStop === null) return {action: 'cut_loss', gain, stopPct: null, stopPrice: null};
    if (rate === null) rate = prevStop;
    const stopPct = prevStop === null ? rate : Math.max(rate, prevStop);
    return {action: 'set_stop', gain, stopPct, stopPrice: roundToTick(entryPrice * (1 + stopPct / 100), jp)};
  }

  // 条件F: 売り検討アラート。detail は stocks/<ticker>.json（なければ null）
  function sellAlerts({entryPrice, entryDate, price, shares, asOf}, detail, cfg = DEFAULT_CFG) {
    const out = [];
    const now = asOf instanceof Date ? asOf : parseDate(asOf);
    const gain = (price / entryPrice - 1) * 100;
    if (detail) {
      const B = detail.B || {};
      if (B.ath_date && now > addMonths(parseDate(B.ath_date), cfg.no_ath_months))
        out.push({key: 'no_ath', text: `${cfg.no_ath_months}か月以上、上場来高値を更新していない（最後は ${B.ath_date}）`});
      const pts = (detail.crash_points || []).filter(p => p.low != null);
      const afterBuy = pts.filter(p => p.date >= entryDate);
      if (afterBuy.length) {
        const cur = afterBuy[afterBuy.length - 1];
        const prev = pts.filter(p => p.date < cur.date).pop();
        if (prev && cur.low < prev.low)
          out.push({key: 'lower_low', text: `暴落点（${cur.date}）で安値を切り下げた（${prev.low.toLocaleString()} → ${cur.low.toLocaleString()}）`});
      }
      const D = detail.D || {};
      const neg = [];
      if (D.revenue_yoy_latest != null && D.revenue_yoy_latest < 0) neg.push('売上高');
      if (D.net_income_yoy_latest != null && D.net_income_yoy_latest < 0) neg.push('純利益');
      const q = (D.quarterly_yoy || []).slice(-1)[0];
      if (q && (q.revenue_yoy < 0 || q.net_income_yoy < 0)) neg.push('直近四半期');
      if (neg.length) out.push({key: 'earnings', text: `業績が前年比マイナス（${neg.join('・')}）`});
      const c = (detail.chart || {}).c || [];
      if (c.length > 30) {
        const last = c[c.length - 1];
        const m = last / Math.min(...c.slice(-22));
        const y = last / Math.min(...c.slice(-252));
        if (m >= cfg.overheat.month_multiple || y >= cfg.overheat.year_multiple)
          out.push({key: 'overheat', text: `過熱（1か月で${m.toFixed(1)}倍 / 1年で${y.toFixed(1)}倍）`});
      }
    }
    if (gain >= cfg.half_profit_gain) {
      out.push(shares >= cfg.half_profit_min_shares
        ? {key: 'half_profit', text: `含み益 +${gain.toFixed(0)}%：半分利確を検討（残りは逆指値で継続）`}
        : {key: 'hold', text: `含み益 +${gain.toFixed(0)}%：買値を割らない限り保有継続`});
    }
    return out;
  }

  // 含み益が定着した（逆指値が同値以上）銘柄の数と比率
  function settledStats(stops) {
    const n = stops.length, k = stops.filter(s => s !== null && s >= 0).length;
    return {n, settled: k, ratio: n ? k / n * 100 : 0};
  }

  return {DEFAULT_CFG, tickSize, roundToTick, ladderStop, recommend, sellAlerts, settledStats, addMonths, parseDate};
});
