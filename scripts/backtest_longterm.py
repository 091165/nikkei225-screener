#!/usr/bin/env python3
"""長期（暴落点）ルールのバックテスト。

使い方:
  python scripts/backtest_longterm.py                         # 日経225 + 検証用銘柄
  python scripts/backtest_longterm.py --tickers 3038.T 4776.T # 指定銘柄だけ（--no-universe と併用）
出力: docs/backtest/longterm_summary.md, longterm_trades.csv, longterm_signals.csv, longterm_summary.json
"""

import argparse
import copy
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from longterm.backtest import backtest_stock, expanding_band, summarize  # noqa: E402
from longterm.config import load_config  # noqa: E402
from longterm.crash import build_crash_points  # noqa: E402
from longterm.data import fetch_financials, fetch_history  # noqa: E402
from longterm.evaluate import _clean  # noqa: E402
from longterm.indicators import deviation  # noqa: E402

CHECK_TICKERS = {"3038.T": "神戸物産", "4776.T": "サイボウズ", "6035.T": "IRジャパン",
                 "5301.T": "東海カーボン", "9697.T": "カプコン", "2593.T": "伊藤園"}

VARIANTS = {
    "all_rules": ("全ルール（E＋F）", {}),
    "no_ath_off": ("「6か月高値更新なし」売りを使わない", {"no_ath": False}),
}


def index_points(ticker, df, icfg, cfg):
    """バックテスト用の指数暴落点。sigma モードのしきい値も時点ごと（前月末まで）に計算する。"""
    dev = deviation(df["close"], cfg["deviation"].get("sma_period", 25))
    if icfg.get("threshold_mode", "fixed") == "sigma":
        th = expanding_band(dev, cfg, icfg.get("sigma_level", "lower2"))
    else:
        th = float(icfg["threshold"])
    return dev, build_crash_points(dev, th, "index", cfg, low=df["low"], severity_levels=icfg.get("severity"),
                                   source=ticker)


def fmt(v, digits=1, suffix=""):
    return "—" if v is None or (isinstance(v, float) and pd.isna(v)) else f"{v:.{digits}f}{suffix}"


