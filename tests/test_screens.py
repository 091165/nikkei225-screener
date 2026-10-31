import numpy as np
import pandas as pd
import pytest

from longterm import screens as S
from longterm.config import load_config

CFG = load_config()


def _ohlc(lows, closes=None, opens=None, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(lows))
    lows = np.asarray(lows, dtype=float)
    closes = np.asarray(closes if closes is not None else lows * 1.01, dtype=float)
    opens = np.asarray(opens if opens is not None else closes, dtype=float)
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) * 1.01, "low": lows, "close": closes}, index=idx)


# ─── 地合い（P1-1）───
@pytest.mark.parametrize("pct,code", [(0, "rest"), (50, "rest"), (50.1, "partial"), (70, "partial"), (100, "full")])
def test_breadth_stage(pct, code):
    assert S.breadth_stage(pct, CFG)["code"] == code


def test_breadth_all_plus_half_all_minus():
    from longterm.market import breadth
    assert breadth([(False, False)] * 10)["below_ma_pct"] == 0
    assert breadth([(False, True)] * 5 + [(False, False)] * 5)["below_ma_pct"] == 50
    assert breadth([(True, True)] * 10)["below_ma_pct"] == 100


@pytest.mark.parametrize("dev,code", [(1.0, "none"), (-3, "dip"), (-7, "buy"), (-12, "deep"), (-25, "historic")])
def test_index_stage(dev, code):
    assert S.index_stage(dev, CFG)["code"] == code


def test_month_tendency():
    assert "弱い" in S.month_tendency(8, CFG)
    assert "強い" in S.month_tendency(12, CFG)
    assert "中立" in S.month_tendency(5, CFG)


def test_advance_decline_ratio():
    idx = pd.bdate_range("2024-01-01", periods=60)
    up = pd.Series(np.arange(60, dtype=float), index=idx)
    down = pd.Series(-np.arange(60, dtype=float), index=idx)
    r = S.advance_decline_ratio({"a": up, "b": up, "c": down}, days=25)
    assert r["ratio"].iloc[-1] == pytest.approx(200.0)


# ─── 下げ止まり（P1-3）───
def test_bottoming_keeps_falling():
    df = _ohlc(np.linspace(100, 70, 30))
    b = S.bottoming_signal(df, CFG)
    assert b["pass"] is False and b["no_new_low"] is False


def test_bottoming_flat_bottom():
    lows = list(np.linspace(100, 80, 10)) + [80, 83, 85, 81, 84, 86, 80.5, 84, 85, 86, 87, 88, 88, 89, 90]
    b = S.bottoming_signal(_ohlc(lows), CFG)
    assert b["no_new_low"] and b["flat_bottom"] and b["pass"]


def test_bottoming_lower_wick():
    lows = list(np.linspace(100, 80, 15)) + [82, 83, 84, 78.5, 85]
    lows[14] = 78        # 最安値は6日前
    closes = [x * 1.01 for x in lows]
    opens = list(closes)
    lows[-2] = 79        # 直近5日に長い下ヒゲ（実体は小さい）
    closes[-2], opens[-2] = 84.0, 83.8
    b = S.bottoming_signal(_ohlc(lows, closes, opens), CFG)
    assert b["lower_wick"] and b["pass"]


# ─── 水平サポート・節目（Y-2）───
def test_support_levels_finds_double_bottom():
    n = 400
    lows = np.full(n, 120.0)
    lows[100], lows[250] = 100.0, 101.0     # 同じ水準で2回止まった
    lows[330] = 90.0                        # 1回だけの安値はサポートにしない
    df = _ohlc(lows, closes=np.full(n, 125.0), start="2024-01-01")
    s = S.support_levels(df, CFG)
    assert s["nearest"] == pytest.approx(100.5)
    assert s["nearest_touches"] == 2
    assert s["nearest_pct"] < 0


@pytest.mark.parametrize("price,jp,exp", [(2350, True, 2000), (2000, True, 1000), (850, True, 800),
                                          (23400, True, 20000), (187.3, False, 100), (45.2, False, 40)])
def test_round_level(price, jp, exp):
    assert S.round_level(price, jp) == exp


# ─── 配当格付け（P1-2）───
@pytest.mark.parametrize("vals,exp", [([100, 110, 120, 130], "up"), ([100, 95, 110, 120], "up"),
                                      ([100, 100, 100, 100], "flat"), ([130, 120, 110, 100], "down"),
                                      ([100], "unknown"), ([], "unknown")])
def test_retained_earnings_trend(vals, exp):
    assert S.retained_earnings_trend(vals) == exp


def test_dividend_grade():
    g = lambda **k: S.dividend_grade(**{"div_yield": 4.5, "market_cap": 5e11, "consec_years": 5, "re_trend": "up",
                                        "jump": False, "cfg": CFG, **k})["grade"]
    assert g() == "A"
    assert g(re_trend="unknown") == "B"          # 利益剰余金が取れない銘柄はAにしない
    assert g(market_cap=5e10) == "B"
    assert g(market_cap=5e10, consec_years=0) == "C"
    assert g(div_yield=1.8) == "—"
    assert g(consec_years=0, dividend_growth_ok=True) == "A"


def test_payout_jump():
    assert S.payout_jump([40, 45, 70], CFG) is True
    assert S.payout_jump([40, 45, 50], CFG) is False
    assert S.payout_jump([120], CFG) is True
    idx = pd.to_datetime(["2023-03-31", "2024-03-31"])
    hist = S.payout_history(pd.Series([-40.0, -60.0], index=idx), pd.Series([100.0, 100.0], index=idx))
    assert hist == [40.0, 60.0]


# ─── 月足（P2-1）───
def test_monthly_patterns():
    idx = pd.date_range("2022-01-31", periods=30, freq="ME")
    opens = np.full(30, 100.0)
    closes = np.full(30, 101.0)
    closes[-4:-1] = 99.0              # 陰線3本 → 最新が陽線
    m = pd.DataFrame({"open": opens, "close": closes}, index=idx)
    p = S.monthly_patterns(m, daily_dev=5.0, upper2=4.0, cfg=CFG)
    assert p["bear_streak_then_up"] is True and p["bull_streak"] == 1


def test_completed_monthly_excludes_current_month():
    df = _ohlc(np.linspace(100, 120, 300), start="2024-01-01")
    m = S.completed_monthly_ohlc(df)
    assert m.index[-1] < df.index[-1]
    # 月の途中で実行しても、確定足の結果は月末までのデータと同じ
    m2 = S.completed_monthly_ohlc(df.iloc[:-3])
    assert m2.index[-1] == m.index[-1] or m2.index[-1] < m.index[-1]
