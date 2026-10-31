"""25日乖離率と理想乖離の計算。"""

import numpy as np
import pandas as pd


def deviation(close: pd.Series, sma_period: int = 25) -> pd.Series:
    """25日乖離率（%）= (終値 − SMA) ÷ SMA × 100。SMAが計算できない先頭部分は除く。"""
    sma = close.rolling(sma_period).mean()
    dev = (close - sma) / sma * 100
    return dev.dropna()


def ideal_deviation(dev: pd.Series, cfg: dict) -> dict | None:
    """乖離率の分布から理想乖離を求める。

    sigma 方式（平均 ± k×標準偏差）とパーセンタイル方式の両方を返し、
    cfg["ideal_deviation"]["method"] で選んだ方を lower2/lower3/upper2/upper3 に入れる。
    履歴が min_days 未満なら None。
    """
    icfg = cfg["ideal_deviation"]
    dev = dev.dropna()
    if len(dev) < icfg.get("min_days", 200):
        return None

    mean = float(dev.mean())
    std = float(dev.std())
    k2, k3 = icfg.get("sigma_k", [2, 3])
    sigma = {
        "lower2": mean - k2 * std,
        "lower3": mean - k3 * std,
        "upper2": mean + k2 * std,
        "upper3": mean + k3 * std,
    }
    p = icfg["percentiles"]
    percentile = {name: float(np.percentile(dev.values, p[name]))
                  for name in ("lower2", "lower3", "upper2", "upper3")}

    method = icfg.get("method", "percentile")
    chosen = percentile if method == "percentile" else sigma
    return {
        "method": method,
        **chosen,
        "sigma": sigma,
        "percentile": percentile,
        "mean": mean,
        "std": std,
        "stat_days": int(len(dev)),
        "start_date": dev.index[0].strftime("%Y-%m-%d"),
        "end_date": dev.index[-1].strftime("%Y-%m-%d"),
        "min": float(dev.min()),
        "max": float(dev.max()),
    }