def write_report(out_dir, summaries, trades, signals, cfg, elapsed, n_stocks, failed):
    lines = ["# 長期（暴落点）ルール バックテスト結果", "",
             f"- 対象: {n_stocks}銘柄（取得失敗 {len(failed)}）、{cfg['backtest']['start']} 以降の暴落点",
             "- エントリー: 暴落点当日の終値（条件B・C合格、Dが不合格でないこと）",
             "- 業績（条件D）は決算期末から3か月遅らせて使用。その時点で業績データがない暴落点は「D未判定」としてA・B・Cだけで判定",
             f"- 比較対象: 日本株 {cfg['backtest']['benchmark_jp']} / 米国株 {cfg['backtest']['benchmark_us']} をただ持った場合",
             f"- 計算時間: {elapsed:.0f}秒", "",
             "## サマリー", "",
             "| 項目 | " + " | ".join(VARIANTS[k][0] for k in summaries) + " |",
             "|---|" + "---|" * len(summaries)]
    rows = [("トレード数", "trades", 0, ""), ("勝率", "win_rate", 1, "%"), ("平均リターン", "avg_return", 1, "%"),
            ("中央値リターン", "median_return", 1, "%"), ("平均 最大含み損", "avg_max_adverse", 1, "%"),
            ("最悪 最大含み損", "worst_max_adverse", 1, "%"), ("平均保有日数", "avg_hold_days", 0, "日"),
            ("同期間の指数リターン（平均）", "avg_bench_return", 1, "%")]
    for m in cfg["backtest"]["forward_months"]:
        rows.append((f"{m}か月後リターン（平均）", f"avg_fwd_{m}m", 1, "%"))
        rows.append((f"　同 指数", f"avg_bench_fwd_{m}m", 1, "%"))
    for label, key, dg, sfx in rows:
        lines.append(f"| {label} | " + " | ".join(fmt(s.get(key), dg, sfx) for s in summaries.values()) + " |")

    t = trades[trades["variant"] == "all_rules"]
    if not t.empty:
        lines += ["", "## 内訳（全ルール）", "", "| 区分 | トレード数 | 勝率 | 平均リターン |", "|---|---|---|---|"]
        for label, mask in [("D合格", t["d_status"] == "合格"), ("D未判定", t["d_status"] == "未判定"),
                            ("長期買い（強）", t["strong"]), ("長期買い（強以外）", ~t["strong"]),
                            ("指数要因", t["kind"].isin(["index", "both"])), ("個別要因", t["kind"] == "individual")]:
            s = t[mask]
            if len(s):
                lines.append(f"| {label} | {len(s)} | {(s['return_pct'] > 0).mean() * 100:.1f}% | {s['return_pct'].mean():.1f}% |")
        lines += ["", "### 手仕舞い理由", "", "| 理由 | 件数 | 平均リターン |", "|---|---|---|"]
        for reason, s in t.groupby(t["exit_reason"].str.replace(r"暴落点.*で", "暴落点で", regex=True)):
            lines.append(f"| {reason} | {len(s)} | {s['return_pct'].mean():.1f}% |")

    lines += ["", "## 検証ケース", ""]
    for tk, nm in CHECK_TICKERS.items():
        sg = signals[signals["ticker"] == tk]
        tr = t[t["ticker"] == tk] if not t.empty else t
        lines.append(f"### {tk} {nm}")
        if sg.empty:
            lines += ["- 暴落点なし（または取得失敗）", ""]
            continue
        lines.append("| 暴落点 | 種類 | B | C | 連続切上 | D | 判定 | 理由 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for _, r in sg.iterrows():
            if r["date"] < "2015-01-01":
                continue
            lines.append(f"| {r['date']} | {r['kind']} | {'✔' if r['b'] else '✖'} | "
                         f"{'—' if pd.isna(r['c']) else ('✔' if r['c'] else '✖')} | {r['c_streak']} | {r['d']} | "
                         f"{'買い' if r['entered'] else '見送り'} | {r['reason'] or ''} |")
        for _, r in tr.iterrows():
            lines.append(f"- トレード: {r['entry_date']} 買い {r['entry_price']:,.0f} → {r['exit_date']} "
                         f"{r['exit_reason']}、リターン {r['return_pct']:.1f}%、最大含み損 {r['max_adverse_pct']:.1f}%")
        lines.append("")
    if failed:
        lines += ["## 取得失敗", ""] + [f"- {tk}: {e}" for tk, e in failed]
    with open(os.path.join(out_dir, "longterm_summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*", default=[], help="追加で検証するティッカー")
    ap.add_argument("--no-universe", action="store_true", help="日経225を含めない")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "backtest"))
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    t0 = time.time()
    cfg = load_config()
    stocks = {}
    if not args.no_universe:
        import stock_deviation_screener as s
        stocks.update(s.NIKKEI225)
        stocks.update(CHECK_TICKERS)
    for t in args.tickers:
        stocks.setdefault(t, CHECK_TICKERS.get(t, t))

    jp_cfg, us_cfg = cfg["crash_point"]["index_jp"], cfg["crash_point"]["index_us"]
    n225 = fetch_history(jp_cfg["ticker"])
    jp_dev, jp_pts = index_points(jp_cfg["ticker"], n225, jp_cfg, cfg)
    us_pts, us_dev = [], None
    if any(not t.endswith(".T") for t in stocks):
        for t in us_cfg["tickers"]:
            dfi = fetch_history(t)
            dv, pts = index_points(t, dfi, us_cfg, cfg)
            us_pts += pts
            us_dev = us_dev if us_dev is not None else dv
    bench = {"jp": fetch_history(cfg["backtest"]["benchmark_jp"])["close"],
             "us": fetch_history(cfg["backtest"]["benchmark_us"])["close"]}

    def load(t):
        return t, fetch_history(t), fetch_financials(t)

    data, failed = {}, []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for f in as_completed([ex.submit(load, t) for t in stocks]):
            try:
                t, df, fin = f.result()
                if df.empty or len(df) < 300:
                    failed.append((t, "price history too short"))
                else:
                    data[t] = (df, fin)
            except Exception as e:
                failed.append(("?", f"{type(e).__name__}: {e}"))
    print(f"  loaded {len(data)} stocks in {time.time() - t0:.0f}s")

    all_trades, all_signals, summaries = [], [], {}
    for key, (label, rules) in VARIANTS.items():
        vcfg = copy.deepcopy(cfg)
        vcfg["backtest"]["exit_rules"].update(rules)
        trades = []
        for t, (df, fin) in data.items():
            jp = t.endswith(".T")
            try:
                tr, sg = backtest_stock(t, stocks[t], df, fin, jp_pts if jp else us_pts,
                                        jp_dev if jp else us_dev, bench["jp" if jp else "us"], vcfg)
            except Exception as e:
                failed.append((t, f"{type(e).__name__}: {e}"))
                continue
            trades += [dict(x, variant=key) for x in tr]
            if key == "all_rules":
                all_signals += sg
        all_trades += trades
        summaries[key] = summarize(pd.DataFrame(trades), vcfg)
        print(f"  [{label}] {summaries[key]}")

    os.makedirs(args.out, exist_ok=True)
    trades_df = pd.DataFrame(all_trades)
    signals_df = pd.DataFrame(all_signals)
    if not trades_df.empty:
        trades_df.drop(columns=["partial_exits"]).to_csv(os.path.join(args.out, "longterm_trades.csv"), index=False)
    signals_df.to_csv(os.path.join(args.out, "longterm_signals.csv"), index=False)
    elapsed = time.time() - t0
    write_report(args.out, summaries, trades_df, signals_df, cfg, elapsed, len(stocks), failed)
    with open(os.path.join(args.out, "longterm_summary.json"), "w", encoding="utf-8") as f:
        json.dump(_clean({"summaries": summaries, "variants": {k: v[0] for k, v in VARIANTS.items()},
                          "n_stocks": len(stocks), "failed": failed}), f, ensure_ascii=False, indent=1)
    print(open(os.path.join(args.out, "longterm_summary.md"), encoding="utf-8").read())


if __name__ == "__main__":
    main()
