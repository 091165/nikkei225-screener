#!/usr/bin/env python3
"""スイング用の別戦略のバックテスト（長期ロジックとは混ぜない）。

ルール: 5日線からの乖離率が -5% 以下になった日の終値で買い、5営業日後の終値で売る。
フィルター版: 買う日に日経平均が前日比マイナス、かつ日経平均の25日乖離率がマイナス圏のときだけ買う。
対象は既定で日経225（TOPIX100 の銘柄リストはリポジトリにないため。--tickers で差し替え可）。
"""

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from longterm.config import load_config  # noqa: E402
from longterm.data import fetch_history  # noqa: E402
from longterm.indicators import deviation  # noqa: E402


def swing_trades(ticker, df, n225, cfg, start):
    s = cfg["swing"]
    close = df["close"]
    dev5 = (close / close.rolling(s["sma"]).mean() - 1) * 100
    n_chg = n225["close"].pct_change() * 100
    n_dev = deviation(n225["close"], 25)
    out, busy = [], -1
    idx = close.index
    for i in range(len(idx) - s["hold_days"]):
        d = idx[i]
        if d < start or i <= busy or not dev5.iloc[i] <= s["entry_dev"]:
            continue
        j = i + s["hold_days"]
        ret = (close.iloc[j] / close.iloc[i] - 1) * 100
        filt = bool(d in n_chg.index and d in n_dev.index and n_chg.loc[d] < 0 and n_dev.loc[d] < 0)
        out.append({"ticker": ticker, "entry_date": d.strftime("%Y-%m-%d"), "exit_date": idx[j].strftime("%Y-%m-%d"),
                    "dev5": float(dev5.iloc[i]), "return_pct": float(ret), "market_filter": filt})
        busy = j
    return out


def stats(df):
    if df.empty:
        return "| 0 | — | — | — |"
    return (f"| {len(df)} | {(df['return_pct'] > 0).mean() * 100:.1f}% | {df['return_pct'].mean():.2f}% | "
            f"{df['return_pct'].median():.2f}% |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "backtest"))
    args = ap.parse_args()
    t0 = time.time()
    cfg = load_config()
    if args.tickers:
        tickers = args.tickers
    else:
        import stock_deviation_screener as s
        tickers = list(s.NIKKEI225)
    n225 = fetch_history("^N225")
    start = pd.Timestamp(args.start)

    def run(t):
        try:
            df = fetch_history(t)
            return swing_trades(t, df, n225, cfg, start) if len(df) > 300 else []
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=8) as ex:
        trades = [x for r in ex.map(run, tickers) for x in r]
    df = pd.DataFrame(trades)
    os.makedirs(args.out, exist_ok=True)
    df.to_csv(os.path.join(args.out, "swing_trades.csv"), index=False)
    s = cfg["swing"]
    lines = ["# スイング戦略（別戦略）バックテスト結果", "",
             f"- ルール: 5日線乖離率 {s['entry_dev']}% 以下の終値で買い、{s['hold_days']}営業日後の終値で売り",
             f"- 対象: {len(tickers)}銘柄（既定は日経225。TOPIX100 の銘柄リストは未整備）、{args.start} 以降",
             f"- 計算時間: {time.time() - t0:.0f}秒", "",
             "| 条件 | トレード数 | 勝率 | 平均リターン | 中央値 |", "|---|---|---|---|---|",
             "| フィルターなし " + stats(df),
             "| 日経が前日比マイナス かつ 日経25日乖離率マイナス " + stats(df[df["market_filter"]] if not df.empty else df),
             "| 上記以外 " + stats(df[~df["market_filter"]] if not df.empty else df)]
    with open(os.path.join(args.out, "swing_summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
