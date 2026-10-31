import numpy as np
import pandas as pd
import pytest

from longterm import screens as S
from longterm.config import load_config
from longterm.crash import CrashPoint
from longterm.enrich import cross_section, madakabu_score

CFG = load_config()


def _close(vals, start="2014-01-01"):
    return pd.Series(np.asarray(vals, dtype=float), index=pd.bdate_range(start, periods=len(vals)))


def _cp(date, low, kind="index"):
    return CrashPoint(date, kind, -10, -11, -12, date, low, date, date, True)


# ─── P2-2 暴落点での強さ ───
def test_crash_strength_held_and_broken():
    close = _close(np.full(400, 100.0), "2023-01-02")
    low = close.copy()
    low.iloc[220] = 90.0                       # 暴落点の直前6か月の安値
    d = close.index[300].strftime("%Y-%m-%d")
    held = S.crash_strength([_cp(d, 95.0)], low, close)
    assert held["held"] is True and held["above_crash_low_pct"] == pytest.approx(100 / 95 * 100 - 100)
    broken = S.crash_strength([_cp(d, 85.0)], low, close)
    assert broken["held"] is False
    young = S.crash_strength([_cp(close.index[10].strftime("%Y-%m-%d"), 95.0)], low, close)
    assert young["held"] is None          # 上場が暴落点より新しい（直前6か月のデータがない）


# ─── X-1 逆張り型 / 順張り押し目 ───
def test_dip_type():
    assert S.dip_type(62, 1000, 600, 4.2, 10, True) == "逆張り型"
    assert S.dip_type(19, 60, 5, 1.0, 0, True) == "順張り押し目"
    assert S.dip_type(62, 1000, 600, 0, 0, True) == "—"       # 無配は逆張り型にしない
    assert S.dip_type(62, 1000, 600, 4.2, 10, False) == "—"


def test_perfect_order_days():
    up = _close(np.linspace(100, 200, 200))
    assert S.perfect_order_days(up)["now"] is True
    down = _close(list(np.linspace(100, 200, 200)) + list(np.linspace(200, 120, 120)))
    po = S.perfect_order_days(down)
    assert po["now"] is False and po["days_since"] > 0


# ─── X-8 / Y-3 ───
def test_short_rebound():
    vals = list(np.linspace(120, 100, 40)) + [104.0]
    r = S.short_rebound(_close(vals), dev25=-5.0)
    assert r["pass"] is True


def test_topping_and_trailing_stop():
    n = 80
    close = np.r_[np.linspace(100, 150, 40), np.full(40, 148.0)]
    close[45] = 152
    df = pd.DataFrame({"high": close * 1.005, "low": close * 0.995, "close": close}, index=pd.bdate_range("2024-01-01", periods=n))
    dev = pd.Series(np.r_[np.full(40, 8.0), np.zeros(40)], index=df.index)
    assert S.topping_signal(df, dev, upper1=5.0)["pass"] is True
    lows = np.full(n, 150.0)
    lows[60] = 140.0
    df2 = df.assign(low=lows)
    assert S.trailing_stop_level(df2) == 140.0


# ─── Y-5 / Y-6 ───
def test_turnaround_and_gain():
    close = _close(list(np.linspace(300, 100, 600)) + [110.0] * 10)
    assert S.turnaround([-5, -3, 2], drop_from_ath=60, close=close) is True
    assert S.turnaround([5, -3, 2], drop_from_ath=60, close=close) is False
    c10 = _close(np.geomspace(10, 200, 2700), "2015-01-01")
    assert S.gain_over_years(c10, 10) > 10


# ─── X-4 月別（勝率の区間）───
def test_wilson_and_month_stats():
    lo, hi = S.wilson_interval(18, 20)
    assert lo > 50
    lo2, hi2 = S.wilson_interval(10, 20)
    assert lo2 < 50 < hi2
    close = pd.Series(100.0, index=pd.bdate_range("2005-01-03", "2025-06-30"))
    close.loc[close.index.month == 1] = 90.0           # 毎年1月に下がる
    ms = S.month_stats(close, 1)
    assert ms["win_rate"] == 0 and ms["significant"] is True and ms["n"] >= 19


