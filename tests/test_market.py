import numpy as np
import pandas as pd

from longterm.config import load_config
from longterm.evaluate import ath_marks, chart_series, evaluate_index
from longterm.indicators import deviation
from longterm.market import breadth, index_regime, monthly_seasonality, note_for


def _df(values, start="2015-01-01"):
    idx = pd.bdate_range(start, periods=len(values))
    v = np.asarray(values, dtype=float)
    return pd.DataFrame({"open": v, "high": v, "low": v, "close": v}, index=idx)


def test_monthly_seasonality_uses_completed_months():
    close = pd.Series(100.0, index=pd.bdate_range("2020-01-01", "2024-06-14"))
    close.loc[close.index.month == 3] = 90.0      # 3月は毎年下落、4月は上昇
    s = monthly_seasonality(close)
    assert s[2]["avg"] < 0 and s[2]["win_rate"] == 0
    assert s[3]["avg"] > 0
    assert s[5]["n"] == 4                        # 2024年6月（未確定）は数えない


def test_breadth_weak_flag():
    assert breadth([(True, True)] * 6 + [(False, False)] * 4)["weak"] is True
    b = breadth([(True, False)] * 4 + [(False, False)] * 6)
    assert b["down_pct"] == 40.0 and b["weak"] is False


def test_index_regime_reset_date_and_notes():
    cfg = load_config()
    vals = [100.0] * 300 + [85.0] * 3 + [100.0] * 200
    df = _df(vals, "2019-06-03")
    icfg = cfg["crash_point"]["index_jp"]
    ix = evaluate_index("^N225", df, icfg, cfg)
    reg = index_regime(ix, df, icfg, cfg, {"2020-07": "テスト局面"})
    assert reg["last_crash"] == ix["points"][-1].date
    assert reg["crashes"][-1]["note"] == "テスト局面"
    assert reg["reset_date"] > reg["last_touch"]
    assert reg["armed"] is True


def test_note_for_prefers_exact_date():
    notes = {"2020-03": "コロナショック", "2020-03-09": "暴落点"}
    assert note_for("2020-03-09", notes) == "暴落点"
    assert note_for("2020-03-19", notes) == "コロナショック"


def test_chart_series_daily_recent_weekly_old():
    df = _df(np.linspace(100, 200, 4000), "2010-01-01")
    dev = deviation(df["close"], 25)
    ch = chart_series(df, dev, daily_years=3, total_years=15)
    assert len(ch["d"]) == len(ch["c"]) == len(ch["v"])
    assert len(ch["d"]) < 4000
    assert ch["d"] == sorted(ch["d"])


def test_ath_marks_one_per_month():
    close = pd.Series(np.linspace(100, 200, 300), index=pd.bdate_range("2023-01-02", periods=300))
    marks = ath_marks(close, close.index[0])
    assert len(marks) == len({m[:7] for m in marks})
