import numpy as np
import pandas as pd
import pytest

from longterm import conditions as C
from longterm.config import load_config
from longterm.crash import CrashPoint, stock_crash_points


def _cp(date, low, kind="individual", end=None):
    return CrashPoint(date=date, kind=kind, threshold=-10, deviation=-11, min_deviation=-12,
                      min_deviation_date=date, low=low, low_date=date, episode_end=end or date,
                      recovered=True)


def _close(values, start="2015-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


# ─── 条件A ───
def test_condition_a_within_window():
    cfg = load_config()
    dates = pd.bdate_range("2024-07-01", "2024-08-09")
    pts = [_cp("2024-08-05", 100, kind="index")]
    a = C.condition_a(pts, dates, cfg)
    assert a["pass"] and a["a1_index"] and not a["a2_individual"]
    a_late = C.condition_a(pts, pd.bdate_range("2024-07-01", "2024-08-20"), cfg)
    assert not a_late["pass"]


# ─── 条件B ───
def test_condition_b_recent_ath_passes():
    cfg = load_config()
    close = _close(np.linspace(100, 200, 1500))
    close.iloc[-30:] = 190      # 30営業日前に高値
    b = C.condition_b(close, cfg)
    assert b["pass"] and not b["exception"]
    assert b["days_since_high"] > 0


def test_condition_b_stale_ath_fails():
    cfg = load_config()
    vals = list(np.linspace(100, 200, 500)) + [150.0] * 300   # 高値から1年以上
    b = C.condition_b(_close(vals), cfg)
    assert b["pass"] is False


def test_condition_b_exception_for_very_old_ath():
    cfg = load_config()
    old_peak = [300.0] * 10
    slump = list(np.linspace(100, 80, 2500))      # 約10年の低迷
    recent = list(np.linspace(80, 150, 200))     # 直近で5年高値を更新
    b = C.condition_b(_close(old_peak + slump + recent, "2010-01-01"), cfg)
    assert b["exception"] is True
    assert b["pass"] is True
    assert b["ath"] == 300.0


# ─── 条件C ───
def test_condition_c_higher_lows_and_streak():
    cfg = load_config()
    close = _close(np.linspace(50, 300, 2000), "2015-01-01")
    pts = [_cp("2016-01-20", 60), _cp("2018-12-25", 90), _cp("2020-03-09", 120)]
    c = C.condition_c(pts, close, cfg)
    assert c["pass"] is True
    assert c["strength_streak"] == 2 and c["strong"] is True
    assert c["higher_highs"] is True
    assert c["prev_date"] == "2018-12-25" and c["latest_low"] == 120


def test_condition_c_lower_low_fails():
    cfg = load_config()
    close = _close(np.linspace(50, 300, 2000), "2015-01-01")
    c = C.condition_c([_cp("2018-12-25", 90), _cp("2020-03-09", 80)], close, cfg)
    assert c["pass"] is False and c["strength_streak"] == 0


def test_condition_c_needs_two_points():
    cfg = load_config()
    c = C.condition_c([_cp("2020-03-09", 80)], _close([1.0] * 100), cfg)
    assert c["pass"] is None


# ─── 条件D ───
def _income(rev, ni, start=2021, quarterly=False):
    if quarterly:
        cols = pd.date_range(f"{start}-03-31", periods=len(rev), freq="QE")
    else:
        cols = pd.to_datetime([f"{start + i}-03-31" for i in range(len(rev))])
    df = pd.DataFrame([rev, ni], index=["Total Revenue", "Net Income"], columns=cols)
    return df[df.columns[::-1]]   # yfinance は新しい期が左


def test_condition_d_growth_passes():
    cfg = load_config()
    d = C.condition_d(_income([100, 125, 160, 200], [10, 13, 17, 21]), None, None, None, cfg)
    assert d["pass"] is True
    assert d["revenue_streak"] == 3 and d["net_income_streak"] == 3
    assert d["growth_stock"] is True
    assert d["quarterly_ok"] is None


def test_condition_d_decline_fails():
    cfg = load_config()
    d = C.condition_d(_income([100, 110, 105, 120], [10, 11, 12, 13]), None, None, None, cfg)
    assert d["pass"] is False and "通期" in d["reason"]
    assert d["revenue_streak"] == 1


def test_condition_d_insufficient_is_not_pass():
    cfg = load_config()
    d = C.condition_d(_income([100, 110], [10, 11]), None, None, None, cfg)
    assert d["pass"] is None


def test_condition_d_quarterly_yoy():
    cfg = load_config()
    q = _income([10, 11, 12, 13, 12, 14, 15, 16], [1, 1, 1, 1, 1.1, 1.2, 1.3, 1.4], start=2024, quarterly=True)
    d = C.condition_d(_income([100, 110, 120], [10, 11, 12]), q, None, None, cfg)
    assert d["quarterly_ok"] is True and len(d["quarterly_yoy"]) == 4
    q_bad = _income([10, 11, 12, 13, 9, 14, 15, 16], [1, 1, 1, 1, 1.1, 1.2, 1.3, 1.4], start=2024, quarterly=True)
    d_bad = C.condition_d(_income([100, 110, 120], [10, 11, 12]), q_bad, None, None, cfg)
    assert d_bad["pass"] is False and d_bad["quarterly_ok"] is False


def test_condition_d_aux_checks():
    cfg = load_config()
    cols = pd.to_datetime(["2023-03-31", "2024-03-31", "2025-03-31"])
    inc = pd.DataFrame([[100, 120, 150], [10, 12, 15], [30, 36, 45]],
                       index=["Total Revenue", "Net Income", "Operating Income"], columns=cols)
    cf = pd.DataFrame([[25, 30, 40]], index=["Operating Cash Flow"], columns=cols)
    bs = pd.DataFrame([[70, 75, 80], [100, 110, 120]], index=["Stockholders Equity", "Total Assets"], columns=cols)
    d = C.condition_d(inc, None, cf, bs, cfg)
    assert d["aux"]["operating_cf_positive"] is True
    assert d["aux"]["ocf_matches_op_income"] is True
    assert d["aux"]["op_margin"] == pytest.approx(30.0)
    assert d["aux"]["equity_ratio"] == pytest.approx(80 / 120 * 100)


# ─── 補助シグナル ───
def test_monthly_perfect_order_uses_completed_months():
    cfg = load_config()
    close = pd.Series(np.linspace(100, 400, 900), index=pd.bdate_range("2022-01-03", periods=900))
    po = C.monthly_perfect_order(close, cfg)
    assert po["pass"] is True and po["monthly_uptrend"] is True
    # 評価日が月の途中なら当月は使わない
    m = C.completed_monthly_close(close)
    assert m.index[-1] < close.index[-1]


def test_relative_strength():
    idx = pd.bdate_range("2020-03-02", periods=10)
    s_dev = pd.Series(-5.0, index=idx)
    i_dev = pd.Series(-12.0, index=idx)
    rs = C.relative_strength(s_dev, i_dev, [_cp("2020-03-09", 1, kind="index")])
    assert rs["pass"] is True


# ─── 暴落点の統合 ───
def test_stock_crash_points_merges_overlapping():
    cfg = load_config()
    idx = pd.bdate_range("2020-01-01", "2020-12-31")
    dev = pd.Series(2.0, index=idx)
    low = pd.Series(100.0, index=idx)
    ep = pd.bdate_range("2020-03-06", "2020-04-10")
    dev.loc[ep] = -15.0
    low.loc[pd.Timestamp("2020-03-19")] = 60.0
    index_pt = _cp("2020-03-09", None, kind="index")
    index_pt.source = "^N225"
    pts = stock_crash_points(dev, low, -12.0, [index_pt], cfg)
    assert len(pts) == 1
    assert pts[0].date == "2020-03-06" and pts[0].kind == "both"
    assert pts[0].low == 60.0 and pts[0].source == "^N225"


# ─── 総合判定 ───
@pytest.mark.parametrize("a,b,c,d,rs,expected", [
    (True, True, True, True, True, "strong_buy"),
    (True, True, True, True, False, "buy"),
    (False, True, True, True, False, "watch"),
    (True, False, True, True, True, "skip"),
    (True, True, False, True, True, "skip"),
    (True, True, True, False, True, "weak_fundamentals"),
    (True, True, True, None, True, "insufficient"),
])
def test_verdict(a, b, c, d, rs, expected):
    A = {"pass": a, "a1_index": a, "a2_individual": False, "date": "2024-08-05"}
    B = {"pass": b, "exception": False, "high_date": "2024-07-11", "days_since_high": 25}
    Cc = {"pass": c, "prev_date": "2024-04-19", "prev_low": 100.0, "latest_date": "2024-08-05", "latest_low": 120.0}
    D = {"pass": d, "revenue_streak": 3, "net_income_streak": 3, "reason": None}
    v = C.verdict(A, B, Cc, D, {"pass": rs}, {"pass": False})
    assert v["code"] == expected
    assert len(v["reasons"]) == 4
