#!/usr/bin/env python3
"""
Web Dashboard Generator v2 — スッキリ版
========================================
注目銘柄カードTop5 + 簡潔なテーブル
"""

import pandas as pd
import os
from datetime import datetime
import glob

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
PAGES_DIR = os.path.join(OUTPUT_DIR, "docs")

ZONE_JP = {
    "STRONG BUY": "強い買い", "BUY ZONE": "買いゾーン", "MILD DIP": "軽い押し目",
    "NEUTRAL": "平常圏", "OVERBOUGHT": "過熱", "EXTREME HIGH": "極端な高値",
}
ZONE_CLS = {
    "STRONG BUY": "z-strong", "BUY ZONE": "z-buy", "MILD DIP": "z-mild",
    "NEUTRAL": "z-neutral", "OVERBOUGHT": "z-over", "EXTREME HIGH": "z-extreme",
}
MARKET_JP = {"Nikkei225": "日経225", "Dow30": "ダウ30", "NASDAQ100": "NASDAQ100", "配当貴族": "配当貴族", "SP500高配当": "SP500高配当"}


def _top5_cards(df):
    top5 = df.nlargest(5, "buy_score").to_dict("records")
    cards = ""
    for i, r in enumerate(top5):
        score = r.get("buy_score", 0)
        zone = r.get("zone", "")
        div_val = r.get("div_yield", 0) or 0
        dev = r.get("deviation", 0)
        dist = r.get("dist_to_2s", 0)
        consec = r.get("div_consec_years", 0) or 0
        market = MARKET_JP.get(r.get("market", ""), r.get("market", ""))

        if dist <= 0:
            ring = "ring-hot"
        elif dist <= 5:
            ring = "ring-warm"
        else:
            ring = "ring-ok"

        rank_label = ["①", "②", "③", "④", "⑤"][i]
        # リングには-2σまでの距離を表示
        ring_text = f"{dist:+.0f}%"

        cards += f"""<div class="top-card">
          <div class="top-rank">{rank_label}</div>
          <div class="top-score-ring {ring}"><span>{ring_text}</span></div>
          <div class="top-info">
            <div class="top-ticker">{r['ticker']}</div>
            <div class="top-name">{r['name']}</div>
            <div class="top-market">{market}</div>
          </div>
          <div class="top-metrics">
            <div class="top-metric">
              <span class="top-val {'neg' if dev < 0 else 'pos'}">{dev:+.1f}%</span>
              <span class="top-lbl">乖離率</span>
            </div>
            <div class="top-metric">
              <span class="top-val {'pos' if div_val >= 3 else ''}">{div_val:.1f}%</span>
              <span class="top-lbl">配当</span>
            </div>
            <div class="top-metric">
              <span class="top-val {'pos' if consec >= 5 else ''}">{consec if consec > 0 else '—'}</span>
              <span class="top-lbl">連続増配</span>
            </div>
          </div>
          <div class="top-zone"><span class="zone-badge {ZONE_CLS.get(zone, '')}">{ZONE_JP.get(zone, zone)}</span></div>
          <div class="top-signals">{_signals_html(r)}</div>
        </div>"""
    return cards


