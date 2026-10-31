"""条件E（逆指値ラダー）・条件F（売り検討アラート）のロジック。

バックテストと、保有銘柄画面（Stage 6, JavaScript 側で同じ規則を実装）の基準になる。
"""

import math

import pandas as pd

# 日本株の呼値（TOPIX100以外の一般的な刻み）。(上限株価, 刻み)
_JP_TICKS = [(3000, 1), (5000, 5), (30000, 10), (50000, 50), (300000, 100),
             (500000, 500), (3000000, 1000), (5000000, 5000), (30000000, 10000), (math.inf, 50000)]


def tick_size(price: float, jp: bool = True) -> float:
    if not jp:
        return 0.01
    for upper, tick in _JP_TICKS:
        if price <= upper:
            return tick
    return _JP_TICKS[-1][1]


def round_to_tick(price: float, jp: bool = True) -> float:
    """逆指値を呼値単位に丸める（売りの逆指値なので切り捨て）。"""
    t = tick_size(price, jp)
    return round(math.floor(price / t + 1e-9) * t, 2)


def ladder_stop(gain_pct: float, cfg: dict) -> float | None:
    """含み損益（%）に対応する逆指値（買値からの%）。損切り水準以下なら None（＝損切り）。"""
    h = cfg["holdings"]
    if gain_pct <= h.get("cut_loss_pct", -10.0):
        return None
    for step in h["ladder"]:
        if gain_pct >= step["min_gain"]:
            return float(step["stop"])
    return float(h["ladder"][-1]["stop"])


def recommend(entry_price: float, entry_date, as_of, current_price: float, prev_stop_pct: float | None,
              cfg: dict, jp: bool = True) -> dict:
    """条件E: 買付から wait_months 経過後の推奨アクションと逆指値。逆指値は切り上げのみ。"""
    h = cfg["holdings"]
    entry_date, as_of = pd.Timestamp(entry_date), pd.Timestamp(as_of)
    wait_end = entry_date + pd.DateOffset(months=h.get("wait_months", 3))
    gain = (current_price / entry_price - 1) * 100
    if as_of < wait_end:
        return {"action": "wait", "gain_pct": gain, "days_left": int((wait_end - as_of).days),
                "stop_pct": prev_stop_pct, "stop_price": None}
    rate = ladder_stop(gain, cfg)
    if rate is None and prev_stop_pct is None:
        return {"action": "cut_loss", "gain_pct": gain, "stop_pct": None, "stop_price": None}
    if rate is None:
        rate = prev_stop_pct
    stop_pct = rate if prev_stop_pct is None else max(rate, prev_stop_pct)
    return {"action": "set_stop", "gain_pct": gain, "stop_pct": stop_pct,
            "stop_price": round_to_tick(entry_price * (1 + stop_pct / 100), jp)}


def overheat(close: pd.Series, cfg: dict) -> dict:
    """過熱警告: 1か月で month_multiple 倍、または1年で year_multiple 倍。"""
    o = cfg["holdings"]["overheat"]
    last = close.index[-1]
    m = close.loc[last - pd.DateOffset(months=1):]
    y = close.loc[last - pd.DateOffset(years=1):]
    month_x = float(close.iloc[-1] / m.min()) if len(m) else 1.0
    year_x = float(close.iloc[-1] / y.min()) if len(y) else 1.0
    return {"pass": month_x >= o["month_multiple"] or year_x >= o["year_multiple"],
            "month_multiple": month_x, "year_multiple": year_x}
