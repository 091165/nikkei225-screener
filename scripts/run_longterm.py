#!/usr/bin/env python3
"""長期（暴落点）ロジックを全銘柄に対して実行し、JSON を出力する。

使い方:
  python scripts/run_longterm.py                       # 全銘柄
  python scripts/run_longterm.py --tickers 3038.T AAPL # 一部だけ
出力（既定）: output/docs/data/longterm/summary.json, market.json, stocks/<ticker>.json
あわせて output/breadth_history.csv（地合いの推移）と output/longterm_history.csv（スコアの記録）に追記する。
"""

import argparse
import csv
import glob
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from longterm import screens as S  # noqa: E402
from longterm.config import load_config  # noqa: E402
from longterm.data import fetch_financials, fetch_history  # noqa: E402
from longterm.enrich import cross_section  # noqa: E402
from longterm.evaluate import _clean, evaluate_index, evaluate_stock, summary_row  # noqa: E402
from longterm.indicators import deviation  # noqa: E402
from longterm.market import breadth, index_regime, load_crash_notes  # noqa: E402

BREADTH_CSV = os.path.join(ROOT, "output", "breadth_history.csv")
MACRO_LAST = os.path.join(ROOT, "output", "macro_last.json")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
HISTORY_CSV = os.path.join(ROOT, "output", "longterm_history.csv")


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
    """既存スクリーナーの最新CSVから、配当・セクター・PBR・時価総額などを銘柄ごとに読む（あれば）。"""
    files = sorted(glob.glob(os.path.join(ROOT, "output", "stock_deviation_*.csv")))
    if not files:
        return {}
    df = pd.read_csv(files[-1])
    cols = [c for c in ["div_yield", "sector", "industry", "div_per_share", "pbr", "market_cap", "payout_ratio",
                        "div_consec_years", "buy_score"] if c in df.columns]
    out = {}
    for _, r in df.iterrows():
        out[r["ticker"]] = {c: (None if pd.isna(r[c]) else r[c]) for c in cols}
    return out


def read_csv_rows(name):
    path = os.path.join(ROOT, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if any((v or "").strip() for v in r.values())]


def manual_context():
    """手動管理のCSV（円高・内需、同業グループ、日米ペア、ウォッチリスト、日本株の配当履歴）。"""
    dom = {}
    for r in read_csv_rows("jp_domestic_ratio.csv"):
        try:
            dom[r["ticker"].strip()] = float(r["japan_sales_ratio"])
        except (KeyError, ValueError):
            pass
    peer = {r["ticker"].strip(): r["group"].strip() for r in read_csv_rows("peer_groups.csv") if r.get("group")}
    pairs = {r["jp_ticker"].strip(): {"us": r["us_ticker"].strip(), "memo": r.get("memo")}
             for r in read_csv_rows("jp_us_pairs.csv") if r.get("us_ticker")}
    watch = {r["ticker"].strip() for r in read_csv_rows("watchlist.csv") if r.get("ticker")}
    divh = {}
    for r in read_csv_rows("jp_dividend_history.csv"):
        try:
            divh.setdefault(r["ticker"].strip(), {})[int(r["year"])] = float(r["div_per_share"])
        except (KeyError, ValueError):
            pass
    return {"domestic": dom, "peer_groups": peer, "pairs": pairs, "watchlist": watch, "div_history": divh}


def fetch_fred(series: str) -> pd.Series | None:
    """FRED の公開CSV（日付, 値）。取得できなければ None。"""
    try:
        import io
        import requests
        r = requests.get(FRED_URL.format(series), timeout=20)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = ["date", "value"]
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna()
        return pd.Series(df["value"].values, index=pd.to_datetime(df["date"]))
    except Exception as e:
        print(f"  [WARN] FRED {series}: {type(e).__name__}: {e}")
        return None


