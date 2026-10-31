"""暴落点の検出。

- 指数暴落点: 指数の25日乖離率がしきい値に「前回のタッチから reset_months 以上あいて」初めてタッチした日
- 個別暴落点: 個別銘柄の25日乖離率が、その銘柄の下限ライン（理想乖離 -2σ 相当）に同じ条件でタッチした日
- 同じ下落局面で翌日以降さらに深く下がっても、暴落点は最初のタッチ日で固定
"""

from dataclasses import asdict, dataclass

import pandas as pd


@dataclass
class CrashPoint:
    date: str               # 暴落点（最初のタッチ日）
    kind: str               # "index" / "individual"
    threshold: float        # 判定に使ったしきい値（乖離率%）
    deviation: float        # 暴落点当日の乖離率
    min_deviation: float    # エピソード中の最小乖離率
    min_deviation_date: str
    low: float | None       # エピソード中の最安値（調整後の安値）
    low_date: str | None
    episode_end: str        # 乖離率が0%以上に戻った日（または上限日数で打ち切った日）
    recovered: bool         # 乖離率が0%以上に戻ったか（False = 上限日数で打ち切り / データ末尾）
    severity: str | None = None
    source: str | None = None   # 指数暴落点の場合、どの指数か

    def to_dict(self):
        return asdict(self)


def detect_touch_dates(dev: pd.Series, threshold: float, reset_months: int = 3) -> list[pd.Timestamp]:
    """乖離率がしきい値以下になった日のうち、直前のタッチから reset_months 以上あいた最初の日を返す。

    「あいた」は最後にタッチした日から数える（同じ局面で何日もタッチが続いても、最初の日だけが暴落点）。
    """
    touches = dev.index[dev.values <= threshold]
    events = []
    last_touch = None
    for t in touches:
        if last_touch is None or t >= last_touch + pd.DateOffset(months=reset_months):
            events.append(t)
        last_touch = t
    return events


def _episode(dev: pd.Series, low: pd.Series | None, start: pd.Timestamp, max_days: int):
    """暴落点 start から、乖離率が0%以上に戻るまで（最大 max_days 営業日）の区間を返す。"""
    after = dev.loc[start:]
    window = after.iloc[: max_days + 1]
    recovered_idx = window.index[window.values >= 0]
    if len(recovered_idx) > 0:
        end = recovered_idx[0]
        recovered = True
    else:
        end = window.index[-1]
        recovered = False
    seg_dev = dev.loc[start:end]
    seg_low = low.loc[start:end].dropna() if low is not None else None
    return seg_dev, seg_low, end, recovered


def severity_label(min_dev: float, levels: dict | None) -> str | None:
    """暴落度（small/medium/large）。levels は {"small": -7, "medium": -10, "large": -20}。"""
    if not levels:
        return None
    label = None
    for name in ("small", "medium", "large"):
        if name in levels and min_dev <= levels[name]:
            label = name
    return label


def episode_point(dev: pd.Series, d: pd.Timestamp, kind: str, threshold: float, cfg: dict,
                  low: pd.Series | None = None, severity_levels: dict | None = None,
                  source: str | None = None) -> CrashPoint:
    """暴落点 d のエピソード（安値・最小乖離率・戻り日）を dev / low から計算する。"""
    seg_dev, seg_low, end, recovered = _episode(dev, low, d, cfg["crash_point"].get("episode_max_days", 60))
    min_dev_date = seg_dev.idxmin()
    if seg_low is not None and len(seg_low) > 0:
        low_date = seg_low.idxmin()
        low_val = float(seg_low.loc[low_date])
        low_date_s = low_date.strftime("%Y-%m-%d")
    else:
        low_val, low_date_s = None, None
    min_dev = float(seg_dev.loc[min_dev_date])
    return CrashPoint(
        date=d.strftime("%Y-%m-%d"),
        kind=kind,
        threshold=float(threshold),
        deviation=float(dev.loc[d]),
        min_deviation=min_dev,
        min_deviation_date=min_dev_date.strftime("%Y-%m-%d"),
        low=low_val,
        low_date=low_date_s,
        episode_end=end.strftime("%Y-%m-%d"),
        recovered=recovered,
        severity=severity_label(min_dev, severity_levels),
        source=source,
    )


def build_crash_points(dev: pd.Series, threshold: float, kind: str, cfg: dict,
                       low: pd.Series | None = None, severity_levels: dict | None = None,
                       source: str | None = None) -> list[CrashPoint]:
    """乖離率系列から暴落点を検出し、エピソード（安値・最小乖離率など）を付けて返す。"""
    dates = detect_touch_dates(dev, threshold, cfg["crash_point"].get("reset_months", 3))
    return [episode_point(dev, d, kind, threshold, cfg, low, severity_levels, source) for d in dates]


