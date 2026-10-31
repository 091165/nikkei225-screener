import numpy as np
import pandas as pd
import pytest

from longterm import holdings as H
from longterm.backtest import expanding_band, financials_as_of, forward_returns, simulate_exit
from longterm.config import load_config, override
from longterm.crash import CrashPoint
from longterm.indicators import deviation, ideal_deviation


# ─── 条件E: 逆指値ラダー ───
@pytest.mark.parametrize("gain,expected", [
    (-15, None), (-10, None), (-9.9, -10), (0, -10), (29.9, -10), (30, 0), (49.9, 0),
    (50, 10), (99.9, 10), (100, 20), (300, 20),
])
def test_ladder_table(gain, expected):
    assert H.ladder_stop(gain, load_config()) == expected


def test_recommend_wait_then_ratchet():
    cfg = load_config()
    r = H.recommend(1000, "2024-01-10", "2024-03-01", 1200, None, cfg)
    assert r["action"] == "wait" and r["days_left"] > 0
    r = H.recommend(1000, "2024-01-10", "2024-04-15", 1600, None, cfg)
    assert r["action"] == "set_stop" and r["stop_pct"] == 10 and r["stop_price"] == 1100
    # 株価が下がっても逆指値は下げない
    r2 = H.recommend(1000, "2024-01-10", "2024-05-15", 1100, r["stop_pct"], cfg)
    assert r2["stop_pct"] == 10
    r3 = H.recommend(1000, "2024-01-10", "2024-04-15", 880, None, cfg)
    assert r3["action"] == "cut_loss"


@pytest.mark.parametrize("price,jp,expected", [
    (1234.7, True, 1234), (3456, True, 3455), (12345, True, 12340), (45678, True, 45650), (123.456, False, 123.45),
])
def test_round_to_tick(price, jp, expected):
    assert H.round_to_tick(price, jp) == pytest.approx(expected)


def test_overheat():
    cfg = load_config()
    close = pd.Series(np.r_[np.full(250, 100.0), np.linspace(100, 210, 21)],
                      index=pd.bdate_range("2023-01-02", periods=271))
    assert H.overheat(close, cfg)["pass"] is True


# ─── バックテスト ───
def _df(prices, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(prices))
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame({"open": p, "high": p, "low": p, "close": p}, index=idx)


def _cfg(**rules):
    cfg = load_config()
    cfg["backtest"]["exit_rules"] = {"stop": True, "half_profit": True, "lower_low": False, "no_ath": False, **rules}
    return cfg


def test_exit_cut_loss_after_wait():
    df = _df([1000] * 5 + [850] * 200)
    ex = simulate_exit(df, df.index[0], 1000.0, [], _cfg())
    assert ex["exit_reason"].startswith("損切り")
    assert pd.Timestamp(ex["exit_date"]) >= df.index[0] + pd.DateOffset(months=3)
    assert ex["return_pct"] == pytest.approx(-15.0)


def test_exit_ladder_stop_hit_and_never_lowered():
    # 3か月後に +60%（逆指値 +10%）→ その後 +20% まで下落 → +5% まで下落で逆指値にかかる
    prices = [1000] * 70 + [1600] * 30 + [1200] * 30 + [1050] * 10
    df = _df(prices)
    ex = simulate_exit(df, df.index[0], 1000.0, [], _cfg())
    assert ex["exit_reason"] == "逆指値"
    assert ex["return_pct"] == pytest.approx(5.0)   # 窓を開けて逆指値より下で約定（始値=1050）
    assert ex["final_stop_pct"] == 10


def test_exit_half_profit_then_stop():
    prices = [1000] * 70 + [2100] * 20 + [1150] * 5
    df = _df(prices)
    ex = simulate_exit(df, df.index[0], 1000.0, [], _cfg())
    assert ex["partial_exits"] and ex["partial_exits"][0]["fraction"] == 0.5
    # 半分は 2100、残りは +20% 逆指値（1150 で約定）
    assert ex["return_pct"] == pytest.approx((0.5 * 2100 + 0.5 * 1150) / 1000 * 100 - 100)


def test_exit_lower_low_at_next_crash():
    df = _df([1000] * 30 + [800] * 10 + [1000] * 60)
    entry = df.index[0]
    prev = CrashPoint("2019-12-01", "individual", -10, -12, -12, "2019-12-01", 900.0, "2019-12-01",
                      "2019-12-20", True)
    cur = CrashPoint(entry.strftime("%Y-%m-%d"), "index", -10, -12, -12, entry.strftime("%Y-%m-%d"), 950.0,
                     entry.strftime("%Y-%m-%d"), entry.strftime("%Y-%m-%d"), True)
    nxt_date = df.index[30].strftime("%Y-%m-%d")
    nxt = CrashPoint(nxt_date, "individual", -10, -15, -15, nxt_date, 800.0, nxt_date,
                     df.index[39].strftime("%Y-%m-%d"), True)
    ex = simulate_exit(df, entry, 1000.0, [prev, cur, nxt], _cfg(lower_low=True))
    assert ex["exit_reason"].startswith(f"暴落点{nxt_date}")
    assert ex["exit_date"] == nxt_date


def test_open_position_marked_to_market():
    df = _df(np.linspace(1000, 1250, 50))
    ex = simulate_exit(df, df.index[0], 1000.0, [], _cfg())
    assert ex["closed"] is False and ex["exit_reason"] == "保有中"


def test_financials_as_of_applies_lag():
    cols = pd.to_datetime(["2025-03-31", "2024-03-31", "2023-03-31"])
    fin = {"income": pd.DataFrame([[3, 2, 1]], index=["Total Revenue"], columns=cols)}
    out = financials_as_of(fin, pd.Timestamp("2024-08-01"), 3)
    assert list(pd.to_datetime(out["income"].columns)) == list(pd.to_datetime(["2024-03-31", "2023-03-31"]))
    assert financials_as_of(fin, pd.Timestamp("2023-05-01"), 3)["income"] is None


def test_expanding_band_has_no_lookahead():
    cfg = load_config()
    rng = np.random.default_rng(5)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 2000))), index=pd.bdate_range("2015-01-01", periods=2000))
    dev = deviation(close, 25)
    band = expanding_band(dev, cfg)
    d = pd.Timestamp("2019-06-14")
    prev_month_end = pd.Timestamp("2019-05-31")
    assert band.loc[d] == pytest.approx(ideal_deviation(dev.loc[:prev_month_end], cfg)["lower2"])
    # 未来のデータを変えても過去の値は変わらない
    dev2 = dev.copy()
    dev2.loc["2020-01-01":] = -50
    assert expanding_band(dev2, cfg).loc[d] == pytest.approx(band.loc[d])


def test_forward_returns():
    close = pd.Series(np.linspace(100, 200, 600), index=pd.bdate_range("2020-01-01", periods=600))
    fr = forward_returns(close, close.index[0], [3, 6, 24, 36])
    assert fr["fwd_3m"] > 0 and fr["fwd_24m"] > fr["fwd_6m"]
    assert fr["fwd_36m"] is None
