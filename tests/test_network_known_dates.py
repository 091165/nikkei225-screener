"""実データ（Yahoo Finance）での検証。RUN_NETWORK_TESTS=1 のときだけ実行（GitHub Actions 用）。"""

import pandas as pd
import pytest

from longterm.config import load_config, override
from longterm.crash import build_crash_points, detect_touch_dates
from longterm.data import fetch_history
from longterm.indicators import deviation

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def n225():
    df = fetch_history("^N225")
    assert len(df) > 5000, "日経平均の日足が取得できていない"
    return df


def _events(df, threshold):
    dev = deviation(df["close"], 25)
    return [d.strftime("%Y-%m-%d") for d in detect_touch_dates(dev, threshold, 3)]


def test_n225_minus10_known_crash_points(n225):
    events = _events(n225, -10.0)
    print("^N225 -10% crash points:", events)
    for d in ["2016-01-20", "2018-12-25", "2020-03-09"]:
        assert d in events, f"{d} が検出されていない"


def test_n225_2020_entry_stays_on_first_touch(n225):
    cfg = load_config()
    dev = deviation(n225["close"], 25)
    pts = build_crash_points(dev, -10.0, "index", cfg, low=n225["low"],
                             severity_levels=cfg["crash_point"]["index_jp"]["severity"])
    covid = [p for p in pts if "2020-03-01" <= p.date <= "2020-06-30"]
    print("2020 crash points:", [p.to_dict() for p in covid])
    assert len(covid) == 1
    assert covid[0].date == "2020-03-09"
    assert covid[0].min_deviation <= -20.0      # その後 -20% 超まで下がる
    assert covid[0].severity == "large"


def test_n225_minus7_episodes_2011_2020(n225):
    cfg = override(load_config(), **{"crash_point.index_jp.threshold": -7.0})
    dev = deviation(n225["close"], 25)
    pts = build_crash_points(dev, -7.0, "index", cfg, low=n225["low"],
                             severity_levels=cfg["crash_point"]["index_jp"]["severity"])
    period = [p for p in pts if "2011-01-01" <= p.date <= "2020-12-31"]
    sev = pd.Series([p.severity for p in period]).value_counts().to_dict()
    print(f"-7% episodes 2011-2020: {len(period)} {sev}")
    for p in period:
        print(f"  {p.date} min={p.min_deviation:.1f}% ({p.severity})")
    assert 10 <= len(period) <= 18     # 目安は14回前後（大1・中7・小6）


def test_n225_minus7_detects_august_2024(n225):
    events = _events(n225, -7.0)
    aug = [d for d in events if "2024-07-25" <= d <= "2024-08-15"]
    print("2024 summer events:", aug)
    assert aug, "2024年8月の急落が検出されていない"