def stock_crash_points(dev: pd.Series, low: pd.Series, individual_threshold: float | None,
                       index_points: list[CrashPoint], cfg: dict) -> list[CrashPoint]:
    """1銘柄の暴落点（指数暴落点と個別暴落点の和集合）を時系列で返す。

    - 個別暴落点: その銘柄の乖離率が individual_threshold（理想乖離 -2σ 相当）に初めてタッチした日
    - 指数暴落点: 指数側で検出した日（その日以降の最初の営業日）。安値・乖離率はその銘柄の値で計算
    - エピソードが重なる暴落点は1つにまとめる（早い日を採用。種類が違えば kind="both"、安値は低い方）
    """
    pts: list[CrashPoint] = []
    if individual_threshold is not None:
        pts += build_crash_points(dev, individual_threshold, "individual", cfg, low=low)
    for ip in index_points:
        pos = dev.index.searchsorted(pd.Timestamp(ip.date))
        if pos >= len(dev):
            continue
        d = dev.index[pos]
        if (d - pd.Timestamp(ip.date)).days > 7:   # その時期に上場していない等
            continue
        pts.append(episode_point(dev, d, "index", ip.threshold, cfg, low, source=ip.source))
    pts.sort(key=lambda p: p.date)

    merged: list[CrashPoint] = []
    for p in pts:
        if merged and p.date <= merged[-1].episode_end:
            m = merged[-1]
            if p.kind != m.kind:
                m.kind = "both"
            if p.source and p.source not in (m.source or ""):
                m.source = f"{m.source},{p.source}" if m.source else p.source
            if p.low is not None and (m.low is None or p.low < m.low):
                m.low, m.low_date = p.low, p.low_date
            if p.min_deviation < m.min_deviation:
                m.min_deviation, m.min_deviation_date = p.min_deviation, p.min_deviation_date
            if p.episode_end > m.episode_end:
                m.episode_end, m.recovered = p.episode_end, p.recovered
            continue
        merged.append(p)
    return merged


def index_threshold(dev: pd.Series, ideal: dict | None, icfg: dict) -> float:
    """指数暴落点のしきい値を設定から決める（fixed: 固定値 / sigma: 理想乖離の lower2 等）。"""
    if icfg.get("threshold_mode", "fixed") == "sigma":
        if ideal is None:
            raise ValueError("sigma モードには理想乖離が必要です")
        return float(ideal[icfg.get("sigma_level", "lower2")])
    return float(icfg["threshold"])


def legacy_lower_band(dev: pd.Series, cfg: dict, as_of: pd.Timestamp | None = None) -> dict | None:
    """旧方式の下限: 過去N年の乖離率で、独立したタッチ（reset_months 以上の間隔）の回数が
    min_touches〜max_touches 回になる乖離率を二分探索で求める。

    タッチ回数はしきい値を下げるほど（おおむね）減るので、回数が範囲の中央に近づくように探す。
    見つからない場合は、範囲に最も近い回数になった値を返す（found=False）。
    """
    lcfg = cfg["legacy_lower_band"]
    reset = cfg["crash_point"].get("reset_months", 3)
    end = as_of if as_of is not None else dev.index[-1]
    start = end - pd.DateOffset(years=lcfg.get("lookback_years", 10))
    window = dev.loc[start:end].dropna()
    if len(window) < 200:
        return None
    lo_n, hi_n = lcfg.get("min_touches", 10), lcfg.get("max_touches", 20)
    target = (lo_n + hi_n) / 2

    def count(th):
        return len(detect_touch_dates(window, th, reset))

    low, high = float(window.min()), 0.0   # low: タッチ1回, high(0%): タッチ多数
    best = None
    for _ in range(40):
        mid = (low + high) / 2
        n = count(mid)
        score = 0 if lo_n <= n <= hi_n else min(abs(n - lo_n), abs(n - hi_n))
        if best is None or (score, abs(n - target)) < (best[0], abs(best[2] - target)):
            best = (score, mid, n)
        if lo_n <= n <= hi_n and abs(n - target) < 1:
            break
        if n > target:
            high = mid      # 多すぎ → しきい値を下げる
        else:
            low = mid       # 少なすぎ → しきい値を上げる
        if high - low < 0.01:
            break
    score, th, n = best
    return {
        "threshold": float(th),
        "touches": int(n),
        "found": score == 0,
        "lookback_start": window.index[0].strftime("%Y-%m-%d"),
        "lookback_end": window.index[-1].strftime("%Y-%m-%d"),
    }