def _row(rank, r):
    dev = r.get("deviation", 0)
    dist = r.get("dist_to_2s", 0)
    div_val = r.get("div_yield", 0) or 0
    score = r.get("buy_score", 0) or 0
    zone = r.get("zone", "")
    market = r.get("market", "")
    price = r.get("price", 0) or 0
    div_thr = 4.0 if market == "Nikkei225" else 6.0
    consec = r.get("div_consec_years", 0) or 0
    pbr = r.get("pbr", 0) or 0
    biz = r.get("biz_momentum", "—")
    ath_drop = r.get("drop_from_ath", 0) or 0
    rsi = r.get("rsi_14", 0) or 0
    per = r.get("per", 0) or 0
    roe = r.get("roe", 0) or 0
    w52 = r.get("week52_pos", 0) or 0

    # 行クラス
    rc = ""
    if zone in ["STRONG BUY", "BUY ZONE"] and div_val >= div_thr:
        rc = "row-hot"
    elif zone in ["STRONG BUY", "BUY ZONE"]:
        rc = "row-buy"
    elif score >= 70:
        rc = "row-hot"
    elif score >= 50:
        rc = "row-warm"

    # 色クラス
    pbr_cls = "neg" if 0 < pbr < 1 else ("hot" if 0 < pbr < 1.5 else "")
    pbr_txt = f"{pbr:.1f}" if pbr > 0 else "—"
    biz_cls = "pos" if biz == "増収増益" else ("neg" if biz in ["減収減益","減益"] else "")
    sc = "s-h" if score >= 70 else ("s-m" if score >= 50 else ("s-l" if score >= 30 else "s-n"))
    rsi_cls = "neg" if 0 < rsi <= 30 else ("hot" if rsi >= 70 else "")
    per_cls = "pos" if 0 < per <= 10 else ""
    roe_cls = "pos" if roe >= 15 else ("neg" if roe < 0 else "")

    return f"""<tr class="{rc}" data-score="{score}" data-dist="{dist}" data-div="{div_val}" data-dev="{dev}" data-consec="{consec}" data-pbr="{pbr}" data-ath="{ath_drop}" data-rsi="{rsi}" data-per="{per}" data-roe="{roe}" data-w52="{w52}">
      <td>{rank}</td>
      <td><div class="sc {sc}"><b>{score}</b><div class="sf" style="width:{score}%"></div></div></td>
      <td class="sig-cell">{_signals_html(r)}</td>
      <td><span class="zone-badge {ZONE_CLS.get(zone,'')}">{ZONE_JP.get(zone,zone)}</span></td>
      <td><b>{r['ticker']}</b></td>
      <td>{r['name']} <span class="mkt">{MARKET_JP.get(market, market)}</span></td>
      <td class="hide-m">{r.get('sector','')[:12]}</td>
      <td>{price:,.0f}</td>
      <td class="{'neg' if dev<0 else 'pos'}">{dev:+.1f}%</td>
      <td class="{'neg hot' if dist<=0 else ('hot' if dist<=3 else '')}">{dist:+.1f}%</td>
      <td class="{'pos' if div_val>=div_thr else ''}">{div_val:.1f}%</td>
      <td class="{'pos' if consec>=5 else ''}">{consec if consec>0 else '—'}</td>
      <td class="{pbr_cls}">{pbr_txt}</td>
      <td class="hide-m {per_cls}">{f'{per:.0f}' if per > 0 else '—'}</td>
      <td class="hide-m {roe_cls}">{f'{roe:.0f}%' if roe != 0 else '—'}</td>
      <td class="hide-m {rsi_cls}">{f'{rsi:.0f}' if rsi > 0 else '—'}</td>
      <td class="hide-m">{w52:.0f}%</td>
      <td class="hide-m {biz_cls}">{biz}</td>
      <td class="hide-m">{ath_drop:.0f}%</td>
    </tr>"""


def _signals_html(r):
    """シグナルをタグ表示（4種類）"""
    tags = []
    if r.get("flag_high_div"): tags.append('<span class="sig sig-div">高配当</span>')
    if r.get("zone") in ["STRONG BUY", "BUY ZONE"]: tags.append('<span class="sig sig-buy">-2σ突入</span>')
    if r.get("pbr_under1") or r.get("is_quality"): tags.append('<span class="sig sig-qual">優良割安</span>')
    if r.get("cross_above_25ma") or r.get("method12"): tags.append('<span class="sig sig-ma">MA転換</span>')
    return "".join(tags) if tags else '<span class="dim">—</span>'


def _table(records, tid):
    rows = "".join(_row(i+1, r) for i, r in enumerate(records))
    return f"""<div class="tw"><table id="{tid}"><thead><tr>
      <th>#</th>
      <th class="sortable" data-sort="score">スコア ⇅</th>
      <th>シグナル</th>
      <th>ゾーン</th><th>コード</th><th>銘柄名</th>
      <th class="hide-m">セクター</th><th>株価</th>
      <th class="sortable" data-sort="dev">乖離率 ⇅</th>
      <th class="sortable" data-sort="dist">-2σまで ⇅</th>
      <th class="sortable" data-sort="div">配当 ⇅</th>
      <th class="sortable" data-sort="consec">連続増配 ⇅</th>
      <th class="sortable" data-sort="pbr">PBR ⇅</th>
      <th class="hide-m sortable" data-sort="per">PER ⇅</th>
      <th class="hide-m sortable" data-sort="roe">ROE ⇅</th>
      <th class="hide-m sortable" data-sort="rsi">RSI ⇅</th>
      <th class="hide-m sortable" data-sort="w52">52週 ⇅</th>
      <th class="hide-m">業績</th>
      <th class="hide-m sortable" data-sort="ath">ATH下落 ⇅</th>
    </tr></thead><tbody>{rows}</tbody></table></div>"""


