"""長期（暴落点）ルールのバックテスト。

ルックアヘッドを避けるため:
- 個別暴落点の下限ライン（理想乖離 -2σ 相当）は、毎月「前月末までのデータ」だけで計算し直した値を使う
- 各暴落点での条件B・C は、その日までのデータに切り詰めて判定する（今回の暴落点の安値は「その日までの安値」）
- 業績（条件D）は決算期末から financial_lag_months 遅らせて使う。その時点で使える業績がなければ「D未判定」
  （yfinance で取れる業績は直近4年程度なので、それ以前の暴落点は条件A・B・Cだけで判定する）
"""

import pandas as pd

from . import conditions as C
from .crash import CrashPoint, stock_crash_points
from .holdings import ladder_stop, round_to_tick
from .indicators import deviation, ideal_deviation


def expanding_band(dev: pd.Series, cfg: dict, level: str = "lower2") -> pd.Series:
    """各日の下限ライン = 前月末までの乖離率で計算した理想乖離（level）。履歴が足りない期間は NaN。"""
    month_ends = dev.resample("ME").last().index
    values = {}
    for m in month_ends:
        ideal = ideal_deviation(dev.loc[:m], cfg)
        values[m] = ideal[level] if ideal else float("nan")
    monthly = pd.Series(values)
    # 月末までのデータで計算した値を、翌日（翌月1日）から使う
    monthly.index = monthly.index + pd.Timedelta(days=1)
    return monthly.reindex(dev.index, method="ffill")


def financials_as_of(fin: dict | None, d: pd.Timestamp, lag_months: int) -> dict:
    """決算期末 + lag_months <= d の期だけを残した財務諸表。"""
    out = {}
    for k, df in (fin or {}).items():
        if df is None or df.empty:
            out[k] = None
            continue
        cols = [c for c in df.columns if pd.Timestamp(c) + pd.DateOffset(months=lag_months) <= d]
        out[k] = df[cols] if cols else None
    return out


def _value_after(series: pd.Series, d: pd.Timestamp):
    pos = series.index.searchsorted(d)
    if pos >= len(series):
        return None, None
    return series.index[pos], float(series.iloc[pos])


def forward_returns(close: pd.Series, d: pd.Timestamp, months: list[int]) -> dict:
    base = float(close.loc[d]) if d in close.index else _value_after(close, d)[1]
    out = {}
    for m in months:
        target = d + pd.DateOffset(months=m)
        if target > close.index[-1]:
            out[f"fwd_{m}m"] = None
            continue
        _, v = _value_after(close, target)
        out[f"fwd_{m}m"] = (v / base - 1) * 100 if v is not None else None
    return out


