import numpy as np
import pandas as pd
import pytest

from longterm.config import load_config, override
from longterm.crash import (build_crash_points, detect_touch_dates, index_threshold,
                            legacy_lower_band, severity_label)


def _daily(start, end, value=2.0):
    return pd.Series(value, index=pd.bdate_range(start, end), dtype=float)


def _set(s, date, value):
    s.loc[pd.Timestamp(date)] = value


def test_first_touch_only_within_same_episode():
    dev = _daily("2020-01-01", "2020-12-31")
    for d, v in [("2020-03-09", -10.5), ("2020-03-10", -12.0), ("2020-03-16", -20.0), ("2020-03-19", -25.0)]:
        _set(dev, d, v)
    assert detect_touch_dates(dev, -10.0, 3) == [pd.Timestamp("2020-03-09")]


def test_gap_is_measured_from_last_touch():
    dev = _daily("2019-01-01", "2020-12-31")
    _set(dev, "2019-01-10", -11)   # 暴落点
    _set(dev, "2019-03-01", -11)   # 前回タッチから2か月弱 → 暴落点ではない
    _set(dev, "2019-05-15", -11)   # 1/10 から4か月だが、直前タッチ(3/1)から2.5か月 → 暴落点ではない
    _set(dev, "2019-09-02", -11)   # 直前タッチから3か月以上 → 暴落点
    assert detect_touch_dates(dev, -10, 3) == [pd.Timestamp("2019-01-10"), pd.Timestamp("2019-09-02")]


def test_exactly_three_months_counts_as_new():
    dev = _daily("2019-01-01", "2019-12-31")
    _set(dev, "2019-01-10", -11)
    _set(dev, "2019-04-10", -11)
    assert detect_touch_dates(dev, -10, 3) == [pd.Timestamp("2019-01-10"), pd.Timestamp("2019-04-10")]


def test_crash_point_episode_low_and_fixed_entry():
    cfg = load_config()
    idx = pd.bdate_range("2020-01-01", "2020-12-31")
    dev = pd.Series(2.0, index=idx)
    low = pd.Series(100.0, index=idx)
    # 3/9 に -10% タッチ → 3/19 に -25% → 4/20 に 0% 以上に戻る
    episode = pd.bdate_range("2020-03-09", "2020-04-17")
    dev.loc[episode] = np.linspace(-10.5, -1.0, len(episode))
    dev.loc[pd.Timestamp("2020-03-19")] = -25.0
    low.loc[episode] = 90.0
    low.loc[pd.Timestamp("2020-03-19")] = 70.0
    low.loc[pd.Timestamp("2020-05-01")] = 50.0   # エピソード外の安値は含めない

    pts = build_crash_points(dev, -10.0, "index", cfg, low=low,
                             severity_levels=cfg["crash_point"]["index_jp"]["severity"], source="^N225")
    assert len(pts) == 1
    p = pts[0]
    assert p.date == "2020-03-09"
    assert p.min_deviation == pytest.approx(-25.0)
    assert p.min_deviation_date == "2020-03-19"
    assert p.low == pytest.approx(70.0)
    assert p.low_date == "2020-03-19"
    assert p.episode_end == "2020-04-20"
    assert p.recovered is True
    assert p.severity == "large"
    assert p.source == "^N225"


def test_episode_capped_at_max_days():
    cfg = override(load_config(), **{"crash_point.episode_max_days": 10})
    idx = pd.bdate_range("2021-01-01", "2021-06-30")
    dev = pd.Series(-11.0, index=idx)   # ずっと戻らない
    pts = build_crash_points(dev, -10.0, "individual", cfg)
    assert pts[0].recovered is False
    assert pd.Timestamp(pts[0].episode_end) == idx[10]
    assert pts[0].low is None


@pytest.mark.parametrize("min_dev,expected", [(-6.9, None), (-7.0, "small"), (-12.0, "medium"), (-21.0, "large")])
def test_severity_label(min_dev, expected):
    levels = load_config()["crash_point"]["index_jp"]["severity"]
    assert severity_label(min_dev, levels) == expected


def test_index_threshold_modes():
    cfg = load_config()
    ideal = {"lower2": -6.3, "lower3": -9.8}
    assert index_threshold(None, ideal, cfg["crash_point"]["index_jp"]) == -10.0
    assert index_threshold(None, ideal, cfg["crash_point"]["index_us"]) == -6.3
    us3 = dict(cfg["crash_point"]["index_us"], sigma_level="lower3")
    assert index_threshold(None, ideal, us3) == -9.8


def test_legacy_lower_band_finds_10_to_20_touches():
    cfg = load_config()
    idx = pd.bdate_range("2014-01-01", "2024-12-31")
    rng = np.random.default_rng(3)
    dev = pd.Series(rng.normal(0, 1.0, len(idx)), index=idx)
    # 4か月ごとに深さの異なる下落局面を入れる
    for i, d in enumerate(pd.date_range("2014-03-01", "2024-10-01", freq="4MS")):
        pos = idx.searchsorted(d)
        dev.iloc[pos] = -4 - (i % 15)
    band = legacy_lower_band(dev, cfg)
    assert band["found"] is True
    assert 10 <= band["touches"] <= 20
    assert band["threshold"] < 0
    assert len(detect_touch_dates(dev.loc[band["lookback_start"]:], band["threshold"], 3)) == band["touches"]
