"""1銘柄・指数の評価をまとめて行い、JSON に書ける dict を返す。"""

import math

import numpy as np
import pandas as pd

from . import conditions as C
from .crash import build_crash_points, index_threshold, legacy_lower_band, stock_crash_points
from .indicators import deviation, ideal_deviation
from .market import monthly_seasonality


def _clean(obj):
    """NaN/inf を None にして JSON に書けるようにする。"""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if hasattr(obj, "item"):          # numpy スカラー
        return _clean(obj.item())
    return obj


def chart_series(df: pd.DataFrame, dev: pd.Series, daily_years: int = 3, total_years: int = 15) -> dict:
    """詳細画面のチャート用データ。直近 daily_years 年は日足、それより前（最大 total_years 年）は週足。"""
    end = df.index[-1]
    start = end - pd.DateOffset(years=total_years)
    split = end - pd.DateOffset(years=daily_years)
    d = pd.DataFrame({"c": df["close"], "v": dev}).loc[start:]
    old = d.loc[:split].resample("W-FRI").last().dropna(subset=["c"])
    new = d.loc[split:].iloc[1:]
    d = pd.concat([old, new])
    return {"d": [x.strftime("%Y-%m-%d") for x in d.index],
            "c": [float(f"{v:.4g}") for v in d["c"]],
            "v": [None if pd.isna(v) else round(float(v), 2) for v in d["v"]]}


def ath_marks(close: pd.Series, since: pd.Timestamp) -> list[str]:
    """上場来高値を更新した日（月に1回まで）。"""
    prev_max = close.cummax().shift(1)
    new_high = close[(close > prev_max)].loc[since:]
    if new_high.empty:
        return []
    firsts = new_high.groupby(new_high.index.to_period("M")).head(1)
    return [x.strftime("%Y-%m-%d") for x in firsts.index]


def histogram(dev: pd.Series, bins: int = 40) -> dict:
    counts, edges = np.histogram(dev.dropna().values, bins=bins)
    return {"counts": [int(c) for c in counts], "edges": [round(float(e), 2) for e in edges]}


def evaluate_index(ticker: str, df: pd.DataFrame, icfg: dict, cfg: dict, as_of=None) -> dict:
    """指数の理想乖離・暴落点を計算する。icfg は crash_point.index_jp / index_us。"""
    if as_of is not None:
        df = df.loc[:pd.Timestamp(as_of)]
    sma = cfg["deviation"].get("sma_period", 25)
    dev = deviation(df["close"], sma)
    ideal = ideal_deviation(dev, cfg)
    th = index_threshold(dev, ideal, icfg)
    pts = build_crash_points(dev, th, "index", cfg, low=df["low"],
                             severity_levels=icfg.get("severity"), source=ticker)
    return {
        "ticker": ticker,
        "dev": dev,
        "ideal": ideal,
        "threshold": th,
        "points": pts,
        "as_of": df.index[-1].strftime("%Y-%m-%d"),
        "close": float(df["close"].iloc[-1]),
        "deviation": float(dev.iloc[-1]),
    }