def simulate_exit(df: pd.DataFrame, entry_date: pd.Timestamp, entry_price: float,
                  crash_points: list[CrashPoint], cfg: dict, jp: bool = True) -> dict:
    """条件E（逆指値ラダー）と条件F（売りルール）を機械的に適用して、手仕舞い日・価格を返す。"""
    h, rules = cfg["holdings"], cfg["backtest"]["exit_rules"]
    shares = cfg["backtest"].get("shares", 200)
    wait_end = entry_date + pd.DateOffset(months=h.get("wait_months", 3))
    no_ath_span = pd.DateOffset(months=h.get("no_ath_months", 6))

    after = df.loc[entry_date:].iloc[1:]
    hist_close = df["close"].loc[:entry_date]
    run_max = float(hist_close.max())
    run_max_date = hist_close[hist_close == run_max].index[-1]

    # 保有中に来る次の暴落点と、その直前の暴落点の安値（lower_low 判定用）
    later = [p for p in crash_points if pd.Timestamp(p.date) > entry_date]
    lows_before = {}
    for i, p in enumerate(crash_points):
        prev = [q for q in crash_points[:i] if q.low is not None]
        lows_before[p.date] = prev[-1].low if prev else None

    stop_pct = None
    remaining = 1.0
    proceeds = 0.0
    events = []
    max_adverse = 0.0
    for t, row in after.iterrows():
        max_adverse = min(max_adverse, (row["low"] / entry_price - 1) * 100)
        # 1) 日中: 既存の逆指値にかかったか
        if rules.get("stop", True) and stop_pct is not None:
            stop_price = round_to_tick(entry_price * (1 + stop_pct / 100), jp)
            if row["low"] <= stop_price:
                px = min(row["open"], stop_price)
                proceeds += remaining * px
                events.append((t, "stop", px, remaining))
                return _result(entry_price, proceeds, t, "逆指値", events, stop_pct, max_adverse, closed=True)
        if row["close"] > run_max:
            run_max, run_max_date = float(row["close"]), t
        gain = (row["close"] / entry_price - 1) * 100

        # 2) 大引け: 次の暴落点で安値切り下げ
        if rules.get("lower_low", True):
            for p in later:
                pd_, end = pd.Timestamp(p.date), pd.Timestamp(p.episode_end)
                prev_low = lows_before.get(p.date)
                if pd_ <= t <= end and prev_low is not None and row["low"] < prev_low:
                    proceeds += remaining * row["close"]
                    events.append((t, "lower_low", row["close"], remaining))
                    return _result(entry_price, proceeds, t, f"暴落点{p.date}で安値切り下げ", events,
                                   stop_pct, max_adverse, closed=True)

        if t >= wait_end:
            # 3) 条件E: 損切り・逆指値の設定（切り上げのみ）
            if rules.get("stop", True):
                rate = ladder_stop(gain, cfg)
                if rate is None and stop_pct is None:
                    proceeds += remaining * row["close"]
                    events.append((t, "cut_loss", row["close"], remaining))
                    return _result(entry_price, proceeds, t, "損切り（-10%以下）", events, stop_pct,
                                   max_adverse, closed=True)
                if rate is not None:
                    stop_pct = rate if stop_pct is None else max(stop_pct, rate)
            # 4) 条件F: +100%で半分利確
            if (rules.get("half_profit", True) and remaining == 1.0 and shares >= h.get("half_profit_min_shares", 200)
                    and gain >= h.get("half_profit_gain", 100.0)):
                proceeds += 0.5 * row["close"]
                remaining = 0.5
                events.append((t, "half_profit", row["close"], 0.5))
            # 5) 条件F: 上場来高値を長く更新していない
            if rules.get("no_ath", True) and t - run_max_date > pd.Timedelta(0) and run_max_date + no_ath_span < t:
                proceeds += remaining * row["close"]
                events.append((t, "no_ath", row["close"], remaining))
                return _result(entry_price, proceeds, t, "6か月以上高値更新なし", events, stop_pct,
                               max_adverse, closed=True)

    last_t = df.index[-1]
    proceeds += remaining * float(df["close"].iloc[-1])
    return _result(entry_price, proceeds, last_t, "保有中", events, stop_pct, max_adverse, closed=False)


def _result(entry_price, proceeds, exit_date, reason, events, stop_pct, max_adverse, closed):
    return {
        "exit_date": exit_date.strftime("%Y-%m-%d"),
        "exit_reason": reason,
        "return_pct": (proceeds / entry_price - 1) * 100,
        "closed": closed,
        "final_stop_pct": stop_pct,
        "max_adverse_pct": max_adverse,
        "partial_exits": [{"date": t.strftime("%Y-%m-%d"), "type": k, "price": float(px), "fraction": f}
                          for t, k, px, f in events if k == "half_profit"],
    }