def _market_line(region):
    """長期（暴落点）タブの地合いを1行で（market.json がある場合だけ）。"""
    path = os.path.join(PAGES_DIR, "data", "longterm", "market.json")
    if not os.path.exists(path):
        return ""
    try:
        import json
        with open(path, encoding="utf-8") as f:
            m = json.load(f).get(region) or {}
        if region == "jp":
            ix = (m.get("indices") or [{}])[0]
            b = m.get("breadth") or {}
            parts = [f"日経 25日乖離 {ix.get('deviation', 0):.1f}%（{(m.get('index_stage') or {}).get('label', '—')}）",
                     f"下方乖離 {b.get('below_ma_pct', 0):.0f}%（{(b.get('stage') or {}).get('label', '—')}）",
                     m.get("month_tendency") or ""]
        else:
            vix = m.get("vix") or {}
            ix = (m.get("indices") or [{}])[0]
            parts = [f"S&P500 25日乖離 {ix.get('deviation', 0):.1f}%", f"VIX {vix.get('value', 0):.1f}（参考）" if vix else ""]
        text = "　／　".join(p for p in parts if p)
        return (f'<div style="font-size:0.75rem;margin:0.6rem 0;padding:0.5rem 0.7rem;background:var(--s1);border:1px solid var(--bd);'
                f'border-radius:6px">地合い：{text}　<a href="longterm.html" style="color:var(--ac)">長期タブで詳しく →</a></div>')
    except Exception:
        return ""


