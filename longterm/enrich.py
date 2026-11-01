"""追加の判定を1銘柄ごと（per_stock）と全銘柄まとめて（cross_section）計算し、まだ株格付けを付ける。"""

import numpy as np
import pandas as pd

from . import screens as S
from .conditions import statement_row


def _pbr_history(fin: dict, close: pd.Series) -> list[float]:
    """年次の株主資本 ÷ 発行済株式数 = 1株純資産。各期末の株価で割って過去のPBRを並べる。"""
    bal = (fin or {}).get("balance")
    eq, sh = statement_row(bal, "equity"), statement_row(bal, "shares")
    out = []
    for d in eq.index.intersection(sh.index):
        if sh[d] > 0 and eq[d] > 0:
            pos = close.index.searchsorted(d)
            if pos < len(close):
                out.append(float(close.iloc[pos]) / (float(eq[d]) / float(sh[d])))
    return out


def per_stock(df: pd.DataFrame, dev: pd.Series, ideal: dict, fin: dict | None, extra: dict | None, cfg: dict,
              points: list, d_result: dict, index_close: pd.Series | None, jp: bool, market: str) -> dict:
    extra = extra or {}
    fin = fin or {}
    close, low = df["close"], df["low"]
    cur = float(close.iloc[-1])
    cur_dev = float(dev.iloc[-1])
    at_lower2 = cur_dev <= ideal["lower2"]
    sma25 = float(close.rolling(cfg["deviation"].get("sma_period", 25)).mean().iloc[-1])
    price_lower2 = sma25 * (1 + ideal["lower2"] / 100)
    out = {}

    # P1-3 下げ止まり
    bot = S.bottoming_signal(df, cfg)
    out["bottoming"] = bot
    out["knife"] = bool(at_lower2 and bot.get("pass") is False)

    # Y-2 水平サポート・節目
    sup = S.support_levels(df, cfg)
    out["support"] = {"nearest": sup["nearest"], "touches": sup["nearest_touches"], "pct": sup["nearest_pct"]}
    rl = S.round_level(cur, jp)
    out["round_level"] = {"price": rl, "pct": (rl / cur - 1) * 100}
    tol = cfg["screens"]["support"]["overlap_with_lower2"]
    out["support_meets_lower2"] = bool(sup["nearest"] and abs(price_lower2 / sup["nearest"] - 1) <= tol)

    # P1-2 配当格付け（日本株）
    re_vals = statement_row(fin.get("balance"), "retained_earnings")
    re_source = "利益剰余金"
    if re_vals.empty:
        re_vals, re_source = statement_row(fin.get("balance"), "equity"), "株主資本（利益剰余金の代用）"
    re_trend = S.retained_earnings_trend(list(re_vals.values))
    payout = S.payout_history(statement_row(fin.get("cashflow"), "dividends_paid"),
                              statement_row(fin.get("income"), "net_income"))
    jump = S.payout_jump(payout, cfg)
    div_hist = extra.get("div_history")
    growth = S.dividend_growth_stats(pd.Series(div_hist)) if div_hist else None
    if jp:
        grade = S.dividend_grade(extra.get("div_yield"), extra.get("market_cap"), extra.get("div_consec_years"),
                                 re_trend if re_source == "利益剰余金" else ("unknown" if re_trend != "up" else "up"),
                                 jump, cfg, dividend_growth_ok=bool(growth and growth["ok"]))
        if re_source != "利益剰余金" and grade["grade"] == "A":
            grade["grade"] = "B"      # 利益剰余金が取れない銘柄はAにしない
    else:
        grade = {"grade": "—", "missing": [], "below_vt": (extra.get("div_yield") or 0) <= cfg["screens"]["dividend_grade"]["vt_yield"],
                 "payout_jump": jump}
    # Z-1 業績の推移・DOE・ROA・PSR
    inc, bal = fin.get("income"), fin.get("balance")
    rev = statement_row(inc, "revenue")
    ordinary = statement_row(inc, "pretax")
    if ordinary.empty:
        ordinary = statement_row(inc, "operating_income")
    ni_s = statement_row(inc, "net_income")
    pl = S.pl_trend(list(rev.values), list(ordinary.values), list(ni_s.values))
    eq_s, ta_s = statement_row(bal, "equity"), statement_row(bal, "total_assets")
    divp = statement_row(fin.get("cashflow"), "dividends_paid")
    common = divp.index.intersection(eq_s.index)
    doe_hist = S.doe_history(list(divp.loc[common].values), list(eq_s.loc[common].values))
    equity_negative = bool(len(eq_s) and eq_s.iloc[-1] <= 0)
    fcfg = cfg["fundamentals"]
    doe_jump = S.doe_jumped(doe_hist, fcfg["doe_jump_pt"])
    out["fundamentals"] = {
        "pl": pl,
        "revenue": [float(x) for x in rev.values][-4:], "ordinary": [float(x) for x in ordinary.values][-4:],
        "net_income": [float(x) for x in ni_s.values][-4:],
        "doe_history": [None if x is None else round(x, 2) for x in doe_hist][-5:],
        "doe": None if equity_negative or not doe_hist else doe_hist[-1],
        "doe_jumped": doe_jump,
        "roa": S.ratio_pct(float(ni_s.iloc[-1]) if len(ni_s) else None, float(ta_s.iloc[-1]) if len(ta_s) else None),
        "psr": (extra.get("market_cap") / float(rev.iloc[-1])) if extra.get("market_cap") and len(rev) and rev.iloc[-1] > 0 else None,
        "equity_negative": equity_negative,
    }
    # 配当格付けA の追加条件（Z-1・Y-4）: 業績が上向き/まちまち、DOEが急上昇していない、直近10年で減配なし
    if jp and grade["grade"] == "A":
        extra_missing = []
        if pl["trend"] not in ("up", "mixed"):
            extra_missing.append("業績の推移が上向き/まちまち")
        if doe_jump:
            extra_missing.append("DOEが急上昇していない")
        if div_hist:
            years = sorted(div_hist)[-fcfg["dividend_cut_years"]:]
            if any(div_hist[b_] < div_hist[a_] for a_, b_ in zip(years, years[1:])):
                extra_missing.append(f"直近{fcfg['dividend_cut_years']}年で減配なし")
        if extra_missing:
            grade = {**grade, "grade": "B", "missing": grade["missing"] + extra_missing}
    out["dividend"] = {**grade, "re_trend": re_trend, "re_source": re_source,
                       "re_values": [float(x) for x in re_vals.values][-5:],
                       "payout_history": [round(x, 1) for x in payout if np.isfinite(x)], "growth": growth}

    # P2-1 月足の形（確定足のみ）
    mon = S.completed_monthly_ohlc(df)
    out["monthly"] = S.monthly_patterns(mon, cur_dev, ideal["upper2"], cfg)

    # P2-2 暴落点での強さ
    out["crash_strength"] = S.crash_strength(points, low, close)

    # X-1 逆張り型 / 順張り押し目
    ath = float(close.max())
    ath_date = close[close == ath].index[-1]
    drop = (1 - cur / ath) * 100
    days_ath = int(len(close.loc[ath_date:]) - 1)
    po = S.perfect_order_days(close)
    out["dip"] = {"type": S.dip_type(drop, days_ath, po["days_since"], extra.get("div_yield"),
                                     extra.get("div_consec_years"), at_lower2),
                  "drop_from_ath": drop, "days_since_ath": days_ath, "ath_date": ath_date.strftime("%Y-%m-%d"),
                  "po_now": po["now"], "days_since_po": po["days_since"]}

    # Z-2 買い方の型（表示のみ）
    mtrend = S.monthly_trend(close)
    style = S.buy_style(extra.get("sector"), extra.get("div_yield"), mtrend, out["fundamentals"]["equity_negative"])
    notes = []
    if out["dip"]["type"] == "逆張り型" and style == "GC待ち":
        notes.append(f"逆張り型だが{extra.get('sector') or 'この銘柄'}のためGC待ち推奨")
    if jp and (extra.get("div_yield") or 0) >= cfg["screens"]["dividend_grade"]["min_yield"] and at_lower2 \
            and not out["support_meets_lower2"] and not bot.get("pass"):
        notes.append("日本株の高配当は下値の支えになりにくい（下がったら損切りも考える）")
    out["buy_style"] = {"style": style, "monthly": mtrend, "notes": notes}

    # X-2 -2σの幅（全銘柄の中での位置は cross_section で付ける）
    out["band_width"] = -ideal["lower2"]

    # X-3 PBRの過去比
    pbr_hist = _pbr_history(fin, close)
    pbr_now = extra.get("pbr")
    if pbr_now and len(pbr_hist) >= 3:
        lo, hi = min(pbr_hist + [pbr_now]), max(pbr_hist + [pbr_now])
        out["pbr_percentile"] = (pbr_now - lo) / (hi - lo) * 100 if hi > lo else 50.0
    else:
        out["pbr_percentile"] = None

    # Z-5 上方乖離の見分け
    out["upside"] = S.upside_type(cur_dev, ideal["mean"] + ideal["std"], mtrend.get("months_since_gc"), drop,
                                  out["pbr_percentile"], extra.get("div_yield"),
                                  cfg["screens"]["dividend_grade"]["vt_yield"])

    # X-7 直近20日の対指数の強さ
    if index_close is not None and len(close) > 21:
        ic = index_close.reindex(close.index).ffill()
        out["rel20"] = float((close.iloc[-1] / close.iloc[-21] - 1) * 100 - (ic.iloc[-1] / ic.iloc[-21] - 1) * 100)
    else:
        out["rel20"] = None

    # X-8 久々5日線上抜け / Y-3 上げ止まり・逆指値の目安
    out["short_rebound"] = S.short_rebound(close, cur_dev)
    upper1 = ideal["mean"] + ideal["std"]
    out["topping"] = S.topping_signal(df, dev, upper1)
    out["trailing_stop"] = S.trailing_stop_level(df)

    # Y-5 黒字転換 / Y-6 10年で何倍
    nis = [a["net_income"] for a in (d_result.get("annual") or [])]
    out["turnaround"] = S.turnaround(nis, drop, close)
    out["gain_10y"] = S.gain_over_years(close, 10)

    # X-4 今月・来月の傾向
    m = df.index[-1].month
    out["month_now"] = S.month_stats(close, m)
    out["month_next"] = S.month_stats(close, m % 12 + 1)

    # 米国: 配当の推移（X-5）・利回りの過去レンジ（Y-4）・配当貴族の買い場（P3-1）
    if not jp and "dividends" in df.columns:
        divs = df["dividends"]
        divs = divs[divs > 0]
        annual = S.annual_dividends(divs)
        g = S.dividend_growth_stats(annual)
        out["dividend"]["growth"] = g
        out["dividend"]["annual"] = {str(k): float(v) for k, v in annual.items()}
        out["yield_range"] = S.yield_percentile(close, divs)
        out["aristocrat"] = S.aristocrat_signal(extra.get("div_consec_years"), extra.get("div_yield"),
                                                extra.get("market_cap"), bool(g.get("cut_last_year")),
                                                market == "NASDAQ100")
    elif div_hist:
        out["dividend"]["annual"] = {str(k): float(v) for k, v in div_hist.items()}
    dps = extra.get("div_per_share")
    if dps:
        p7 = dps / 0.07
        out["price_at_7pct"] = {"price": p7, "pct": (p7 / cur - 1) * 100}
    return out