# ─── X-5 / Y-4 / P3-1 ───
def test_dividend_growth_and_aristocrat():
    idx = pd.to_datetime([f"{y}-06-01" for y in range(2014, 2025)])
    divs = pd.Series(np.linspace(1.0, 2.0, len(idx)), index=idx)
    annual = S.annual_dividends(divs)
    g = S.dividend_growth_stats(annual)
    assert g["ok"] is True and g["drawdown_years"] == 0 and g["cut_last_year"] is False
    assert S.aristocrat_signal(50, 7.5, 2e11, False, False) == "配当貴族の買い場"
    assert S.aristocrat_signal(30, 5.5, 2e11, False, False) == "監視"
    assert S.aristocrat_signal(50, 7.5, 2e11, True, False) == "減配：ルール上は売り"
    assert S.aristocrat_signal(50, 7.5, 2e11, False, True) == "—"          # NASDAQ100だけの銘柄は対象外
    assert S.aristocrat_signal(None, 7.5, 2e11, False, False) == "—"


def test_yield_percentile():
    close = pd.Series(100.0, index=pd.bdate_range("2015-01-01", "2025-12-31"))
    close.loc["2020-03-01":"2020-04-30"] = 50.0
    divs = pd.Series(1.0, index=pd.date_range("2015-03-01", "2025-12-01", freq="QS-MAR"))
    yp = S.yield_percentile(close, divs)
    assert yp["max"] > yp["median"] and yp["pct"] is not None


# ─── まだ株格付け・後処理 ───
def _sc(**kw):
    base = {"dividend": {"grade": "A", "re_trend": "up", "payout_jump": False, "below_vt": False},
            "monthly": {"bear_streak_then_up": False, "bull_streak": 0}, "bottoming": {"pass": True},
            "crash_strength": {"held": True}, "dip": {"type": "—"}, "knife": False, "pbr_percentile": None,
            "band_width": 8.0, "month_now": {"n": 0}, "rel20": None, "short_rebound": {"pass": False},
            "topping": {"pass": False}}
    base.update(kw)
    return base


def test_madakabu_score_range_and_penalties():
    row = {"deviation": -12.0, "lower2": -10.0, "dist_to_lower2": -2.0}
    full = madakabu_score(_sc(dip={"type": "逆張り型"}, sector_laggard=True, yen_tag="円高・内需",
                              monthly={"bear_streak_then_up": True, "bull_streak": 3}), row, {"yen_strong": True}, CFG)
    assert full["score"] == 100
    weak = madakabu_score(_sc(dividend={"grade": "—", "re_trend": "flat", "payout_jump": True, "below_vt": True},
                              bottoming={"pass": False}, crash_strength={"held": False}, knife=True), row, {}, CFG)
    assert weak["score"] == 0
    assert any("配当性向" in k for k, _ in weak["minus"])


def test_cross_section_sector_and_bands():
    def r(t, sector, dev, pbr, width):
        return {"ticker": t, "name": t, "market": "Nikkei225", "deviation": dev, "dist_to_lower2": 2.0 if dev < 0 else 20.0,
                "ideal": {"lower2": -width}, "extra": {"sector": sector, "pbr": pbr},
                "screens": _sc(band_width=width)}
    rows = [r("1.T", "銀行", 3.0, 1.0, 5), r("2.T", "銀行", 4.0, 1.1, 8), r("3.T", "銀行", -6.0, 0.5, 12),
            r("4.T", "商社", 1.0, 1.0, 6)]
    lists = cross_section(rows, CFG, {"prev_cheap_pbr": {"1.T"}})
    sc3 = rows[2]["screens"]
    assert sc3["sector_laggard"] is True and sc3["sector_cheap_pbr"] is True
    assert rows[0]["screens"]["cheapness_resolved"] is True        # 以前は割安 → 今は中央値以上
    assert "sector_laggard" not in rows[3]["screens"]               # 3銘柄未満のセクターは判定しない
    assert rows[0]["screens"]["band_label"].startswith("低ボラ") and rows[2]["screens"]["band_label"].startswith("高ボラ")
    assert "madakabu" in sc3 and "tags" in sc3 and "セクター内で唯一安い" in sc3["tags"]
    assert isinstance(lists["month_up"], list)
