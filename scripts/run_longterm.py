#!/usr/bin/env python3
"""長期（暴落点）ロジックを全銘柄に対して実行し、JSON を出力する。

使い方:
  python scripts/run_longterm.py                       # 全銘柄
  python scripts/run_longterm.py --tickers 3038.T AAPL # 一部だけ
出力（既定）: output/docs/data/longterm/summary.json と stocks/<ticker>.json
"""

import argparse
import glob
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from longterm.config import load_config  # noqa: E402
from longterm.data import fetch_financials, fetch_history  # noqa: E402
from longterm.evaluate import _clean, evaluate_index, evaluate_stock, summary_row  # noqa: E402


def universe():
    """既存スクリーナーと同じ銘柄リスト（重複除外）を (ticker, name, market) で返す。"""
    import stock_deviation_screener as s
    out, seen = [], set()
    for market, d in [("Nikkei225", s.NIKKEI225), ("Dow30", s.DOW30), ("NASDAQ100", s.NASDAQ100),
                      ("配当貴族", s.SP500_ARISTOCRATS), ("SP500高配当", s.SP500_HIGH_DIV)]:
        for t, n in d.items():
            if t not in seen:
                out.append((t, n, market))
                seen.add(t)
    return out


def latest_screener_extras():
    """既存スクリーナーの最新CSVから、配当利回り・セクターを銘柄ごとに読む（あれば）。"""
    files = sorted(glob.glob(os.path.join(ROOT, "output", "stock_deviation_*.csv")))
    if not files:
        return {}
    df = pd.read_csv(files[-1])
    cols = [c for c in ["div_yield", "sector", "div_per_share"] if c in df.columns]
    return {r["ticker"]: {c: r[c] for c in cols} for _, r in df.iterrows()}


def index_summary(ix):
    return {
        "ticker": ix["ticker"], "as_of": ix["as_of"], "close": ix["close"], "deviation": ix["deviation"],
        "threshold": ix["threshold"], "ideal": ix["ideal"],
        "crash_points": [p.to_dict() for p in ix["points"]],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*", help="対象ティッカー（省略時は全銘柄）")
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "docs", "data", "longterm"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--as-of", default=None, help="評価日（YYYY-MM-DD、省略時は最新）")
    args = ap.parse_args()

    cfg = load_config()
    t0 = time.time()
    stocks = universe()
    if args.tickers:
        known = {t: (t, n, m) for t, n, m in stocks}
        stocks = [known.get(t, (t, t, "Nikkei225" if t.endswith(".T") else "US")) for t in args.tickers]
    extras = latest_screener_extras()

    jp_cfg, us_cfg = cfg["crash_point"]["index_jp"], cfg["crash_point"]["index_us"]
    idx = {"jp": [evaluate_index(jp_cfg["ticker"], fetch_history(jp_cfg["ticker"]), jp_cfg, cfg, args.as_of)],
           "us": [evaluate_index(t, fetch_history(t), us_cfg, cfg, args.as_of) for t in us_cfg["tickers"]]}
    for grp in idx.values():
        for ix in grp:
            print(f"  index {ix['ticker']}: dev={ix['deviation']:.2f}% threshold={ix['threshold']:.2f}% "
                  f"last crash={ix['points'][-1].date if ix['points'] else '-'}")

    def work(item):
        t, n, m = item
        df = fetch_history(t)
        if df.empty:
            raise RuntimeError("no price data")
        fin = fetch_financials(t)
        indices = idx["jp"] if t.endswith(".T") else idx["us"]
        return evaluate_stock(t, n, m, df, fin, indices, cfg, args.as_of, extras.get(t))

    os.makedirs(os.path.join(args.out, "stocks"), exist_ok=True)
    rows, failed = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, s): s for s in stocks}
        for i, f in enumerate(as_completed(futs), 1):
            t = futs[f][0]
            try:
                r = f.result()
                if r is None:
                    failed.append((t, "history too short"))
                    continue
                with open(os.path.join(args.out, "stocks", f"{t}.json"), "w", encoding="utf-8") as fp:
                    json.dump(r, fp, ensure_ascii=False)
                rows.append(summary_row(r))
            except Exception as e:  # 1銘柄の失敗で全体を止めない
                failed.append((t, f"{type(e).__name__}: {e}"))
            if i % 50 == 0:
                print(f"  {i}/{len(stocks)} done")

    counts = pd.Series([r["verdict"] for r in rows]).value_counts().to_dict() if rows else {}
    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "as_of": args.as_of,
        "indices": {k: [index_summary(ix) for ix in v] for k, v in idx.items()},
        "verdict_counts": counts,
        "stocks": sorted(rows, key=lambda r: (r["market"], r["ticker"])),
        "failed": [{"ticker": t, "error": e} for t, e in failed],
    }
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fp:
        json.dump(_clean(summary), fp, ensure_ascii=False)

    print(f"\n  long-term: {len(rows)} ok / {len(failed)} failed in {time.time() - t0:.0f}s")
    print(f"  verdicts: {counts}")
    for t, e in failed[:20]:
        print(f"  [ERROR] {t}: {e}")
    for r in sorted(rows, key=lambda r: r["ticker"]):
        if args.tickers or r["verdict"] in ("strong_buy", "buy"):
            print(f"  {r['ticker']:8} {r['verdict_label']:10} A={r['a']} B={r['b']} C={r['c']}"
                  f"(streak {r['c_streak']}) D={r['d']} last crash={r['latest_crash_date']}({r['latest_crash_kind']})")


if __name__ == "__main__":
    main()