def madakabu_score(sc: dict, row: dict, ctx: dict, cfg: dict) -> dict:
    """まだ株格付け（0〜100）と内訳。"""
    w, pen = cfg["madakabu"]["weights"], cfg["madakabu"]["penalties"]
    parts = []
    g = sc["dividend"]["grade"]
    if g in w["div_grade"]:
        parts.append((f"配当格付け{g}", w["div_grade"][g]))
    if sc["monthly"].get("bear_streak_then_up") or sc["dividend"]["re_trend"] == "up":
        parts.append(("月足の初陽転／利益剰余金の増加", w["monthly_shape"]))
    dev, lower2, dist = row["deviation"], row["lower2"], row["dist_to_lower2"]
    if dev <= lower2:
        parts.append(("-2σ到達", w["lower2_hit"]))
    elif dist <= 3:
        parts.append(("-2σまで3%以内", w["lower2_near"]))
    elif dev <= 0:
        parts.append(("乖離率0以下", w["below_zero"]))
    if sc["bottoming"].get("pass"):
        parts.append(("下げ止まり", w["bottoming"]))
    if sc["crash_strength"].get("held"):
        parts.append(("暴落点で崩れず", w["crash_held"]))
    if sc.get("sector_laggard") or sc.get("sector_cheap_pbr") or (sc.get("pbr_percentile") is not None and sc["pbr_percentile"] <= 20):
        parts.append(("セクター比・過去比で割安", w["sector"]))
    if ctx.get("yen_strong") and sc.get("yen_tag") == "円高・内需":
        parts.append(("円高局面の内需株", w["yen_strong"]))
    if sc["monthly"].get("bull_streak", 0) >= cfg["screens"]["monthly"]["bull_streak_min"]:
        parts.append(("月足連続陽線", w["bull_streak"]))
    if sc["dip"]["type"] == "逆張り型":
        parts.append(("逆張り型", w["contrarian"]))
    max_total = (max(w["div_grade"].values()) + w["monthly_shape"] + w["lower2_hit"] + w["bottoming"] + w["crash_held"]
                 + w["sector"] + w["yen_strong"] + w["bull_streak"] + w["contrarian"])
    raw = sum(p for _, p in parts) * 100 / max_total
    minus = []
    if sc["dividend"].get("payout_jump"):
        minus.append(("配当性向の急上昇", pen["payout_jump"]))
    if sc["dividend"].get("below_vt"):
        minus.append(("利回り2%以下", pen["below_vt"]))
    if sc.get("knife"):
        minus.append(("-2σ以下で下げ止まり未確認", pen["knife"]))
    if (sc.get("fundamentals") or {}).get("pl", {}).get("trend") == "down":
        minus.append(("業績の推移が下降", pen.get("pl_down", -10)))
    score = max(0, min(100, round(raw + sum(p for _, p in minus))))
    return {"score": int(score), "plus": [[k, round(v * 100 / max_total, 1)] for k, v in parts], "minus": minus}