def evaluate_stock(ticker: str, name: str, market: str, df: pd.DataFrame, fin: dict | None,
                   indices: list[dict], cfg: dict, as_of=None, extra: dict | None = None) -> dict | None:
    """1銘柄の条件A〜D・補助シグナル・総合判定を計算する。

    df: 日足（open/high/low/close、調整後）。fin: {"income","quarterly_income","cashflow","balance"}。
    indices: evaluate_index() の結果（日本株なら ^N225、米国株なら ^GSPC/^DJI）。
    extra: 既存スクリーナーの値（配当利回りなど）をそのまま載せたいとき。
    """
    if as_of is not None:
        df = df.loc[:pd.Timestamp(as_of)]
    sma = cfg["deviation"].get("sma_period", 25)
    dev = deviation(df["close"], sma)
    ideal = ideal_deviation(dev, cfg)
    if ideal is None:
        return None
    band = ideal[cfg["crash_point"]["individual"].get("band", "lower2")]
    index_points = [p for ix in indices for p in ix["points"]]
    pts = stock_crash_points(dev, df["low"], band, index_points, cfg)
    close = df["close"]

    fin = fin or {}
    a = C.condition_a(pts, dev.index, cfg)
    b = C.condition_b(close, cfg)
    c = C.condition_c(pts, close, cfg)
    d = C.condition_d(fin.get("income"), fin.get("quarterly_income"), fin.get("cashflow"), fin.get("balance"), cfg)
    main_index = indices[0] if indices else None
    rs = C.relative_strength(dev, main_index["dev"], main_index["points"]) if main_index else {"pass": None}
    po = C.monthly_perfect_order(close, cfg)
    bot = C.bottoming(df["low"], close, cfg)
    oversold = bool(dev.iloc[-1] <= ideal["lower2"])
    v = C.verdict(a, b, c, d, rs, po)

    cur = float(close.iloc[-1])
    sma_now = float(close.rolling(sma).mean().iloc[-1])
    price_at_lower2 = sma_now * (1 + ideal["lower2"] / 100)
    latest_pt = pts[-1] if pts else None
    out = {
        "ticker": ticker,
        "name": name,
        "market": market,
        "as_of": df.index[-1].strftime("%Y-%m-%d"),
        "price": cur,
        "deviation": float(dev.iloc[-1]),
        "dist_to_lower2": (cur / price_at_lower2 - 1) * 100,
        "ideal": ideal,
        "legacy_lower_band": legacy_lower_band(dev, cfg),
        "crash_points": [p.to_dict() for p in pts],
        "latest_crash": latest_pt.to_dict() if latest_pt else None,
        "A": a, "B": b, "C": c, "D": d,
        "signals": {
            "relative_strength": rs,
            "monthly_perfect_order": po,
            "monthly_up_daily_oversold": bool(po.get("monthly_uptrend") and oversold),
            "bottoming": bot,
        },
        "verdict": v,
        "chart": chart_series(df, dev),
        "ath_marks": ath_marks(close, close.index[-1] - pd.DateOffset(years=15)),
        "histogram": histogram(dev),
        "seasonality": monthly_seasonality(close, years=20),
        "dev_at_last_index_crash": None,
    }
    if main_index and main_index["points"]:
        d_last = pd.Timestamp(main_index["points"][-1].date)
        pos = dev.index.searchsorted(d_last)
        if pos < len(dev) and (dev.index[pos] - d_last).days <= 7:
            out["dev_at_last_index_crash"] = {"date": main_index["points"][-1].date,
                                              "stock_dev": float(dev.iloc[pos]),
                                              "index_dev": float(main_index["dev"].loc[d_last])
                                              if d_last in main_index["dev"].index else None}
    if out["legacy_lower_band"]:
        out["legacy_lower_band"]["diff_from_lower2"] = out["legacy_lower_band"]["threshold"] - ideal["lower2"]
    if extra:
        out["extra"] = extra
    return _clean(out)


def summary_row(r: dict) -> dict:
    """一覧テーブル用の軽量な1行。"""
    lc = r.get("latest_crash") or {}
    return {
        "ticker": r["ticker"], "name": r["name"], "market": r["market"],
        "verdict": r["verdict"]["code"], "verdict_label": r["verdict"]["label"],
        "price": r["price"], "deviation": r["deviation"], "dist_to_lower2": r["dist_to_lower2"],
        "latest_crash_date": lc.get("date"), "latest_crash_kind": lc.get("kind"),
        "a": r["A"]["pass"], "b": r["B"]["pass"], "b_exception": r["B"]["exception"],
        "days_since_high": r["B"]["days_since_high"],
        "c": r["C"]["pass"], "c_streak": r["C"]["strength_streak"], "higher_highs": r["C"]["higher_highs"],
        "d": r["D"]["pass"], "revenue_streak": r["D"]["revenue_streak"],
        "net_income_streak": r["D"]["net_income_streak"], "growth_stock": r["D"]["growth_stock"],
        "relative_strength": r["signals"]["relative_strength"].get("pass"),
        "monthly_po": r["signals"]["monthly_perfect_order"].get("pass"),
        "div_yield": (r.get("extra") or {}).get("div_yield"),
        "sector": (r.get("extra") or {}).get("sector"),
        "lower2": r["ideal"]["lower2"],
        "dev_at_last_index_crash": (r.get("dev_at_last_index_crash") or {}).get("stock_dev"),
        "bottoming": r["signals"]["bottoming"].get("pass"),
    }