def macro_signals() -> dict:
    """Z-3: 長短金利差（T10Y2Y）と雇用者数の前月比（PAYEMS）。取れない日は前回値（output/macro_last.json）を使う。"""
    last = {}
    if os.path.exists(MACRO_LAST):
        with open(MACRO_LAST, encoding="utf-8") as f:
            last = json.load(f)
    out = {}
    t = fetch_fred("T10Y2Y")
    if t is not None and len(t):
        out["t10y2y"] = {"value": float(t.iloc[-1]), "date": t.index[-1].strftime("%Y-%m-%d"), "stale": False}
    elif "t10y2y" in last:
        out["t10y2y"] = {**last["t10y2y"], "stale": True}
    p = fetch_fred("PAYEMS")
    if p is not None and len(p) >= 2:
        out["payems"] = {"change": float(p.iloc[-1] - p.iloc[-2]) * 1000, "date": p.index[-1].strftime("%Y-%m-%d"), "stale": False}
    elif "payems" in last:
        out["payems"] = {**last["payems"], "stale": True}
    fresh = {k: {kk: vv for kk, vv in v.items() if kk != "stale"} for k, v in out.items() if not v.get("stale")}
    if fresh:
        with open(MACRO_LAST, "w", encoding="utf-8") as f:
            json.dump({**last, **fresh}, f, ensure_ascii=False, indent=1)
    out["signals"] = S.recession_signals((out.get("t10y2y") or {}).get("value"), (out.get("payems") or {}).get("change"))
    return out


def nikkei_per() -> dict | None:
    """manual_inputs.csv（date,nikkei_per）の最新値。"""
    rows = [r for r in read_csv_rows("manual_inputs.csv") if (r.get("nikkei_per") or "").strip()]
    if not rows:
        return None
    r = sorted(rows, key=lambda x: x["date"])[-1]
    try:
        return {"value": float(r["nikkei_per"]), "date": r["date"]}
    except ValueError:
        return None


def index_summary(ix):
    return {
        "ticker": ix["ticker"], "as_of": ix["as_of"], "close": ix["close"], "deviation": ix["deviation"],
        "threshold": ix["threshold"], "ideal": ix["ideal"],
        "crash_points": [p.to_dict() for p in ix["points"]],
    }


def ex_dividend_notice(today: pd.Timestamp) -> str | None:
    """3月・9月の権利付き最終日の5営業日前〜権利落ち日の5営業日後なら注意文（祝日は考慮しない概算）。"""
    for month in (3, 9):
        record = pd.Timestamp(today.year, month, 1) + pd.offsets.BMonthEnd(0)
        last_with = record - pd.offsets.BDay(2)
        ex_day = last_with + pd.offsets.BDay(1)
        if last_with - pd.offsets.BDay(5) <= today <= ex_day + pd.offsets.BDay(5):
            return f"{month}月の権利確定の前後です（権利付き最終日 {last_with:%m/%d} ごろ）。新しく仕込むなら権利落ち後が有利になりやすい"
    return None