def tags(sc: dict, row: dict) -> list[str]:
    t = []
    if sc.get("knife"):
        t.append("⚠ 下げ止まり未確認")
    if sc["crash_strength"].get("held"):
        t.append("💪 暴落点で崩れず")
    if sc.get("support_meets_lower2"):
        t.append("乖離と水平線が重なる")
    if sc["dip"]["type"] == "逆張り型":
        t.append("🔻逆張り型の買い場")
    elif sc["dip"]["type"] == "順張り押し目":
        t.append("↗順張りの押し目（逆指値必須）")
    if sc["monthly"].get("bear_streak_then_up"):
        t.append("月足 陰線続き→初陽転")
    if sc["monthly"].get("gc_overheated"):
        t.append("月足GC中・日足過熱（落ち着くまで待つ）")
    if sc.get("sector_laggard"):
        t.append("セクター内で唯一安い")
    if sc.get("sector_cheap_pbr"):
        t.append("同業比PBR割安")
    if sc.get("cheapness_resolved"):
        t.append("同業比の割安が解消（利確検討）")
    if sc["short_rebound"].get("pass"):
        t.append("久々5日線上抜け（スイング）" + ("・75日線が近い" if sc["short_rebound"].get("near_75ma") else ""))
    if sc["topping"].get("pass"):
        t.append("上げ止まり（一部利確の検討）")
    if sc.get("turnaround"):
        t.append("黒字転換×長期下落")
    if sc.get("gain_10y") and sc["gain_10y"] >= 10:
        good = sc["dividend"]["re_trend"] == "up" and sc["dividend"]["grade"] in ("A", "B")
        t.append(f"10年で{sc['gain_10y']:.0f}倍（{'利益・配当の裏付けあり' if good else '過熱に注意'}）")
    if sc.get("aristocrat") in ("配当貴族の買い場", "減配：ルール上は売り"):
        t.append(sc["aristocrat"])
    if sc.get("pair_both_buy"):
        t.append("日米そろって買い場")
    if sc.get("yen_tag"):
        t.append(sc["yen_tag"])
    if sc.get("watchlist"):
        t.append("★ウォッチ")
    bs = sc.get("buy_style") or {}
    if bs.get("style") == "GC待ち":
        t.append("GC待ち推奨")
    elif bs.get("style") == "乖離で拾ってよい" and row["deviation"] <= row["lower2"]:
        t.append("乖離で拾ってよい型")
    for n in bs.get("notes", []):
        if n.startswith("日本株の高配当"):
            t.append("⚠ 高配当だけが根拠")
    if sc.get("upside") == "上げはじめ":
        t.append("↗上げはじめ（月足GC直後）")
    elif sc.get("upside") == "高値追い注意":
        t.append("⚠ 高値追い注意")
    if sc.get("bond_note"):
        t.append(sc["bond_note"])
    return t


