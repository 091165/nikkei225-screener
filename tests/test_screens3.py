import numpy as np
import pandas as pd
import pytest

from longterm import screens as S


# ─── Z-1 ───
@pytest.mark.parametrize("vals,exp", [([100, 110, 120, 130], "up"), ([100, 95, 110, 120], "up"), ([130, 120, 110, 100], "down"),
                                      ([100, 90, 80, 100], "flat"), ([5], "unknown")])
def test_series_trend(vals, exp):
    assert S.series_trend(vals) == exp


def test_pl_trend():
    up, down = [1, 2, 3, 4], [4, 3, 2, 1]
    assert S.pl_trend(up, up, up)["trend"] == "up"
    assert S.pl_trend(up, down, up)["trend"] == "mixed"
    assert S.pl_trend(down, down, up)["trend"] == "down"
    assert S.pl_trend([], [], [])["trend"] == "unknown"


def test_doe_and_negative_equity():
    h = S.doe_history([-30, -40, -60], [1000, 1000, 1000])
    assert h == [3.0, 4.0, 6.0]
    assert S.doe_jumped(h, 1.0) is True
    assert S.doe_jumped([3.0, 3.5], 1.0) is False
    assert S.doe_history([-30], [-500]) == [None]          # 債務超過扱い（マクドナルドなど）はエラーにしない
    assert S.ratio_pct(5, 100) == 5.0 and S.ratio_pct(5, -1) is None


# ─── Z-2 ───
def test_buy_style():
    up = {"up_24m": True, "gc_on": True}
    down = {"up_24m": False, "gc_on": False}
    assert S.buy_style("生活必需品", 3.0, down, False) == "乖離で拾ってよい"
    assert S.buy_style("テクノロジー", 1.0, up, False) == "乖離で拾ってよい"
    assert S.buy_style("ヘルスケア", 3.0, up, False) == "GC待ち"
    assert S.buy_style("テクノロジー", 0.0, down, False) == "GC待ち"      # 無配で月足が下降
    assert S.buy_style("一般消費財", 2.0, down, True) == "GC待ち"         # 月足が下降中で株主資本マイナス
    assert S.buy_style("素材", 2.0, {"up_24m": None, "gc_on": None}, False) == "—"


def test_monthly_trend_gc():
    idx = pd.bdate_range("2020-01-01", "2026-09-30")
    vals = np.r_[np.linspace(200, 100, 1200), np.linspace(100, 160, len(idx) - 1200)]
    m = S.monthly_trend(pd.Series(vals, index=idx))
    assert m["gc_on"] is True and m["months_since_gc"] is not None


# ─── Z-3 / Z-4 / Z-5 ───
def test_recession_and_bond():
    assert S.recession_signals(-0.3, -20000)["both"] is True
    assert S.recession_signals(0.4, 150000)["both"] is False
    assert S.bond_yield_pct(42.5) == pytest.approx(4.25)
    assert S.bond_yield_pct(4.25) == pytest.approx(4.25)


def test_upside_type():
    assert S.upside_type(12, 8, 3, 40, None, 3.0, 2.0) == "上げはじめ"
    assert S.upside_type(12, 8, None, 5, 90, 1.5, 2.0) == "高値追い注意"
    assert S.upside_type(5, 8, 3, 40, 90, 1.5, 2.0) == "—"              # 上方乖離でなければ判定しない