def generate_html(df, today, page_config):
    """
    page_config = {
        "title": "日本株",
        "filename": "index.html",
        "markets": ["Nikkei225"],
        "tabs": [("all","全て"), ("n225","日経225")],
        "tab_keys": {"all": None, "n225": "Nikkei225"},
        "nav_active": "jp",
        "other_page": ("us.html", "米国株"),
    }
    """
    title = page_config["title"]
    markets = page_config["markets"]
    nav_active = page_config["nav_active"]

    # Filter to this page's markets
    page_df = df[df["market"].isin(markets)].copy()
    page_df.reset_index(drop=True, inplace=True)

    # 統計
    n_strong = len(page_df[page_df["zone"]=="STRONG BUY"])
    n_buy = len(page_df[page_df["zone"]=="BUY ZONE"])
    n_near = len(page_df[page_df["dist_to_2s"]<=3.0])
    n_signals = len(page_df[page_df["n_signals"]>=1])
    n_over = len(page_df[page_df["zone"].isin(["OVERBOUGHT","EXTREME HIGH"])])

    # マーケット別バー
    zone_order = ["STRONG BUY","BUY ZONE","MILD DIP","NEUTRAL","OVERBOUGHT","EXTREME HIGH"]
    zone_colors = ["#ef4444","#f97316","#eab308","#22c55e","#a855f7","#ec4899"]
    market_bars = ""
    for m in markets:
        mdf = page_df[page_df["market"]==m]
        total = len(mdf)
        if total == 0:
            continue
        bars = ""
        for z, col in zip(zone_order, zone_colors):
            cnt = len(mdf[mdf["zone"]==z])
            if cnt > 0:
                pct = cnt/total*100
                bars += f'<div class="zb" style="width:{max(pct,3)}%;background:{col}" title="{ZONE_JP[z]}: {cnt}">{cnt}</div>'
        market_bars += f'<div class="mb"><div class="mb-label">{MARKET_JP.get(m,m)} <span>({total})</span></div><div class="mb-bars">{bars}</div></div>'

    # タブデータ
    all_data = {}
    for m in markets:
        all_data[m] = page_df[page_df["market"]==m].to_dict("records")

    # タブ生成（タブがない場合はテーブル直接表示）
    if page_config["tabs"]:
        tab_buttons = f'<button class="tb active" onclick="switchTab(\'all\',this)">全て ({len(page_df)})</button>\n'
        for tab_id, tab_label, market_key in page_config["tabs"]:
            data = all_data.get(market_key, [])
            tab_buttons += f'        <button class="tb" onclick="switchTab(\'{tab_id}\',this)">{tab_label} ({len(data)})</button>\n'
        tab_panels = f'  <div id="tab-all" class="tp active">{_table(page_df.to_dict("records"), "tbl-all")}</div>\n'
        for tab_id, tab_label, market_key in page_config["tabs"]:
            data = all_data.get(market_key, [])
            tab_panels += f'  <div id="tab-{tab_id}" class="tp">{_table(data, "tbl-{tab_id}")}</div>\n'
        tabs_html = f"""<div class="tabs">{tab_buttons}</div>"""
    else:
        tabs_html = ""
        tab_panels = _table(page_df.to_dict("records"), "tbl-all")

    # ナビゲーション
    jp_cls = "nav-active" if nav_active == "jp" else ""
    us_cls = "nav-active" if nav_active == "us" else ""

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>理想乖離ダッシュボード — {title}</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Noto+Sans+JP:wght@300;400;600;700&display=swap" rel="stylesheet">
<style>
:root{{--bg:#0a0e17;--s1:#111827;--s2:#1a2235;--bd:#1e2a3a;--tx:#e2e8f0;--dim:#64748b;--ac:#38bdf8;--red:#ef4444;--org:#f97316;--ylw:#eab308;--grn:#22c55e;--prp:#a855f7;--pnk:#ec4899}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Noto Sans JP','JetBrains Mono',sans-serif;background:var(--bg);color:var(--tx);min-height:100vh}}
.grain{{position:fixed;inset:0;background:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");pointer-events:none;z-index:9999}}

/* Nav */
.nav{{display:flex;gap:0;border-bottom:2px solid var(--bd);background:var(--s1)}}
.nav a{{padding:0.8rem 1.5rem;font-family:'JetBrains Mono',monospace;font-size:0.8rem;font-weight:600;color:var(--dim);text-decoration:none;transition:all 0.15s;border-bottom:2px solid transparent;margin-bottom:-2px}}
.nav a:hover{{color:var(--tx)}}
.nav a.nav-active{{color:var(--ac);border-bottom-color:var(--ac)}}

header{{padding:1.5rem 2rem 1rem;border-bottom:1px solid var(--bd);background:linear-gradient(180deg,rgba(17,24,39,0.95),var(--bg))}}
header h1{{font-family:'JetBrains Mono',monospace;font-size:1.4rem;font-weight:700;background:linear-gradient(135deg,var(--ac),#818cf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.meta{{display:flex;gap:1.2rem;margin-top:0.4rem;font-size:0.75rem;color:var(--dim);font-family:'JetBrains Mono',monospace}}
.meta .pulse{{width:8px;height:8px;border-radius:50%;background:var(--grn);animation:pulse 2s infinite;display:inline-block}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.3}}}}

.wrap{{max-width:1300px;margin:0 auto;padding:1.2rem;position:relative;z-index:1}}
.stats{{display:flex;gap:0.6rem;margin:1.2rem 0;flex-wrap:wrap}}
.st{{background:var(--s1);border:1px solid var(--bd);border-radius:10px;padding:0.8rem 1.2rem;text-align:center;flex:1;min-width:100px}}
.st .n{{font-family:'JetBrains Mono',monospace;font-size:1.8rem;font-weight:700}}
.st .l{{font-size:0.65rem;color:var(--dim);font-family:'JetBrains Mono',monospace;text-transform:uppercase;letter-spacing:0.8px;margin-top:2px}}
.sec{{font-family:'JetBrains Mono',monospace;font-size:1rem;font-weight:700;margin:1.8rem 0 0.8rem;display:flex;align-items:center;gap:0.4rem}}
.sec .dot{{width:8px;height:8px;border-radius:50%}}
.top5{{display:grid;grid-template-columns:repeat(5,1fr);gap:0.8rem;margin-bottom:2rem}}
.top-card{{background:var(--s1);border:1px solid var(--bd);border-radius:12px;padding:1rem;position:relative;transition:all 0.2s;display:flex;flex-direction:column;align-items:center;text-align:center;gap:0.5rem}}
.top-card:hover{{border-color:var(--ac);box-shadow:0 0 24px rgba(56,189,248,0.1);transform:translateY(-3px)}}
.top-rank{{position:absolute;top:8px;left:10px;font-size:0.9rem;opacity:0.4}}
.top-score-ring{{width:52px;height:52px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-family:'JetBrains Mono',monospace;font-size:1.1rem;font-weight:700;position:relative}}
.top-score-ring::before{{content:'';position:absolute;inset:-3px;border-radius:50%;border:3px solid transparent}}
.ring-hot{{background:rgba(239,68,68,0.15);color:var(--red)}}.ring-hot::before{{border-color:var(--red)}}
.ring-warm{{background:rgba(249,115,22,0.15);color:var(--org)}}.ring-warm::before{{border-color:var(--org)}}
.ring-ok{{background:var(--s2);color:var(--dim)}}.ring-ok::before{{border-color:var(--bd)}}
.top-ticker{{font-family:'JetBrains Mono',monospace;font-weight:700;font-size:0.85rem}}
.top-name{{font-size:0.72rem;color:var(--dim);max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.top-market{{font-size:0.6rem;color:var(--dim);opacity:0.6}}
.top-metrics{{display:flex;gap:0.8rem;margin-top:0.3rem}}
.top-metric{{text-align:center}}
.top-val{{display:block;font-family:'JetBrains Mono',monospace;font-size:0.8rem;font-weight:600}}
.top-lbl{{display:block;font-size:0.55rem;color:var(--dim);margin-top:1px}}
.top-zone{{margin-top:0.2rem}}
.top-signals{{margin-top:0.3rem;display:flex;flex-wrap:wrap;justify-content:center;gap:2px}}
.market-section{{margin-bottom:1.5rem}}
.mb{{margin-bottom:0.5rem}}
.mb-label{{font-size:0.75rem;font-family:'JetBrains Mono',monospace;margin-bottom:3px}}
.mb-label span{{color:var(--dim)}}
.mb-bars{{display:flex;gap:2px;height:22px;border-radius:4px;overflow:hidden}}
.zb{{display:flex;align-items:center;justify-content:center;font-size:0.6rem;font-weight:700;color:white;font-family:'JetBrains Mono',monospace;border-radius:3px;min-width:18px;transition:width 0.8s ease}}
.sec2{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem;margin:1.5rem 0 0.6rem}}
.search input{{padding:0.45rem 0.8rem;background:var(--s2);border:1px solid var(--bd);border-radius:6px;color:var(--tx);font-family:'JetBrains Mono',monospace;font-size:0.75rem;outline:none;width:260px}}
.search input:focus{{border-color:var(--ac)}}
.search input::placeholder{{color:var(--dim)}}
.tabs{{display:flex;gap:0.4rem;flex-wrap:wrap}}
.tb{{padding:0.4rem 0.8rem;border:1px solid var(--bd);border-radius:5px;background:transparent;color:var(--dim);font-family:'JetBrains Mono',monospace;font-size:0.7rem;cursor:pointer;transition:all 0.15s}}
.tb:hover{{border-color:var(--ac);color:var(--tx)}}
.tb.active{{background:var(--ac);color:var(--bg);border-color:var(--ac);font-weight:700}}
.tp{{display:none}}.tp.active{{display:block}}
.tw{{overflow-x:auto;border:1px solid var(--bd);border-radius:8px;background:var(--s1);max-height:75vh;overflow-y:auto}}
table{{width:100%;border-collapse:collapse;font-size:0.78rem}}
th{{position:sticky;top:0;z-index:10;background:var(--s2);padding:0.6rem 0.7rem;text-align:left;font-weight:600;font-family:'JetBrains Mono',monospace;font-size:0.65rem;text-transform:uppercase;letter-spacing:0.3px;color:var(--dim);border-bottom:1px solid var(--bd);white-space:nowrap}}
td{{padding:0.45rem 0.7rem;border-bottom:1px solid var(--bd);font-family:'JetBrains Mono',monospace;font-size:0.75rem;white-space:nowrap}}
tr:hover td{{background:rgba(56,189,248,0.04)}}
.sortable{{cursor:pointer;user-select:none}}.sortable:hover{{color:var(--ac)}}
.zone-badge{{display:inline-block;padding:2px 7px;border-radius:3px;font-size:0.6rem;font-weight:700;letter-spacing:0.2px}}
.z-strong{{background:var(--red);color:white}}
.z-buy{{background:var(--org);color:white}}
.z-mild{{background:rgba(234,179,8,0.2);color:var(--ylw)}}
.z-neutral{{background:rgba(34,197,94,0.15);color:var(--grn)}}
.z-over{{background:rgba(168,85,247,0.2);color:var(--prp)}}
.z-extreme{{background:rgba(236,72,153,0.2);color:var(--pnk)}}
.neg{{color:var(--red)}}.pos{{color:var(--grn)}}.hot{{color:var(--org);font-weight:700}}.dim{{color:var(--dim)}}
.mkt{{font-size:0.5rem;color:var(--dim);background:var(--s2);padding:1px 4px;border-radius:2px;margin-left:4px;font-weight:400;vertical-align:middle}}
.sig-cell{{white-space:normal;min-width:80px;max-width:180px}}
.sig{{display:inline-block;padding:1px 5px;border-radius:3px;font-size:0.55rem;font-weight:700;margin:1px;letter-spacing:0.2px}}
.sig-div{{background:rgba(34,197,94,0.2);color:var(--grn)}}
.sig-buy{{background:rgba(239,68,68,0.25);color:var(--red)}}
.sig-qual{{background:rgba(56,189,248,0.2);color:var(--ac)}}
.sig-ma{{background:rgba(249,115,22,0.2);color:var(--org)}}
.sc{{position:relative;width:46px;height:20px;background:var(--s2);border-radius:3px;overflow:hidden;display:inline-flex;align-items:center;justify-content:center}}
.sc b{{position:relative;z-index:2;font-size:0.65rem;font-family:'JetBrains Mono',monospace}}
.sf{{position:absolute;left:0;top:0;height:100%;border-radius:3px;z-index:1}}
.s-h .sf{{background:linear-gradient(90deg,#dc2626,#ef4444)}}.s-h b{{color:white}}
.s-m .sf{{background:linear-gradient(90deg,#ea580c,#f97316)}}.s-m b{{color:white}}
.s-l .sf{{background:linear-gradient(90deg,#ca8a04,#eab308)}}.s-l b{{color:var(--bg)}}
.s-n .sf{{background:var(--s2)}}.s-n b{{color:var(--dim)}}
.row-hot td{{background:rgba(239,68,68,0.07)!important}}.row-hot td:first-child{{border-left:3px solid var(--red)}}
.row-buy td{{background:rgba(249,115,22,0.05)!important}}
.row-warm td{{background:rgba(234,179,8,0.03)!important}}
footer{{text-align:center;padding:1.5rem;font-size:0.65rem;color:var(--dim);font-family:'JetBrains Mono',monospace;border-top:1px solid var(--bd);margin-top:2rem}}
.legend{{background:var(--s1);border:1px solid var(--bd);border-radius:10px;padding:1rem 1.2rem;margin-bottom:1.5rem}}
.legend-title{{font-family:'JetBrains Mono',monospace;font-size:0.75rem;font-weight:700;color:var(--ac);margin-bottom:0.6rem}}
.legend-items{{display:flex;flex-wrap:wrap;gap:0.5rem 1.5rem}}
.legend-item{{font-size:0.7rem;color:var(--dim);display:flex;align-items:center;gap:0.4rem}}

@media(max-width:900px){{
  .top5{{grid-template-columns:repeat(2,1fr)}}
  .top5 .top-card:nth-child(5){{grid-column:span 2;max-width:50%;margin:0 auto}}
}}
@media(max-width:600px){{
  header{{padding:1.2rem 1rem 0.8rem}}
  header h1{{font-size:1.1rem}}
  .wrap{{padding:0.6rem}}
  .stats{{gap:0.4rem}}.st{{padding:0.5rem 0.6rem}}.st .n{{font-size:1.3rem}}
  .top5{{grid-template-columns:1fr 1fr}}
  .top5 .top-card:nth-child(5){{grid-column:span 2;max-width:100%}}
  .top-card{{padding:0.7rem}}
  .top-score-ring{{width:40px;height:40px;font-size:0.9rem}}
  .tabs{{flex-wrap:wrap}}.tb{{font-size:0.6rem;padding:0.3rem 0.5rem}}
  .search input{{width:100%}}
  .hide-m{{display:none}}
  table{{font-size:0.68rem}}td,th{{padding:0.35rem 0.45rem}}
  .nav a{{padding:0.6rem 1rem;font-size:0.7rem}}
}}
</style>
</head>
<body>
<div class="grain"></div>

<div class="nav">
  <a href="index.html" class="{jp_cls}">🇯🇵 日本株</a>
  <a href="us.html" class="{us_cls}">🇺🇸 米国株</a>
  <a href="longterm.html">📉 長期（暴落点）</a>
  <a href="holdings.html">💼 保有管理</a>
</div>

<header>
  <h1>理想乖離ダッシュボード — {title}</h1>
  <div class="meta">
    <span><span class="pulse"></span> {today}</span>
    <span>全{len(page_df)}銘柄</span>
  </div>
</header>

<div class="wrap">
{_market_line(nav_active)}

  <div class="stats">
    <div class="st"><div class="n" style="color:var(--red)">{n_strong}</div><div class="l">強い買い</div></div>
    <div class="st"><div class="n" style="color:var(--org)">{n_buy}</div><div class="l">買いゾーン</div></div>
    <div class="st"><div class="n" style="color:var(--ylw)">{n_near}</div><div class="l">-2σ 3%以内</div></div>
    <div class="st"><div class="n" style="color:var(--ac)">{n_signals}</div><div class="l">シグナル点灯</div></div>
    <div class="st"><div class="n" style="color:var(--prp)">{n_over}</div><div class="l">過熱圏</div></div>
  </div>

  <div class="sec"><span class="dot" style="background:var(--red)"></span> 注目銘柄 Top5</div>
  <div class="top5">
    {_top5_cards(page_df)}
  </div>

  <div class="sec"><span class="dot" style="background:var(--ac)"></span> ゾーン分布</div>
  <div class="market-section">
    {market_bars}
  </div>

  <div class="legend">
    <div class="legend-title">シグナル説明</div>
    <div class="legend-items">
      <div class="legend-item"><span class="sig sig-div">高配当</span>日本株4%超・米国株6%超</div>
      <div class="legend-item"><span class="sig sig-buy">-2σ突入</span>統計的な買いゾーン（-2σ以下）</div>
      <div class="legend-item"><span class="sig sig-qual">優良割安</span>PBR1倍割れ or 自己資本比率50%+ROE10%</div>
      <div class="legend-item"><span class="sig sig-ma">MA転換</span>日足25MA上抜け or 月足ゴールデンクロス</div>
    </div>
  </div>

  <div class="sec2">
    <div class="sec" style="margin:0"><span class="dot" style="background:var(--grn)"></span> 全銘柄データ</div>
    <div style="display:flex;gap:0.6rem;align-items:center;flex-wrap:wrap">
      <div class="search"><input type="text" id="si" placeholder="コード・銘柄名で検索..." oninput="doFilter()"></div>
      {tabs_html}
    </div>
  </div>

  {tab_panels}

</div>

<footer>理想乖離ダッシュボード &middot; GitHub Actionsで毎営業日自動更新<br>データ: Yahoo Finance &middot; 投資判断は自己責任で行ってください</footer>

<script>
function switchTab(id,btn){{
  document.querySelectorAll('.tp').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tb').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+id).classList.add('active');
  btn.classList.add('active');
}}
function doFilter(){{
  const q=document.getElementById('si').value.toLowerCase();
  // タブあり: .tp.active内を検索、タブなし: 全tbody内を検索
  let rows = document.querySelectorAll('.tp.active tbody tr');
  if (rows.length === 0) rows = document.querySelectorAll('tbody tr');
  rows.forEach(r=>{{
    r.style.display=r.textContent.toLowerCase().includes(q)?'':'none';
  }});
}}
document.addEventListener('click',function(e){{
  const th=e.target.closest('.sortable');
  if(!th)return;
  const tbl=th.closest('table');if(!tbl)return;
  const tb=tbl.querySelector('tbody');
  const rows=Array.from(tb.querySelectorAll('tr'));
  const key=th.dataset.sort;
  const asc=th.classList.contains('sort-asc');
  tbl.querySelectorAll('.sortable').forEach(s=>s.classList.remove('sort-asc','sort-desc'));
  th.classList.add(asc?'sort-desc':'sort-asc');
  rows.sort((a,b)=>{{
    const va=parseFloat(a.dataset[key])||0,vb=parseFloat(b.dataset[key])||0;
    return asc?va-vb:vb-va;
  }});
  rows.forEach((r,i)=>{{r.cells[0].textContent=i+1;tb.appendChild(r)}});
}});
</script>
</body>
</html>"""


def main():
    os.makedirs(PAGES_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    csv_files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "stock_deviation_*.csv")))
    xlsx_files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "stock_deviation_*.xlsx")))

    if csv_files:
        df = pd.read_csv(csv_files[-1])
    elif xlsx_files:
        df = pd.read_excel(xlsx_files[-1], sheet_name=0)
        col_map = {
            "順位": "rank", "買いスコア": "buy_score", "ゾーン": "zone",
            "コード": "ticker", "銘柄名": "name",
            "市場": "market", "株価": "price", "25日MA": "sma25",
            "乖離率%": "deviation", "-2σ%": "sigma2_lower", "-3σ%": "sigma3_lower",
            "-2σ株価": "price_at_2s", "-3σ株価": "price_at_3s",
            "-2σまで%": "dist_to_2s", "配当利回り%": "div_yield", "1株配当": "div_per_share",
            "配当@-2σ%": "div_at_2s",
            "標準偏差": "std", "統計日数": "stat_days",
        }
        df.rename(columns=col_map, inplace=True)
    else:
        print("  [ERROR] No data files found")
        return

    for col, default in [("div_yield",0.0),("div_at_2s",0.0),("buy_score",0),
                         ("n_signals",0),("signals","—"),("sector",""),
                         ("drop_from_ath",0.0),("half_from_ath",False),
                         ("cross_above_25ma",False),("monthly_bullish",False),
                         ("method12",False),("flag_high_div",False),("sector_sole_dip",False),
                         ("div_consec_years",0),("pbr",0.0),("pbr_under1",False),
                         ("biz_momentum","—"),("rev_growth_pct",0.0),("earn_growth_pct",0.0),
                         ("rsi_14",0.0),("rsi_oversold",False),("week52_pos",0.0),
                         ("per",0.0),("roe",0.0),("equity_ratio",0.0),("is_quality",False)]:
        if col not in df.columns:
            df[col] = default
        df[col] = df[col].fillna(default)
    df["buy_score"] = df["buy_score"].astype(int)
    df.sort_values("dist_to_2s", ascending=True, inplace=True)
    df.reset_index(drop=True, inplace=True)

    # ===== 日本株ページ (index.html) =====
    jp_config = {
        "title": "日本株",
        "markets": ["Nikkei225"],
        "tabs": [("n225", "日経225", "Nikkei225")],
        "nav_active": "jp",
    }
    jp_html = generate_html(df, today, jp_config)
    jp_path = os.path.join(PAGES_DIR, "index.html")
    with open(jp_path, "w", encoding="utf-8") as f:
        f.write(jp_html)
    print(f"  Saved: {jp_path}")

    # ===== 米国株ページ (us.html) =====
    us_config = {
        "title": "米国株",
        "markets": ["Dow30", "NASDAQ100", "配当貴族", "SP500高配当"],
        "tabs": [],
        "nav_active": "us",
    }
    us_html = generate_html(df, today, us_config)
    us_path = os.path.join(PAGES_DIR, "us.html")
    with open(us_path, "w", encoding="utf-8") as f:
        f.write(us_html)
    print(f"  Saved: {us_path}")

    with open(os.path.join(PAGES_DIR, ".nojekyll"), "w"):
        pass
    print("  Web dashboard generated (2 pages).")


if __name__ == "__main__":
    main()
