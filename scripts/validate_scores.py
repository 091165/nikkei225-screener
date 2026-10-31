#!/usr/bin/env python3
"""まだ株格付けと既存の買いスコアの比較（検証用。配点は自動で変えない）。

output/longterm_history.csv（毎日の run_longterm.py が記録）を使い、各日の上位20銘柄について
その後20営業日・60営業日の騰落率（記録された価格どうし）の平均を比べる。
記録が溜まるまでは比較できる日が少ないので、結果はその旨を表示する。

使い方: python scripts/validate_scores.py [--top 20] [--horizons 20 60]
"""

import argparse
import os

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def forward_returns(hist: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """記録日 i の価格と、i から horizon 記録日後の価格の騰落率（%）。"""
    dates = sorted(hist["date"].unique())
    px = hist.pivot_table(index="date", columns="ticker", values="price")
    fwd = (px.shift(-horizon) / px - 1) * 100
    return fwd.loc[dates[: max(0, len(dates) - horizon)]]


def compare(hist: pd.DataFrame, top: int, horizons: list[int]) -> pd.DataFrame:
    rows = []
    for h in horizons:
        fwd = forward_returns(hist, h)
        for score in ["madakabu", "buy_score"]:
            vals = []
            for d in fwd.index:
                day = hist[hist["date"] == d].dropna(subset=[score]).sort_values(score, ascending=False).head(top)
                r = fwd.loc[d, day["ticker"]].dropna()
                if len(r):
                    vals.append(r.mean())
            all_mean = fwd.stack().mean() if len(fwd) else float("nan")
            rows.append({"horizon": h, "score": score, "days": len(vals),
                         f"top{top}_avg": sum(vals) / len(vals) if vals else float("nan"), "all_avg": all_mean})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--horizons", type=int, nargs="*", default=[20, 60])
    ap.add_argument("--history", default=os.path.join(ROOT, "output", "longterm_history.csv"))
    args = ap.parse_args()
    if not os.path.exists(args.history):
        print("output/longterm_history.csv がまだありません（毎日の実行で記録されます）")
        return
    hist = pd.read_csv(args.history)
    n_days = hist["date"].nunique()
    print(f"記録: {n_days}日分（{hist['date'].min()}〜{hist['date'].max()}）")
    res = compare(hist, args.top, args.horizons)
    print(res.to_markdown(index=False, floatfmt=".2f") if hasattr(res, "to_markdown") else res)
    if n_days <= max(args.horizons):
        print(f"※ 比較には {max(args.horizons)} 営業日より長い記録が必要です。しばらく記録が溜まってから実行してください。")


if __name__ == "__main__":
    main()