def update_breadth_history(row: dict) -> list[dict]:
    """output/breadth_history.csv に今日の1行を書き（同じ日付は置き換え）、直近60行を返す。"""
    cols = list(row.keys())
    rows = []
    if os.path.exists(BREADTH_CSV):
        with open(BREADTH_CSV, encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r.get("date") != row["date"]]
    rows.append({k: row[k] for k in cols})
    rows.sort(key=lambda r: r["date"])
    with open(BREADTH_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return rows[-60:]


def update_score_history(date: str, rows: list[dict]):
    """output/longterm_history.csv に各銘柄の価格・既存スコア・まだ株格付けを記録（validate_scores.py 用）。"""
    cols = ["date", "ticker", "price", "buy_score", "madakabu", "div_grade", "cheap_pbr"]
    old = []
    if os.path.exists(HISTORY_CSV):
        with open(HISTORY_CSV, encoding="utf-8") as f:
            old = [r for r in csv.DictReader(f) if r.get("date") != date]
    new = [{"date": date, "ticker": r["ticker"], "price": round(r["price"], 4), "buy_score": r.get("buy_score"),
            "madakabu": r.get("madakabu"), "div_grade": r.get("div_grade"), "cheap_pbr": r.get("cheap_pbr")}
           for r in rows]
    with open(HISTORY_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(old + new)


def previous_cheap_pbr() -> set:
    """前回の記録で「同業比PBR割安」だった銘柄（割安が解消したかの判定用）。"""
    if not os.path.exists(HISTORY_CSV):
        return set()
    df = pd.read_csv(HISTORY_CSV)
    if df.empty or "cheap_pbr" not in df:
        return set()
    today = datetime.now().strftime("%Y-%m-%d")
    prev = df[df["date"] < today]
    if prev.empty:
        return set()
    last = prev[prev["date"] == prev["date"].max()]
    return set(last.loc[last["cheap_pbr"].astype(str) == "True", "ticker"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*", help="対象ティッカー（省略時は全銘柄）")
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "docs", "data", "longterm"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--as-of", default=None, help="評価日（YYYY-MM-DD、省略時は最新）")
    ap.add_argument("--no-history", action="store_true", help="output/*.csv に記録しない（試し実行用）")
    args = ap.parse_args()

    cfg = load_config()
    t0 = time.time()
    stocks = universe()
    if args.tickers:
        known = {t: (t, n, m) for t, n, m in stocks}
        stocks = [known.get(t, (t, t, "Nikkei225" if t.endswith(".T") else "US")) for t in args.tickers]
    extras = latest_screener_extras()
    ctx = manual_context()
    ctx["prev_cheap_pbr"] = previous_cheap_pbr()

    jp_cfg, us_cfg = cfg["crash_point"]["index_jp"], cfg["crash_point"]["index_us"]
    aux = ["JPY=X", "^VIX", "^TNX"]
    idx_df = {}
    for t in [jp_cfg["ticker"]] + us_cfg["tickers"] + aux:
        try:
            idx_df[t] = fetch_history(t)
        except Exception as e:
            print(f"  [WARN] {t}: {e}")
            idx_df[t] = pd.DataFrame()
    if args.as_of:
        idx_df = {t: d.loc[:args.as_of] for t, d in idx_df.items()}
    idx = {"jp": [evaluate_index(jp_cfg["ticker"], idx_df[jp_cfg["ticker"]], jp_cfg, cfg)],
           "us": [evaluate_index(t, idx_df[t], us_cfg, cfg) for t in us_cfg["tickers"]]}
    for grp in idx.values():
        for ix in grp:
            print(f"  index {ix['ticker']}: dev={ix['deviation']:.2f}% threshold={ix['threshold']:.2f}% "
                  f"last crash={ix['points'][-1].date if ix['points'] else '-'}")
    usdjpy_dev = None
    if not idx_df["JPY=X"].empty:
        usdjpy_dev = float(deviation(idx_df["JPY=X"]["close"], 25).iloc[-1])
    ctx["yen_strong"] = bool(usdjpy_dev is not None and usdjpy_dev < 0)   # ドル円が25日線より下 = 円高方向
    ctx["index_dev_jp"] = idx["jp"][0]["deviation"]
    ctx["bond_yield"] = (S.bond_yield_pct(float(idx_df["^TNX"]["close"].iloc[-1]))
                         if not idx_df["^TNX"].empty else None)

    def work(item):
        t, n, m = item
        df = fetch_history(t)
        if df.empty:
            raise RuntimeError("no price data")
        if args.as_of:
            df = df.loc[:args.as_of]
        fin = fetch_financials(t)
        indices = idx["jp"] if t.endswith(".T") else idx["us"]
        extra = dict(extras.get(t) or {})
        if t in ctx["div_history"]:
            extra["div_history"] = ctx["div_history"][t]
        r = evaluate_stock(t, n, m, df, fin, indices, cfg, None, extra)
        close = df["close"]
        flag = (bool(close.iloc[-1] < close.iloc[-2]),
                bool(close.iloc[-1] < close.rolling(cfg["deviation"].get("sma_period", 25)).mean().iloc[-1]))
        return r, flag, close.iloc[-300:]

    os.makedirs(os.path.join(args.out, "stocks"), exist_ok=True)
    results, failed = [], []
    flags = {"jp": [], "us": []}
    jp_closes = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, s): s for s in stocks}
        for i, f in enumerate(as_completed(futs), 1):
            t, _, m = futs[f]
            try:
                r, flag, tail = f.result()
                if m == "Nikkei225":
                    flags["jp"].append(flag)
                    jp_closes[t] = tail
                elif not t.endswith(".T"):
                    flags["us"].append(flag)
                if r is None:
                    failed.append((t, "history too short"))
                    continue
                results.append(r)
            except Exception as e:  # 1銘柄の失敗で全体を止めない
                failed.append((t, f"{type(e).__name__}: {e}"))
            if i % 50 == 0:
                print(f"  {i}/{len(stocks)} done")

    # 全銘柄そろってからの判定（セクター比較・まだ株格付けなど）
    lists = cross_section(results, cfg, ctx)
    rows = []
    for r in results:
        r = _clean(r)
        with open(os.path.join(args.out, "stocks", f"{r['ticker']}.json"), "w", encoding="utf-8") as fp:
            json.dump(r, fp, ensure_ascii=False)
        row = summary_row(r)
        row["cheap_pbr"] = bool((r.get("screens") or {}).get("sector_cheap_pbr"))
        rows.append(row)

    counts = pd.Series([r["verdict"] for r in rows]).value_counts().to_dict() if rows else {}
    as_of = idx["jp"][0]["as_of"]
    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "as_of": args.as_of or as_of,
        "indices": {k: [index_summary(ix) for ix in v] for k, v in idx.items()},
        "verdict_counts": counts,
        "stocks": sorted(rows, key=lambda r: (r["market"], r["ticker"])),
        "lists": lists,
        "failed": [{"ticker": t, "error": e} for t, e in failed],
        "holdings_config": cfg["holdings"],   # 保有管理画面（条件E・F）が同じしきい値を使うため
    }
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fp:
        json.dump(_clean(summary), fp, ensure_ascii=False)

    # 地合い（P1-1・Y-1）
    jp_rows = [r for r in rows if r["market"] == "Nikkei225"]
    b_jp = breadth(flags["jp"])
    b_jp["below_lower2_n"] = sum(1 for r in jp_rows if r["deviation"] <= r["lower2"])
    b_jp["stage"] = S.breadth_stage(b_jp.get("below_ma_pct"), cfg)
    adr = S.advance_decline_ratio(jp_closes)
    if not adr.empty:
        last_year = adr["ratio"].iloc[-245:]
        b_jp["adr25"] = float(adr["ratio"].iloc[-1])
        b_jp["adr25_min_1y"] = float(last_year.min())
        s_ = cfg["screens"]["breadth"]
        b_jp["adr25_label"] = ("過熱" if b_jp["adr25"] >= s_["adr_hot"] else
                               "売られすぎ（逆張りの局面）" if b_jp["adr25"] <= s_["adr_cold"] else "中立")
    n225 = idx["jp"][0]
    month = pd.Timestamp(as_of).month
    hist = []
    if not args.no_history and not args.tickers:
        hist = update_breadth_history({
            "date": as_of, "n": b_jp.get("n"), "below_ma_pct": round(b_jp.get("below_ma_pct", 0), 1),
            "below_lower2_n": b_jp["below_lower2_n"], "down_pct": round(b_jp.get("down_pct", 0), 1),
            "adr25": round(b_jp.get("adr25", 0), 1), "n225_dev": round(n225["deviation"], 2),
            "sector_value_top": "／".join(x["sector"] for x in lists["sector_value"]),
        })
        update_score_history(as_of, rows)
    notes = load_crash_notes()
    # Z-3: 借金の大きい大型株がそろって -2σ 以下か
    debt = [r for r in results if r["ticker"] in cfg["macro"]["debt_caps"]]
    debt_warn = bool(debt and len(debt) == len(cfg["macro"]["debt_caps"])
                     and all(r["deviation"] <= r["ideal"]["lower2"] for r in debt))
    per = nikkei_per()
    if per:
        m_ = cfg["macro"]
        per["label"] = ("買い場の目安" if per["value"] <= m_["nikkei_per_buy"] else
                        "いったん売りの目安" if per["value"] >= m_["nikkei_per_sell"] else "中間")
    vix = None
    if not idx_df["^VIX"].empty:
        vix = {"value": float(idx_df["^VIX"]["close"].iloc[-1]), "as_of": idx_df["^VIX"].index[-1].strftime("%Y-%m-%d")}
    market = {
        "generated_at": summary["generated_at"],
        "jp": {"indices": [index_regime(n225, idx_df[jp_cfg["ticker"]], jp_cfg, cfg, notes)],
               "breadth": b_jp,
               "index_stage": S.index_stage(n225["deviation"], cfg),
               "month_tendency": S.month_tendency(month, cfg),
               "ex_dividend_notice": ex_dividend_notice(pd.Timestamp(as_of)),
               "usdjpy_dev": usdjpy_dev, "yen_strong": ctx["yen_strong"],
               "breadth_history": hist,
               "breadth_note": "日経225の採用銘柄で集計（元の基準はTOPIX100）",
               "nikkei_per": per, "nikkei_per_range": [cfg["macro"]["nikkei_per_buy"], cfg["macro"]["nikkei_per_sell"]]},
        "us": {"indices": [index_regime(ix, idx_df[ix["ticker"]], us_cfg, cfg, notes) for ix in idx["us"]],
               "breadth": breadth(flags["us"]), "vix": vix,
               "macro": macro_signals() if not args.tickers else {}, "bond_yield": ctx["bond_yield"],
               "debt_caps_warning": debt_warn, "debt_caps": cfg["macro"]["debt_caps"]},
    }
    with open(os.path.join(args.out, "market.json"), "w", encoding="utf-8") as fp:
        json.dump(_clean(market), fp, ensure_ascii=False)

    # 新タブのページ（web/ の静的ファイル）を docs 直下にコピー
    docs_dir = os.path.dirname(os.path.dirname(os.path.abspath(args.out)))
    if os.path.basename(os.path.dirname(os.path.abspath(args.out))) == "data":
        web_dir = os.path.join(ROOT, "web")
        for name in os.listdir(web_dir):
            shutil.copy(os.path.join(web_dir, name), os.path.join(docs_dir, name))

    print(f"\n  long-term: {len(rows)} ok / {len(failed)} failed in {time.time() - t0:.0f}s")
    print(f"  breadth jp={ {k: v for k, v in b_jp.items() if k != 'stage'} } stage={b_jp['stage']['label']}")
    print(f"  verdicts: {counts}")
    grades = pd.Series([r["div_grade"] for r in jp_rows]).value_counts().to_dict() if jp_rows else {}
    print(f"  dividend grades (JP): {grades}  yen_strong={ctx['yen_strong']}  vix={vix}")
    print(f"  macro: {market['us']['macro']}  bond_yield={ctx['bond_yield']}  debt_caps_warning={debt_warn}  nikkei_per={per}")
    for t, e in failed[:20]:
        print(f"  [ERROR] {t}: {e}")
    top = sorted(rows, key=lambda r: -(r.get("madakabu") or 0))[:10]
    print("  まだ株格付け 上位: " + ", ".join(f"{r['ticker']}({r['madakabu']})" for r in top))
    for r in sorted(rows, key=lambda r: r["ticker"]):
        if args.tickers or r["verdict"] in ("strong_buy", "buy"):
            print(f"  {r['ticker']:8} {r['verdict_label']:10} A={r['a']} B={r['b']} C={r['c']}"
                  f"(streak {r['c_streak']}) D={r['d']} grade={r['div_grade']} madakabu={r['madakabu']} tags={r['tags']}")


if __name__ == "__main__":
    main()