def backtest_stock(ticker: str, name: str, df: pd.DataFrame, fin: dict | None, index_points: list[CrashPoint],
                   index_dev: pd.Series, bench_close: pd.Series, cfg: dict) -> tuple[list[dict], list[dict]]:
    """1銘柄のバックテスト。(trades, signals) を返す。signals は各暴落点での判定記録（見送り含む）。"""
    bcfg = cfg["backtest"]
    jp = ticker.endswith(".T")
    sma = cfg["deviation"].get("sma_period", 25)
    dev = deviation(df["close"], sma)
    band = expanding_band(dev, cfg, cfg["crash_point"]["individual"].get("band", "lower2"))
    pts_full = stock_crash_points(dev, df["low"], band, index_points, cfg)
    start = pd.Timestamp(bcfg.get("start", "2005-01-01"))

    trades, signals = [], []
    busy_until = None
    for p in pts_full:
        d = pd.Timestamp(p.date)
        if d < start or d not in df.index:
            continue
        # その日までのデータだけで判定
        dev_d, low_d, close_d = dev.loc[:d], df["low"].loc[:d], df["close"].loc[:d]
        idx_d = [q for q in index_points if pd.Timestamp(q.date) <= d]
        pts_d = stock_crash_points(dev_d, low_d, band.loc[:d], idx_d, cfg)
        b = C.condition_b(close_d, cfg)
        c = C.condition_c(pts_d, close_d, cfg)
        fin_d = financials_as_of(fin, d, bcfg.get("financial_lag_months", 3))
        dd = C.condition_d(fin_d.get("income"), fin_d.get("quarterly_income"), fin_d.get("cashflow"),
                           fin_d.get("balance"), cfg)
        rs = C.relative_strength(dev_d, index_dev.loc[:d], idx_d) if idx_d else {"pass": None}
        po = C.monthly_perfect_order(close_d, cfg)
        d_status = "未判定" if dd["pass"] is None else ("合格" if dd["pass"] else "不合格")
        enter = bool(b["pass"] and c["pass"] and dd["pass"] is not False)
        strong = bool(enter and (rs.get("pass") or po.get("pass")))
        reason = []
        if not b["pass"]:
            reason.append(f"B不合格（高値から{b['days_since_high']}日）")
        if c["pass"] is None:
            reason.append("C判定不能（暴落点が2回未満）")
        elif not c["pass"]:
            reason.append(f"C不合格（安値 {c['prev_low']:.0f}→{c['latest_low']:.0f}）")
        if dd["pass"] is False:
            reason.append("D不合格")
        if enter and busy_until is not None and d <= busy_until:
            enter = False
            reason.append("保有中のため見送り")
        sig = {"ticker": ticker, "name": name, "date": p.date, "kind": p.kind,
               "b": b["pass"], "b_exception": b["exception"], "c": c["pass"], "c_streak": c["strength_streak"],
               "higher_highs": c["higher_highs"], "d": d_status, "relative_strength": rs.get("pass"),
               "monthly_po": po.get("pass"), "entered": enter, "strong": strong, "reason": "／".join(reason)}
        signals.append(sig)
        if not enter:
            continue

        entry_price = float(df["close"].loc[d])
        ex = simulate_exit(df, d, entry_price, pts_full, cfg, jp)
        busy_until = pd.Timestamp(ex["exit_date"])
        hold_days = (busy_until - d).days
        bench_ret = None
        if d in bench_close.index or len(bench_close.loc[d:]):
            _, b0 = _value_after(bench_close, d)
            _, b1 = _value_after(bench_close, busy_until)
            if b0 and b1:
                bench_ret = (b1 / b0 - 1) * 100
        trade = {"ticker": ticker, "name": name, "entry_date": p.date, "kind": p.kind,
                 "entry_price": entry_price, "strong": strong, "d_status": d_status,
                 **ex, "hold_days": hold_days, "bench_return_pct": bench_ret,
                 **forward_returns(df["close"], d, bcfg.get("forward_months", [3, 6, 12, 24])),
                 **{f"bench_{k}": v for k, v in forward_returns(bench_close, d, bcfg.get("forward_months",
                                                                                          [3, 6, 12, 24])).items()}}
        trades.append(trade)
    return trades, signals


def summarize(trades: pd.DataFrame, cfg: dict) -> dict:
    if trades.empty:
        return {"trades": 0}
    months = cfg["backtest"].get("forward_months", [3, 6, 12, 24])
    out = {
        "trades": int(len(trades)),
        "closed": int(trades["closed"].sum()),
        "win_rate": float((trades["return_pct"] > 0).mean() * 100),
        "avg_return": float(trades["return_pct"].mean()),
        "median_return": float(trades["return_pct"].median()),
        "avg_max_adverse": float(trades["max_adverse_pct"].mean()),
        "worst_max_adverse": float(trades["max_adverse_pct"].min()),
        "avg_hold_days": float(trades["hold_days"].mean()),
        "avg_bench_return": float(trades["bench_return_pct"].dropna().mean()) if trades["bench_return_pct"].notna().any() else None,
    }
    for m in months:
        col = f"fwd_{m}m"
        if col in trades:
            s, bs = trades[col].dropna(), trades[f"bench_{col}"].dropna()
            out[f"avg_{col}"] = float(s.mean()) if len(s) else None
            out[f"avg_bench_{col}"] = float(bs.mean()) if len(bs) else None
    return out
