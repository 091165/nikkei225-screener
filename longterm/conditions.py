"""長期の買いルール（条件 A〜D）、補助シグナル、総合判定。

すべての関数は「評価日までのデータ」だけを受け取る前提（呼び出し側で as_of までに切り詰める）。
"""

import pandas as pd

from .crash import CrashPoint

VERDICT_LABELS = {
    "strong_buy": "長期買い（強）",
    "buy": "長期買い",
    "watch": "監視（暴落待ち）",
    "skip": "見送り",
    "weak_fundamentals": "業績未達",
    "insufficient": "判定不能",
}


def _d(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


# ─── 条件A: 暴落点であること ─────────────────────────
def condition_a(points: list[CrashPoint], dates: pd.DatetimeIndex, cfg: dict) -> dict:
    """直近の暴落点が評価日から entry_window_days 営業日以内なら合格。A1=指数要因、A2=個別要因。"""
    window = cfg["crash_point"].get("entry_window_days", 5)
    recent = set(_d(d) for d in dates[-(window + 1):])
    hits = [p for p in points if p.date in recent]
    a1 = any(p.kind in ("index", "both") for p in hits)
    a2 = any(p.kind in ("individual", "both") for p in hits)
    latest = hits[-1] if hits else None
    return {
        "pass": a1 or a2,
        "a1_index": a1,
        "a2_individual": a2,
        "date": latest.date if latest else None,
        "kind": latest.kind if latest else None,
    }


# ─── 条件B: 上場来高値の更新 ─────────────────────────
def condition_b(close: pd.Series, cfg: dict) -> dict:
    """評価日（close の最終日）からさかのぼって months か月以内に上場来高値を更新しているか。

    上場来高値が old_ath_years 年以上前なら「別相場」とみなし、直近 fallback_high_years 年の高値で代用（exception=True）。
    """
    bcfg = cfg["condition_b"]
    close = close.dropna()
    as_of = close.index[-1]
    ath = float(close.max())
    ath_date = close[close == ath].index[-1]
    cutoff = as_of - pd.DateOffset(months=bcfg.get("months", 6))
    exception = False
    ref_date, ref_high = ath_date, ath
    if ath_date < as_of - pd.DateOffset(years=bcfg.get("old_ath_years", 8)):
        exception = True
        recent = close.loc[as_of - pd.DateOffset(years=bcfg.get("fallback_high_years", 5)):]
        ref_high = float(recent.max())
        ref_date = recent[recent == ref_high].index[-1]
    return {
        "pass": bool(ref_date >= cutoff),
        "exception": exception,
        "ath": ath,
        "ath_date": _d(ath_date),
        "high_used": ref_high,
        "high_date": _d(ref_date),
        "days_since_high": int((as_of - ref_date).days),
    }


# ─── 条件C: 暴落点ごとの安値切り上げ ─────────────────────
def condition_c(points: list[CrashPoint], close: pd.Series, cfg: dict) -> dict:
    """今回（最新）の暴落点の安値 > 前回の暴落点の安値 なら合格。暴落点が2回未満なら判定不能（pass=None）。"""
    pts = [p for p in points if p.low is not None]
    res = {"pass": None, "latest_date": None, "latest_low": None, "prev_date": None, "prev_low": None,
           "higher_highs": None, "strength_streak": 0, "strong": False, "n_points": len(pts)}
    if not pts:
        return res
    res.update(latest_date=pts[-1].date, latest_low=pts[-1].low)
    if len(pts) < 2:
        return res
    cur, prev = pts[-1], pts[-2]
    res.update(prev_date=prev.date, prev_low=prev.low, **{"pass": bool(cur.low > prev.low)})

    streak = 0
    for i in range(len(pts) - 1, 0, -1):
        if pts[i].low > pts[i - 1].low:
            streak += 1
        else:
            break
    res["strength_streak"] = streak
    res["strong"] = streak >= cfg["condition_c"].get("rising_streak_strong", 2)

    if len(pts) >= 3:
        def high_between(a, b):
            seg = close.loc[pd.Timestamp(a.date):pd.Timestamp(b.date)]
            return float(seg.max()) if len(seg) else None
        h_cur, h_prev = high_between(prev, cur), high_between(pts[-3], prev)
        if h_cur is not None and h_prev is not None:
            res["higher_highs"] = bool(h_cur > h_prev)
            res["high_between_latest"] = h_cur
            res["high_between_prev"] = h_prev
    return res


# ─── 条件D: 業績 ───────────────────────────────────
_ROWS = {
    "revenue": ["Total Revenue", "Operating Revenue", "Revenue"],
    "net_income": ["Net Income", "Net Income Common Stockholders",
                   "Net Income From Continuing Operation Net Minority Interest"],
    "operating_income": ["Operating Income", "Total Operating Income As Reported"],
    "pretax": ["Pretax Income"],
    "operating_cf": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "equity": ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"],
    "total_assets": ["Total Assets"],
    "retained_earnings": ["Retained Earnings"],
    "dividends_paid": ["Cash Dividends Paid", "Common Stock Dividend Paid"],
    "shares": ["Ordinary Shares Number", "Share Issued"],
}


def statement_row(df: pd.DataFrame | None, key: str) -> pd.Series:
    """yfinance の財務諸表（行=科目、列=期末日）から1科目を取り出し、古い順の Series にする。"""
    if df is None or df.empty:
        return pd.Series(dtype=float)
    for name in _ROWS[key]:
        if name in df.index:
            s = pd.to_numeric(df.loc[name], errors="coerce").dropna()
            s.index = pd.to_datetime(s.index)
            return s.sort_index()
    return pd.Series(dtype=float)


def _yoy(s: pd.Series, lag: int = 1) -> pd.Series:
    prev = s.shift(lag)
    return ((s - prev) / prev.abs() * 100).dropna()


def _increasing_streak(s: pd.Series, lag: int = 1) -> int:
    n = 0
    vals = s.values
    for i in range(len(vals) - 1, lag - 1, -1):
        if vals[i] > vals[i - lag]:
            n += 1
        else:
            break
    return n


def _quarterly_yoy(s: pd.Series) -> pd.Series:
    """前年同期比。四半期の期末日から1年前（±45日）の値と比べる。"""
    out = {}
    for d, v in s.items():
        target = d - pd.DateOffset(years=1)
        cand = s[(s.index >= target - pd.Timedelta(days=45)) & (s.index <= target + pd.Timedelta(days=45))]
        if len(cand):
            pv = cand.iloc[-1]
            if pv != 0:
                out[d] = (v - pv) / abs(pv) * 100
    return pd.Series(out, dtype=float)


def condition_d(annual_income: pd.DataFrame | None, quarterly_income: pd.DataFrame | None,
                annual_cashflow: pd.DataFrame | None, annual_balance: pd.DataFrame | None,
                cfg: dict) -> dict:
    """通期: 売上高・純利益が取得できる全期で前年比プラス（最低 min_annual_periods 期）。
    四半期: 取得できる範囲で前年同期比プラス（比較できる期がなければ判定に使わない）。
    データ不足は pass=None（判定不能、合格扱いにしない）。
    """
    dcfg = cfg["condition_d"]
    rev, ni = statement_row(annual_income, "revenue"), statement_row(annual_income, "net_income")
    common = rev.index.intersection(ni.index)
    rev, ni = rev.loc[common], ni.loc[common]
    n = len(common)
    res = {"pass": None, "annual_periods": n, "quarterly_periods": 0,
           "revenue_streak": 0, "net_income_streak": 0, "growth_stock": False,
           "annual": [], "quarterly_yoy": [], "aux": {}, "reason": None}
    res["annual"] = [{"period": _d(d), "revenue": float(rev[d]), "net_income": float(ni[d])} for d in common]
    if n < dcfg.get("min_annual_periods", 3):
        res["reason"] = f"通期データが{n}期分しかない（最低{dcfg.get('min_annual_periods', 3)}期）"
        return res

    rev_yoy, ni_yoy = _yoy(rev), _yoy(ni)
    annual_ok = bool((rev.diff().dropna() > 0).all() and (ni.diff().dropna() > 0).all())
    res["revenue_streak"] = _increasing_streak(rev)
    res["net_income_streak"] = _increasing_streak(ni)
    res["revenue_yoy_latest"] = float(rev_yoy.iloc[-1]) if len(rev_yoy) else None
    res["net_income_yoy_latest"] = float(ni_yoy.iloc[-1]) if len(ni_yoy) else None
    g = dcfg.get("growth_stock_pct", 20.0)
    res["growth_stock"] = bool(ni.iloc[-2] > 0 and (res["revenue_yoy_latest"] or 0) >= g
                               and (res["net_income_yoy_latest"] or 0) >= g)

    q_rev, q_ni = statement_row(quarterly_income, "revenue"), statement_row(quarterly_income, "net_income")
    qy_rev, qy_ni = _quarterly_yoy(q_rev), _quarterly_yoy(q_ni)
    q_common = qy_rev.index.intersection(qy_ni.index)
    res["quarterly_periods"] = int(len(q_rev))
    res["quarterly_yoy"] = [{"period": _d(d), "revenue_yoy": float(qy_rev[d]), "net_income_yoy": float(qy_ni[d])}
                            for d in q_common]
    quarterly_ok = None
    if len(q_common):
        quarterly_ok = bool((qy_rev.loc[q_common] > 0).all() and (qy_ni.loc[q_common] > 0).all())
    res["annual_ok"] = annual_ok
    res["quarterly_ok"] = quarterly_ok

    # 補助チェック（表示用。aux_in_verdict=True なら合否にも入れる）
    aux = {}
    ocf = statement_row(annual_cashflow, "operating_cf")
    oi = statement_row(annual_income, "operating_income")
    if len(ocf):
        aux["operating_cf_positive"] = bool(ocf.iloc[-1] > 0)
    if len(ocf) and len(oi) and oi.iloc[-1] > 0:
        ratio = float(ocf.iloc[-1] / oi.iloc[-1])
        lo, hi = dcfg.get("ocf_to_op_income_range", [0.5, 2.0])
        aux["ocf_to_op_income"] = ratio
        aux["ocf_matches_op_income"] = bool(lo <= ratio <= hi)
    eq, ta = statement_row(annual_balance, "equity"), statement_row(annual_balance, "total_assets")
    if len(eq) and len(ta) and ta.iloc[-1] > 0:
        er = float(eq.iloc[-1] / ta.iloc[-1] * 100)
        aux["equity_ratio"] = er
        aux["equity_ratio_ok"] = bool(er >= dcfg.get("equity_ratio_min", 60.0))
    if len(oi) and rev.iloc[-1] > 0 and oi.index[-1] in rev.index:
        om = float(oi.iloc[-1] / rev.loc[oi.index[-1]] * 100)
        aux["op_margin"] = om
        aux["op_margin_ok"] = bool(om >= dcfg.get("op_margin_min", 20.0))
    re_ = statement_row(annual_balance, "retained_earnings")
    if len(re_) >= 2:
        aux["retained_earnings_rising"] = bool((re_.diff().dropna() > 0).all())
    res["aux"] = aux

    ok = annual_ok and quarterly_ok is not False
    if dcfg.get("aux_in_verdict", False):
        ok = ok and all(v for k, v in aux.items() if k.endswith(("_positive", "_matches_op_income", "_ok")))
    res["pass"] = bool(ok)
    if not annual_ok:
        res["reason"] = "通期で前年割れの期がある"
    elif quarterly_ok is False:
        res["reason"] = "四半期で前年同期割れがある"
    return res


# ─── 補助シグナル ─────────────────────────────────
def relative_strength(stock_dev: pd.Series, index_dev: pd.Series, index_points: list[CrashPoint]) -> dict:
    """直近の指数暴落点の日に、個別の乖離率 > 指数の乖離率（個別の下げが浅い）か。"""
    for p in reversed(index_points):
        d = pd.Timestamp(p.date)
        if d in stock_dev.index and d in index_dev.index:
            s, i = float(stock_dev.loc[d]), float(index_dev.loc[d])
            return {"pass": s > i, "date": p.date, "stock_dev": s, "index_dev": i}
    return {"pass": None, "date": None}


def completed_monthly_close(close: pd.Series) -> pd.Series:
    """確定した月足の終値（評価日が月の途中なら当月分を除く）。"""
    m = close.resample("ME").last().dropna()
    last = close.index[-1]
    month_end_bday = (last + pd.offsets.BMonthEnd(0)).normalize()
    if len(m) and last.normalize() < month_end_bday:
        m = m.iloc[:-1]
    return m


def monthly_perfect_order(close: pd.Series, cfg: dict) -> dict:
    """月足の短期>中期>長期 かつ 3本とも上向き（前月比）。確定足のみで判定。"""
    s, mid, lng = cfg["signals"].get("monthly_ma", [6, 12, 24])
    m = completed_monthly_close(close)
    if len(m) < lng + 1:
        return {"pass": None}
    ma = {k: m.rolling(k).mean() for k in (s, mid, lng)}
    order = ma[s].iloc[-1] > ma[mid].iloc[-1] > ma[lng].iloc[-1]
    rising = all(ma[k].iloc[-1] > ma[k].iloc[-2] for k in (s, mid, lng))
    uptrend = bool(m.iloc[-1] > ma[lng].iloc[-1] and ma[lng].iloc[-1] > ma[lng].iloc[-2])
    return {"pass": bool(order and rising), "monthly_uptrend": uptrend,
            "ma": {str(k): float(ma[k].iloc[-1]) for k in ma}, "month": _d(m.index[-1])}


def bottoming(low: pd.Series, close: pd.Series, cfg: dict) -> dict:
    """直近N日で安値を更新していない、または5日線が上向きに転じた。"""
    n = cfg["signals"].get("bottoming_days", 5)
    low, close = low.dropna(), close.dropna()
    if len(low) < n * 2 + 1 or len(close) < 8:
        return {"pass": None}
    no_new_low = bool(low.iloc[-n:].min() > low.iloc[:-n].iloc[-60:].min())
    sma5 = close.rolling(5).mean()
    turned_up = bool(sma5.iloc[-1] > sma5.iloc[-2] and sma5.iloc[-2] <= sma5.iloc[-3])
    return {"pass": no_new_low or turned_up, "no_new_low": no_new_low, "sma5_turned_up": turned_up}


# ─── 総合判定 ─────────────────────────────────────
def verdict(a: dict, b: dict, c: dict, d: dict, rs: dict, po: dict) -> dict:
    """A〜D と補助シグナルから総合判定を返す。reasons は「なぜその判定か」の短い説明。"""
    reasons = []
    reasons.append(("✔" if a["pass"] else "✖") +
                   (f" 暴落点 {a['date']}（{'指数' if a['a1_index'] else ''}{'・' if a['a1_index'] and a['a2_individual'] else ''}{'個別' if a['a2_individual'] else ''}）"
                    if a["pass"] else " 直近に暴落点なし"))
    reasons.append(("✔" if b["pass"] else "✖") +
                   f" {'直近高値' if b['exception'] else '上場来高値'}更新 {b['high_date']}（{b['days_since_high']}日前）")
    if c["pass"] is None:
        reasons.append("？ 暴落点が2回未満で安値切り上げを判定できない")
    else:
        reasons.append(("✔" if c["pass"] else "✖") +
                       f" 前回暴落点（{c['prev_date']}）の安値 {c['prev_low']:,.0f} → 今回（{c['latest_date']}） {c['latest_low']:,.0f}")
    if d["pass"] is None:
        reasons.append(f"？ 業績: {d.get('reason') or 'データ不足'}")
    else:
        reasons.append(("✔" if d["pass"] else "✖") +
                       f" 業績: 売上{d['revenue_streak']}期・純利益{d['net_income_streak']}期連続増加"
                       + (f"（{d['reason']}）" if d.get("reason") else ""))

    if a["pass"] and a.get("a1_index") and rs.get("pass") is not None:
        reasons.append(("✔" if rs["pass"] else "－") +
                       f" 相対的な強さ: 個別 {rs['stock_dev']:.1f}% / 指数 {rs['index_dev']:.1f}%（{rs['date']}）")
    if c.get("strength_streak"):
        reasons.append(f"{'✔' if c.get('strong') else '－'} 安値の連続切り上げ {c['strength_streak']}回")

    if b["pass"] is False or c["pass"] is False:
        code = "skip"
    elif c["pass"] is None or d["pass"] is None:
        code = "insufficient"
    elif d["pass"] is False:
        code = "weak_fundamentals"
    elif a["pass"]:
        # 強: 指数要因の暴落点で相対的に強い（個別の下げが浅い）、または暴落点ごとの安値が連続して切り上がっている
        # （月足パーフェクトオーダーは条件Bの銘柄ならほぼ満たすので、強さの判定には使わず表示のみ）
        strong = bool((rs.get("pass") and a.get("a1_index")) or c.get("strong"))
        code = "strong_buy" if strong else "buy"
    else:
        code = "watch"
    return {"code": code, "label": VERDICT_LABELS[code], "reasons": reasons}
