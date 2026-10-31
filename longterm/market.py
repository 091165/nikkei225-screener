"""地合い（マーケット・レジーム）パネル用のデータ。"""

import os

import numpy as np
import pandas as pd
import yaml

from .crash import severity_label

_NOTES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "crash_notes.yaml")


def load_crash_notes(path=_NOTES_PATH) -> dict:
    """暴落局面のメモ（"YYYY-MM" または "YYYY-MM-DD" → 名前）。"""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return {str(k): v for k, v in (yaml.safe_load(f) or {}).items()}


def note_for(date: str, notes: dict) -> str | None:
    return notes.get(date) or notes.get(date[:7])


def monthly_seasonality(close: pd.Series, years: int | None = None) -> list[dict]:
    """月ごとの平均騰落率・勝率（確定した月足のみ）。years を指定すると直近N年に限定。"""
    m = close.resample("ME").last().dropna()
    last = close.index[-1]
    if last.normalize() < (last + pd.offsets.BMonthEnd(0)).normalize():
        m = m.iloc[:-1]
    ret = m.pct_change().dropna() * 100
    if years:
        ret = ret.loc[ret.index[-1] - pd.DateOffset(years=years):]
    out = []
    for month in range(1, 13):
        r = ret[ret.index.month == month]
        out.append({"month": month, "avg": float(r.mean()) if len(r) else None,
                    "win_rate": float((r > 0).mean() * 100) if len(r) else None, "n": int(len(r))})
    return out


def _fwd(close: pd.Series, d: pd.Timestamp, months: int):
    target = d + pd.DateOffset(months=months)
    if target > close.index[-1]:
        return None
    pos0, pos1 = close.index.searchsorted(d), close.index.searchsorted(target)
    return float((close.iloc[pos1] / close.iloc[pos0] - 1) * 100)


def index_regime(ix: dict, df: pd.DataFrame, icfg: dict, cfg: dict, notes: dict) -> dict:
    """指数の現在の乖離率・暴落度・最後の暴落点・リセット日・過去の暴落点一覧。"""
    dev = ix["dev"]
    ideal = ix["ideal"]
    th = ix["threshold"]
    touches = dev.index[dev.values <= th]
    last_touch = touches[-1] if len(touches) else None
    reset = cfg["crash_point"].get("reset_months", 3)
    as_of = dev.index[-1]
    pts = ix["points"]
    last = pts[-1] if pts else None
    crashes = []
    for p in pts:
        d = pd.Timestamp(p.date)
        crashes.append({
            "date": p.date, "min_deviation": p.min_deviation, "min_deviation_date": p.min_deviation_date,
            "severity": p.severity, "note": note_for(p.date, notes),
            **{f"ret_{m}m": _fwd(df["close"], d, m) for m in (3, 6, 12)},
        })
    reset_date = last_touch + pd.DateOffset(months=reset) if last_touch is not None else None
    return {
        "ticker": ix["ticker"], "as_of": ix["as_of"], "close": ix["close"], "deviation": ix["deviation"],
        "threshold": th, "lower2": ideal["lower2"], "lower3": ideal["lower3"],
        "dist_to_lower2": ix["deviation"] - ideal["lower2"], "dist_to_lower3": ix["deviation"] - ideal["lower3"],
        "severity_now": severity_label(ix["deviation"], icfg.get("severity")),
        "severity_levels": icfg.get("severity"),
        "last_crash": last.date if last else None,
        "days_since_last_crash": int((as_of - pd.Timestamp(last.date)).days) if last else None,
        "last_touch": last_touch.strftime("%Y-%m-%d") if last_touch is not None else None,
        "reset_date": reset_date.strftime("%Y-%m-%d") if reset_date is not None else None,
        "armed": bool(reset_date is None or as_of >= reset_date),   # 次にタッチしたら暴落点になる状態か
        "crashes": crashes,
        "seasonality": monthly_seasonality(df["close"]),
    }


def breadth(flags: list[tuple[bool, bool]]) -> dict:
    """(当日下落, 25日線より下) のリストから割合を出す。どちらかが50%超なら地合い悪化。"""
    if not flags:
        return {"n": 0}
    arr = np.array(flags, dtype=bool)
    down, below = float(arr[:, 0].mean() * 100), float(arr[:, 1].mean() * 100)
    return {"n": int(len(arr)), "down_pct": down, "below_ma_pct": below, "weak": bool(down > 50 or below > 50)}
