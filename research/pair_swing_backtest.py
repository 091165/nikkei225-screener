#!/usr/bin/env python3
"""研究用（ページには出さない）: 下方乖離が多い日の「下方首位買い × 上方首位売り」スイングの検証。

日経225の採用銘柄で、25日線より下の銘柄の割合が threshold% 以上の日に、
25日乖離率が一番低い銘柄を買い、一番高い銘柄を同額空売りして hold 営業日後に両方決済したときの損益（%）を集計する。
（元の考え方は TOPIX100 が対象。ここでは日経225で代用。手数料・貸株料は考慮しない）

使い方: python research/pair_swing_backtest.py [--start 2010-01-01]
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from longterm.data import fetch_history  # noqa: E402


def run(closes: pd.DataFrame, thresholds, holds, start) -> pd.DataFrame:
    sma = closes.rolling(25).mean()
    dev = (closes / sma - 1) * 100
    below = (dev < 0).sum(axis=1) / dev.notna().sum(axis=1) * 100
    rows = []
    dates = dev.loc[start:].index
    for th in thresholds:
        for h in holds:
            pnl = []
            busy = None
            for i, d in enumerate(dates):
                if busy is not None and d <= busy or below.loc[d] < th:
                    continue
                pos = closes.index.get_loc(d)
                if pos + h >= len(closes):
                    break
                row = dev.loc[d].dropna()
                if len(row) < 10:
                    continue
                lo, hi = row.idxmin(), row.idxmax()
                exit_d = closes.index[pos + h]
                long_r = closes.loc[exit_d, lo] / closes.loc[d, lo] - 1
                short_r = 1 - closes.loc[exit_d, hi] / closes.loc[d, hi]
                pnl.append((long_r + short_r) / 2 * 100)
                busy = exit_d
            s = pd.Series(pnl, dtype=float)
            rows.append({"threshold": th, "hold": h, "trades": len(s),
                         "win_rate": (s > 0).mean() * 100 if len(s) else None,
                         "avg": s.mean() if len(s) else None, "median": s.median() if len(s) else None})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2010-01-01")
    args = ap.parse_args()
    import stock_deviation_screener as s
    tickers = list(s.NIKKEI225)
    with ThreadPoolExecutor(max_workers=8) as ex:
        data = dict(zip(tickers, ex.map(lambda t: fetch_history(t)["close"], tickers)))
    closes = pd.DataFrame(data).sort_index()
    res = run(closes, [60, 70, 80], [3, 5, 10], pd.Timestamp(args.start))
    print(res.to_string(index=False, float_format=lambda v: f"{v:.2f}"))


if __name__ == "__main__":
    main()
