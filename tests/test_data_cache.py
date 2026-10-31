import numpy as np
import pandas as pd

from longterm import data as D


def _df(start, n, base=100.0, scale=1.0):
    idx = pd.bdate_range(start, periods=n)
    c = (base + np.arange(n)) * scale
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=idx)


def test_merge_incremental_appends_new_days():
    full = _df("2024-01-01", 300)
    cached, recent = full.iloc[:290], full.iloc[280:]
    merged = D.merge_incremental(cached, recent)
    assert merged is not None
    pd.testing.assert_frame_equal(merged, full, check_freq=False)


def test_merge_incremental_detects_adjustment_change():
    full = _df("2024-01-01", 300)
    cached = full.iloc[:290]
    recent = full.iloc[280:].copy()
    recent[["open", "high", "low", "close"]] /= 2      # 株式分割で過去の調整後株価が変わった
    assert D.merge_incremental(cached, recent) is None


def test_fetch_history_uses_cache_and_refetches_on_split(tmp_path, monkeypatch):
    monkeypatch.setenv("LONGTERM_CACHE", str(tmp_path))
    calls = []
    state = {"scale": 1.0, "n": 300}

    def fake(ticker, start=None):
        calls.append(start)
        df = _df("2024-01-01", state["n"], scale=state["scale"])
        return df if start is None else df.loc[start:]

    first = D.fetch_history("TEST", downloader=fake)
    assert len(first) == 300 and calls == [None]
    state["n"] = 305
    second = D.fetch_history("TEST", downloader=fake)
    assert len(second) == 305 and calls[-1] is not None          # 差分取得
    state["scale"] = 0.5
    third = D.fetch_history("TEST", downloader=fake)
    assert calls[-1] is None                                       # 全期間を取り直し
    assert third["close"].iloc[0] == 50.0


def test_fetch_history_without_cache(monkeypatch):
    monkeypatch.delenv("LONGTERM_CACHE", raising=False)
    out = D.fetch_history("X", downloader=lambda t, start=None: _df("2024-01-01", 10))
    assert len(out) == 10