def cross_section(results: list[dict], cfg: dict, ctx: dict) -> dict:
    """全銘柄そろってから計算する項目（セクター比較・-2σ幅の位置・円高タグ・日米ペア・まだ株格付け）。"""
    by_t = {r["ticker"]: r for r in results}
    # X-2 -2σの幅: 下位3分の1 = 低ボラ、上位3分の1 = 高ボラ
    widths = sorted(r["screens"]["band_width"] for r in results)
    if widths:
        lo, hi = np.percentile(widths, [33.3, 66.7])
        for r in results:
            w = r["screens"]["band_width"]
            r["screens"]["band_label"] = ("低ボラ：理想乖離が効きやすい" if w <= lo else
                                          "高ボラ：理想乖離は目安程度" if w >= hi else "中間")
    # P2-3 / X-6 セクター内の相対位置（手動の同業グループがあれば優先）
    peer = ctx.get("peer_groups", {})
    groups = {}
    for r in results:
        if not r["ticker"].endswith(".T"):
            continue
        key = peer.get(r["ticker"]) or (r.get("extra") or {}).get("sector")
        if key:
            groups.setdefault(key, []).append(r)
    prev_cheap = ctx.get("prev_cheap_pbr", set())
    for key, rs in groups.items():
        if len(rs) < 3:
            continue
        devs = [x["deviation"] for x in rs]
        med_dev = float(np.median(devs))
        pbrs = [(x.get("extra") or {}).get("pbr") for x in rs]
        pbrs_ok = [p for p in pbrs if p and p > 0]
        med_pbr = float(np.median(pbrs_ok)) if len(pbrs_ok) >= 3 else None
        lowest = min(rs, key=lambda x: x["deviation"])
        for x in rs:
            sc = x["screens"]
            sc["sector_group"] = key
            sc["sector_laggard"] = bool(x is lowest and med_dev > 0 and x["deviation"] <= med_dev - 5
                                        and x["dist_to_lower2"] <= 5)
            p = (x.get("extra") or {}).get("pbr")
            sc["sector_cheap_pbr"] = bool(med_pbr and p and 0 < p <= med_pbr * 0.6)
            sc["cheapness_resolved"] = bool(x["ticker"] in prev_cheap and med_pbr and p and p >= med_pbr)
    # P2-4 円高・内需タグ / X-11 日米ペア / X-9 ウォッチリスト
    dom = ctx.get("domestic", {})
    pairs = ctx.get("pairs", {})
    watch = ctx.get("watchlist", set())
    for r in results:
        sc = r["screens"]
        ratio = dom.get(r["ticker"])
        sc["yen_tag"] = None if ratio is None else ("円高・内需" if ratio >= 50 else "円安・外需")
        sc["watchlist"] = r["ticker"] in watch
        if r["ticker"] in pairs:
            us = by_t.get(pairs[r["ticker"]]["us"])
            if us:
                sc["pair"] = {"ticker": us["ticker"], "name": us["name"], "deviation": us["deviation"],
                              "at_lower2": us["deviation"] <= us["ideal"]["lower2"], "memo": pairs[r["ticker"]].get("memo")}
                sc["pair_both_buy"] = bool(sc["pair"]["at_lower2"] and r["deviation"] <= r["ideal"]["lower2"])
    # Z-4 米国株: 配当利回り − 10年債利回り
    bond = ctx.get("bond_yield")
    spread_min = cfg["macro"]["bond_spread_min"]
    for r in results:
        if r["ticker"].endswith(".T") or bond is None:
            continue
        y = (r.get("extra") or {}).get("div_yield")
        if y:
            sp = y - bond
            r["screens"]["bond_spread"] = sp
            if r["screens"].get("aristocrat") in ("配当貴族の買い場", "監視") and sp < spread_min:
                r["screens"]["bond_note"] = "債券より妙味が小さい"
    for r in results:
        row = {"deviation": r["deviation"], "lower2": r["ideal"]["lower2"], "dist_to_lower2": r["dist_to_lower2"]}
        r["screens"]["madakabu"] = madakabu_score(r["screens"], row, ctx, cfg)
        r["screens"]["tags"] = tags(r["screens"], row)

    # 一覧用の集計
    jp = [r for r in results if r["market"] == "Nikkei225"]
    month_up = sorted([r for r in jp if r["screens"]["month_now"].get("n", 0) >= 10],
                      key=lambda r: (-r["screens"]["month_now"]["win_rate"], -r["screens"]["month_now"]["avg"]))
    summary = {
        "month_up": [_mini(r, r["screens"]["month_now"]) for r in month_up[:10]],
        "month_down": [_mini(r, r["screens"]["month_now"]) for r in month_up[::-1][:10]],
        "strong_in_decline": [],
        "sector_value": [],
    }
    if ctx.get("index_dev_jp") is not None and ctx["index_dev_jp"] < 0:
        strong = sorted([r for r in jp if r["screens"].get("rel20") is not None], key=lambda r: -r["screens"]["rel20"])[:15]
        summary["strong_in_decline"] = [{"ticker": r["ticker"], "name": r["name"], "rel20": r["screens"]["rel20"],
                                         "grade": r["screens"]["dividend"]["grade"]} for r in strong]
    # Y-7 セクター別「PBR1倍割れ×利回り3%以上」
    sec = {}
    for r in jp:
        e = r.get("extra") or {}
        s_ = e.get("sector")
        if not s_:
            continue
        hit = bool(e.get("pbr") and 0 < e["pbr"] < 1 and (e.get("div_yield") or 0) >= 3)
        n, k = sec.get(s_, (0, 0))
        sec[s_] = (n + 1, k + hit)
    summary["sector_value"] = sorted([{"sector": s_, "n": n, "hits": k, "pct": k / n * 100} for s_, (n, k) in sec.items() if n >= 3],
                                     key=lambda x: (-x["pct"], -x["hits"]))[:3]
    return summary


def _mini(r, ms):
    return {"ticker": r["ticker"], "name": r["name"], "win_rate": ms.get("win_rate"), "avg": ms.get("avg"), "n": ms.get("n"),
            "significant": ms.get("significant"), "ci_low": ms.get("ci_low"), "ci_high": ms.get("ci_high")}
