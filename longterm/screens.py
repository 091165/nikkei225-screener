"""追加の判定（地合い・配当格付け・下げ止まり・水平サポート・月足の形など）。すべて純粋関数。

しきい値は config/longterm.yaml の screens セクション。
"""

import math

import numpy as np
import pandas as pd


# ─── 地合い ───────────────────────────────────────
def breadth_stage(below_pct: float | None, cfg: dict) -> dict:
    """下方乖離（25日線より下）の銘柄割合から段階を返す。"""
    s = cfg["screens"]["breadth"]
    if below_pct is None:
        return {"code": None, "label": "—"}
    if below_pct <= s["rest_max"]:
        return {"code": "rest", "label": "逆張りの買いは休み（持っている銘柄の含み益を育てる時期）"}
    if below_pct <= s["partial_max"]:
        return {"code": "partial", "label": "買ってもよい段階（分けて買う）"}
    return {"code": "full", "label": "全体の買い場"}


def index_stage(dev: float, cfg: dict) -> dict:
    """日経平均の25日乖離率の段階。"""
    s = cfg["screens"]["index_stage"]
    if dev <= s["historic"]:
        return {"code": "historic", "label": "歴史的な買い場"}
    if dev <= s["deep"]:
        return {"code": "deep", "label": "深い買い場"}
    if dev <= s["buy"]:
        return {"code": "buy", "label": "買い場（相応の資金）"}
    if dev < 0:
        return {"code": "dip", "label": "軽い押し目（様子見）"}
    return {"code": "none", "label": "買い時ではない"}


def month_tendency(month: int, cfg: dict) -> str:
    s = cfg["screens"]["month_tendency"]
    if month in s["weak"]:
        return "弱い月（過去の傾向）"
    if month in s["strong"]:
        return "強い月（過去の傾向）"
    return "中立の月"


def advance_decline_ratio(closes: dict, days: int = 25, history: int = 260) -> pd.DataFrame:
    """銘柄ごとの終値 Series から、日ごとの値上がり数・値下がり数と25日騰落レシオ（%）を計算する。"""
    if not closes:
        return pd.DataFrame(columns=["adv", "dec", "ratio"])
    df = pd.DataFrame(closes).sort_index()
    df = df.iloc[-(history + days + 1):]
    chg = df.diff().iloc[1:]
    adv = (chg > 0).sum(axis=1)
    dec = (chg < 0).sum(axis=1)
    ratio = adv.rolling(days).sum() / dec.rolling(days).sum().replace(0, np.nan) * 100
    return pd.DataFrame({"adv": adv, "dec": dec, "ratio": ratio}).dropna()


# ─── 下げ止まり・水平サポート・節目 ──────────────────────
def bottoming_signal(df: pd.DataFrame, cfg: dict) -> dict:
    """直近20日の最安値を直近5日に更新していない かつ（下辺が水平 または 下ヒゲ）。"""
    s = cfg["screens"]["bottoming"]
    lookback, recent = s["lookback_days"], s["recent_days"]
    d = df.dropna(subset=["low", "close"]).iloc[-lookback:]
    if len(d) < lookback:
        return {"pass": None}
    low20 = float(d["low"].min())
    pos = int(np.argmin(d["low"].values))
    not_updated = pos < len(d) - recent
    # 下辺が水平: 安値の谷（前後1日より低い）が最安値 +3% 以内に2回以上
    lows = d["low"].values
    troughs = [i for i in range(len(lows)) if (i == 0 or lows[i] <= lows[i - 1]) and (i == len(lows) - 1 or lows[i] <= lows[i + 1])]
    near = [i for i in troughs if lows[i] <= low20 * (1 + s["flat_tolerance"])]
    flat = len(near) >= 2 and (near[-1] - near[0]) >= 3
    # 下ヒゲ: 直近5日に、下ヒゲが実体の2倍以上
    wick = False
    if "open" in d.columns:
        for _, r in d.iloc[-recent:].iterrows():
            body = abs(r["close"] - r["open"])
            lower = min(r["open"], r["close"]) - r["low"]
            if lower > 0 and lower >= s["wick_ratio"] * max(body, r["close"] * 0.001):
                wick = True
    return {"pass": bool(not_updated and (flat or wick)), "no_new_low": bool(not_updated),
            "flat_bottom": bool(flat), "lower_wick": bool(wick), "low_20d": low20}


def support_levels(df: pd.DataFrame, cfg: dict) -> dict:
    """直近2年の安値の谷（前後10営業日で最安）を集め、±2%以内に2回以上あるものを水平サポートとする。"""
    s = cfg["screens"]["support"]
    d = df.dropna(subset=["low"]).loc[df.index[-1] - pd.DateOffset(years=s["years"]):]
    lows = d["low"].values
    w = s["window"]
    # 谷 = 前後 w 営業日の中で最安（同じ安値が続く場合は最初の日だけ）
    troughs = [lows[i] for i in range(w, len(lows) - w) if int(np.argmin(lows[i - w:i + w + 1])) == w]
    price = float(df["close"].iloc[-1])
    levels = []
    for t in sorted(troughs):
        group = [x for x in troughs if abs(x / t - 1) <= s["tolerance"]]
        if len(group) >= 2:
            lv = float(np.mean(group))
            if not any(abs(lv / x - 1) <= s["tolerance"] for x, _ in levels):
                levels.append((lv, len(group)))
    below = [(lv, n) for lv, n in levels if lv < price]
    nearest = max(below, key=lambda x: x[0]) if below else None
    return {"levels": [{"price": lv, "touches": n} for lv, n in levels],
            "nearest": nearest[0] if nearest else None,
            "nearest_touches": nearest[1] if nearest else None,
            "nearest_pct": (nearest[0] / price - 1) * 100 if nearest else None}


def round_level(price: float, jp: bool = True) -> float:
    """今の株価より下で一番近いキリのいい節目。"""
    if jp:
        unit = 100 if price < 1000 else (1000 if price < 10000 else 5000)
    else:
        unit = 10 if price < 100 else (100 if price < 1000 else 500)
    lv = math.floor(price / unit) * unit
    if lv >= price:
        lv -= unit
    return float(lv)


# ─── 配当格付け ─────────────────────────────────────
def retained_earnings_trend(values: list[float]) -> str:
    """直近4期で、前期より減った期が1回以下 かつ 最新が最古より多ければ up。"""
    v = [x for x in values if x is not None and not (isinstance(x, float) and math.isnan(x))][-4:]
    if len(v) < 2:
        return "unknown"
    decreases = sum(1 for a, b in zip(v, v[1:]) if b < a)
    if decreases <= 1 and v[-1] > v[0]:
        return "up"
    if v[-1] < v[0] and decreases >= 2:
        return "down"
    return "flat"


def payout_history(dividends_paid: pd.Series, net_income: pd.Series) -> list[float]:
    """年次の配当支払額（キャッシュフロー計算書、マイナス値）÷ 純利益 × 100。"""
    common = dividends_paid.index.intersection(net_income.index)
    out = []
    for d in sorted(common):
        ni = net_income[d]
        if ni and ni > 0:
            out.append(abs(float(dividends_paid[d])) / float(ni) * 100)
        else:
            out.append(float("inf") if dividends_paid[d] else 0.0)
    return out


def payout_jump(history: list[float], cfg: dict) -> bool:
    """配当性向が前期比で大きく上がった、または100%超（配当が利益に対して重い）。"""
    s = cfg["screens"]["dividend_grade"]
    if not history:
        return False
    last = history[-1]
    if last > s["payout_max"]:
        return True
    return len(history) >= 2 and last - history[-2] >= s["payout_jump_pt"]


def dividend_grade(div_yield, market_cap, consec_years, re_trend: str, jump: bool, cfg: dict,
                   dividend_growth_ok: bool = False) -> dict:
    """A: 利回り4%以上・時価総額1000億円以上・連続増配3年以上・利益剰余金up。B: 1つ欠け。C: 2つ以上欠け。—: 利回り2%以下。"""
    s = cfg["screens"]["dividend_grade"]
    y = div_yield or 0.0
    if y <= s["vt_yield"]:
        return {"grade": "—", "missing": [], "below_vt": True, "payout_jump": jump}
    checks = {
        f"利回り{s['min_yield']:g}%以上": y >= s["min_yield"],
        "時価総額1000億円以上": (market_cap or 0) >= s["min_market_cap"],
        f"連続増配{s['min_consec_years']}年以上": (consec_years or 0) >= s["min_consec_years"] or dividend_growth_ok,
        "利益剰余金が増加": re_trend == "up",
    }
    missing = [k for k, ok in checks.items() if not ok]
    grade = "A" if not missing else ("B" if len(missing) == 1 else "C")
    return {"grade": grade, "missing": missing, "below_vt": False, "payout_jump": jump}


# ─── 月足の形 ───────────────────────────────────────
def monthly_patterns(monthly: pd.DataFrame, daily_dev: float | None, upper2: float | None, cfg: dict) -> dict:
    """確定した月足（open/close）から: 陰線が続いたあとの初陽線、連続陽線の本数、月足GC中の日足過熱。"""
    s = cfg["screens"]["monthly"]
    if len(monthly) < 4:
        return {"bear_streak_then_up": None, "bull_streak": 0, "gc_overheated": None}
    up = (monthly["close"] > monthly["open"]).values
    bull = 0
    for u in up[::-1]:
        if u:
            bull += 1
        else:
            break
    bear_before = 0
    if up[-1]:
        for u in up[-2::-1]:
            if not u:
                bear_before += 1
            else:
                break
    gc_over = None
    c = monthly["close"]
    if len(c) >= s["gc_long"] and daily_dev is not None and upper2 is not None:
        gc_on = c.rolling(s["gc_short"]).mean().iloc[-1] > c.rolling(s["gc_long"]).mean().iloc[-1]
        gc_over = bool(gc_on and daily_dev >= upper2)
    return {"bear_streak_then_up": bool(up[-1] and bear_before >= s["bear_streak_min"]),
            "bear_streak_before": bear_before, "bull_streak": bull, "gc_overheated": gc_over}


def completed_monthly_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """確定した月足（評価日が月の途中なら当月を除く）。"""
    m = df.resample("ME").agg({"open": "first", "close": "last"}).dropna()
    last = df.index[-1]
    if len(m) and last.normalize() < (last + pd.offsets.BMonthEnd(0)).normalize():
        m = m.iloc[:-1]
    return m


# ─── 暴落点での強さ（P2-2）───────────────────────────
def crash_strength(points: list, low: pd.Series, close: pd.Series, months: int = 6) -> dict:
    """直近の指数要因の暴落点で、その直前 months か月の安値を割ったか。現在値は暴落点の安値から何%上か。"""
    idx_pts = [p for p in points if getattr(p, "kind", None) in ("index", "both") and p.low is not None]
    if not idx_pts:
        return {"date": None}
    p = idx_pts[-1]
    d = pd.Timestamp(p.date)
    prior = low.loc[d - pd.DateOffset(months=months):d - pd.Timedelta(days=1)].dropna()
    if prior.empty or low.index[0] > d - pd.DateOffset(months=months):
        return {"date": p.date, "held": None}
    held = bool(p.low >= float(prior.min()))
    return {"date": p.date, "held": held, "crash_low": p.low, "prior_low": float(prior.min()),
            "above_crash_low_pct": (float(close.iloc[-1]) / p.low - 1) * 100}


# ─── 逆張り型 / 順張り押し目（X-1）──────────────────────
def perfect_order_days(close: pd.Series) -> dict:
    """日足 5日線 > 25日線 > 75日線 の状態と、最後にその状態だった日からの営業日数。"""
    m5, m25, m75 = (close.rolling(n).mean() for n in (5, 25, 75))
    po = (m5 > m25) & (m25 > m75)
    po = po[m75.notna()]
    if po.empty:
        return {"now": None, "days_since": None}
    if po.iloc[-1]:
        return {"now": True, "days_since": 0}
    hits = np.flatnonzero(po.values)
    return {"now": False, "days_since": int(len(po) - 1 - hits[-1]) if len(hits) else int(len(po))}


def dip_type(drop_from_ath: float, days_since_ath: int, days_since_po: int | None, div_yield: float | None,
             consec_years: int | None, at_lower2: bool) -> str:
    """逆張り型: 半値割れ・最高値から1年以上・PO が1年以上出ていない・配当と増配あり・-2σ到達。
    順張り押し目: 削れ率25%未満・最高値から1年未満・-2σ到達。"""
    if not at_lower2:
        return "—"
    if (drop_from_ath >= 50 and days_since_ath >= 250 and (days_since_po is None or days_since_po >= 250)
            and (div_yield or 0) > 0 and (consec_years or 0) >= 1):
        return "逆張り型"
    if drop_from_ath < 25 and days_since_ath < 250:
        return "順張り押し目"
    return "—"


# ─── 久々5日線上抜け（X-8）・上げ止まり（Y-3）──────────────
def short_rebound(close: pd.Series, dev25: float) -> dict:
    m5 = close.rolling(5).mean()
    m75 = close.rolling(75).mean()
    below = (close < m5).iloc[-21:-1]
    crossed = bool(close.iloc[-1] > m5.iloc[-1] and close.iloc[-2] <= m5.iloc[-2])
    rebound = bool(crossed and below.sum() >= 15 and dev25 < 0)
    near75 = bool(pd.notna(m75.iloc[-1]) and abs(close.iloc[-1] / m75.iloc[-1] - 1) <= 0.02)
    return {"pass": rebound, "near_75ma": near75}


def topping_signal(df: pd.DataFrame, dev: pd.Series, upper1: float) -> dict:
    """直近60日高値を直近20日に更新していない かつ 直近20日の値幅が60日の半分以下 かつ 期間中に +1σ 以上を経た。"""
    d = df.iloc[-60:]
    if len(d) < 60:
        return {"pass": None}
    h60 = d["high"].max()
    no_new_high = bool(d["high"].iloc[-20:].max() < h60)
    r20 = d["high"].iloc[-20:].max() - d["low"].iloc[-20:].min()
    r60 = h60 - d["low"].min()
    ranged = bool(r60 > 0 and r20 <= r60 / 2)
    was_hot = bool((dev.iloc[-60:] >= upper1).any())
    return {"pass": bool(no_new_high and ranged and was_hot)}


def trailing_stop_level(df: pd.DataFrame, window: int = 5) -> float | None:
    """直近60日の押し安値（谷）のうち最新のもの。逆指値の目安。"""
    lows = df["low"].iloc[-60:].values
    for i in range(len(lows) - window - 1, window - 1, -1):
        if int(np.argmin(lows[i - window:i + window + 1])) == window:
            return float(lows[i])
    return None


# ─── 黒字転換（Y-5）・10年で10倍（Y-6）───────────────────
def turnaround(net_incomes: list[float], drop_from_ath: float, close: pd.Series) -> bool:
    if len(net_incomes) < 3:
        return False
    recent_profit = net_incomes[-1] > 0 and all(x < 0 for x in net_incomes[-3:-1])
    m = close.resample("ME").last().dropna()
    long_down = len(m) > 25 and m.iloc[-1] < m.iloc[-25]
    return bool(recent_profit and drop_from_ath >= 50 and long_down)


def gain_over_years(close: pd.Series, years: int = 10) -> float | None:
    start = close.index[-1] - pd.DateOffset(years=years)
    if close.index[0] > start:
        return None
    pos = close.index.searchsorted(start)
    return float(close.iloc[-1] / close.iloc[pos])


# ─── 銘柄ごとの月別傾向（X-4）───────────────────────────
def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 100.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half) * 100, min(1.0, center + half) * 100)


def month_stats(close: pd.Series, month: int) -> dict:
    """確定した月足から、その月の平均騰落率・勝率・最大・最小・年数・勝率の95%区間。"""
    m = close.resample("ME").last().dropna()
    last = close.index[-1]
    if len(m) and last.normalize() < (last + pd.offsets.BMonthEnd(0)).normalize():
        m = m.iloc[:-1]
    r = (m.pct_change().dropna() * 100)
    r = r[r.index.month == month]
    if r.empty:
        return {"month": month, "n": 0}
    wins = int((r > 0).sum())
    lo, hi = wilson_interval(wins, len(r))
    return {"month": month, "n": int(len(r)), "avg": float(r.mean()), "win_rate": wins / len(r) * 100,
            "max": float(r.max()), "min": float(r.min()), "ci_low": lo, "ci_high": hi,
            "significant": bool(lo > 50 or hi < 50)}


# ─── 配当の推移（X-5）・利回りの過去レンジ（Y-4）・配当貴族（P3-1）────
def annual_dividends(dividends: pd.Series, years: int = 10) -> pd.Series:
    """配当履歴（日付→1株配当）から、確定した年ごとの合計（直近 years 年）。"""
    if dividends is None or dividends.empty:
        return pd.Series(dtype=float)
    a = dividends.groupby(dividends.index.year).sum()
    this_year = pd.Timestamp.now().year
    a = a[a.index < this_year]
    return a.iloc[-years:]


def dividend_growth_stats(annual: pd.Series) -> dict:
    if len(annual) < 2:
        return {"cagr_5y": None, "drawdown_years": None, "ok": False}
    drawdowns = int((annual.diff().dropna() < 0).sum())
    cagr = None
    if len(annual) >= 6 and annual.iloc[-6] > 0:
        cagr = ((annual.iloc[-1] / annual.iloc[-6]) ** (1 / 5) - 1) * 100
    return {"cagr_5y": cagr, "drawdown_years": drawdowns,
            "ok": bool(cagr is not None and cagr > 0 and drawdowns <= 1),
            "cut_last_year": bool(len(annual) >= 2 and annual.iloc[-1] < annual.iloc[-2])}


def yield_percentile(close: pd.Series, dividends: pd.Series, years: int = 20) -> dict:
    """日ごとの「直近1年配当 ÷ 株価」の過去 years 年の分布で、今の利回りの位置（%）。"""
    if dividends is None or dividends.empty or dividends.sum() == 0:
        return {"pct": None}
    # 配当日が休日でも落とさないよう、次の取引日に寄せてから合計する
    pos = close.index.searchsorted(dividends.index)
    keep = pos < len(close.index)
    aligned = pd.Series(dividends.values[keep], index=close.index[pos[keep]]).groupby(level=0).sum()
    ttm = aligned.reindex(close.index, fill_value=0.0).rolling("365D").sum()
    y = (ttm / close * 100).loc[close.index[-1] - pd.DateOffset(years=years):]
    y = y[y > 0]
    if len(y) < 250:
        return {"pct": None}
    now = float(y.iloc[-1])
    return {"pct": float((y <= now).mean() * 100), "now": now, "max": float(y.max()), "median": float(y.median())}


def aristocrat_signal(consec_years, div_yield, market_cap_usd, cut_last_year: bool, nasdaq_only: bool) -> str:
    """米国の配当貴族の買い場（連続増配40年以上・利回り7%以上・時価総額100億ドル以上）。"""
    if nasdaq_only:
        return "—"
    if cut_last_year:
        return "減配：ルール上は売り"
    if consec_years is None:
        return "—"
    y = div_yield or 0
    if consec_years >= 40 and y >= 7 and (market_cap_usd or 0) >= 1e10:
        return "配当貴族の買い場"
    if consec_years >= 25 and y >= 5:
        return "監視"
    return "—"


# ─── Z-1 業績の推移・DOE・ROA・PSR ─────────────────────
def series_trend(values: list[float]) -> str:
    """直近4期で、前期より減った期が1回以下 かつ 最新が最古以上なら up。最新が最古より下なら down。それ以外 flat。"""
    v = [x for x in values if x is not None and not (isinstance(x, float) and math.isnan(x))][-4:]
    if len(v) < 2:
        return "unknown"
    decreases = sum(1 for a, b in zip(v, v[1:]) if b < a)
    if decreases <= 1 and v[-1] >= v[0]:
        return "up"
    if v[-1] < v[0]:
        return "down"
    return "flat"


def pl_trend(revenue: list[float], ordinary: list[float], net_income: list[float]) -> dict:
    """売上・経常利益（なければ営業利益）・純利益の推移。3つとも up なら up、下降が1つなら mixed、2つ以上なら down。"""
    t = {"revenue": series_trend(revenue), "ordinary": series_trend(ordinary), "net_income": series_trend(net_income)}
    known = [x for x in t.values() if x != "unknown"]
    if not known:
        return {"trend": "unknown", **t}
    downs = sum(1 for x in known if x == "down")
    trend = "up" if downs == 0 else ("mixed" if downs == 1 else "down")
    return {"trend": trend, **t}


def doe_history(dividends_paid: list[float], equity: list[float]) -> list[float | None]:
    """DOE（配当総額 ÷ 株主資本、%）。株主資本がマイナス（債務超過扱い）の期は None。"""
    out = []
    for d, e in zip(dividends_paid, equity):
        out.append(abs(d) / e * 100 if e and e > 0 else None)
    return out


def doe_jumped(history: list[float | None], max_pt: float) -> bool:
    h = [x for x in history if x is not None]
    return len(h) >= 2 and h[-1] - h[-2] > max_pt


def ratio_pct(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den <= 0:
        return None
    return num / den * 100


# ─── Z-2 買い方の型 ───────────────────────────────────
DEFENSIVE_SECTORS = {"生活必需品", "公益", "通信", "Consumer Defensive", "Utilities", "Communication Services"}
GC_WAIT_SECTORS = {"ヘルスケア", "Healthcare"}


def monthly_trend(close: pd.Series, short: int = 9, long: int = 24) -> dict:
    """確定月足で、24か月前より高いか（右肩上がり）と、月足9MA > 24MA（GC中）か。GCが直近何か月前に起きたか。"""
    m = close.resample("ME").last().dropna()
    last = close.index[-1]
    if len(m) and last.normalize() < (last + pd.offsets.BMonthEnd(0)).normalize():
        m = m.iloc[:-1]
    if len(m) < long + 2:
        return {"up_24m": None, "gc_on": None, "months_since_gc": None}
    s, l = m.rolling(short).mean(), m.rolling(long).mean()
    on = (s > l).dropna()
    crosses = np.flatnonzero(on.values[1:] & ~on.values[:-1])
    months_since = int(len(on) - 1 - (crosses[-1] + 1)) if len(crosses) else None
    return {"up_24m": bool(m.iloc[-1] > m.iloc[-25]), "gc_on": bool(on.iloc[-1]), "months_since_gc": months_since}


def buy_style(sector: str | None, div_yield: float | None, mtrend: dict, equity_negative: bool) -> str:
    """乖離で拾ってよい / GC待ち / —（表示のみ）。"""
    down = mtrend.get("up_24m") is False
    if sector in GC_WAIT_SECTORS or (not (div_yield or 0) and down) or (down and equity_negative):
        return "GC待ち"
    if sector in DEFENSIVE_SECTORS or (mtrend.get("up_24m") and mtrend.get("gc_on")):
        return "乖離で拾ってよい"
    return "—"


# ─── Z-5 上方乖離の見分け ───────────────────────────────
def upside_type(dev: float, upper1: float, months_since_gc: int | None, drop_from_ath: float,
                pbr_percentile: float | None, div_yield: float | None, vt_yield: float) -> str:
    """上方乖離（+1σ以上）の銘柄だけ: 上げはじめ / 高値追い注意 / —。"""
    if dev < upper1:
        return "—"
    if months_since_gc is not None and months_since_gc <= 6 and drop_from_ath >= 30:
        return "上げはじめ"
    if pbr_percentile is not None and pbr_percentile >= 80 and (div_yield or 0) <= vt_yield:
        return "高値追い注意"
    return "—"


def recession_signals(t10y2y: float | None, payems_change: float | None) -> dict:
    """長短金利差の逆転（<0）と、雇用者数の前月比マイナス。両方なら警告。"""
    inv = t10y2y is not None and t10y2y < 0
    neg = payems_change is not None and payems_change < 0
    return {"inverted": inv, "jobs_negative": neg, "both": bool(inv and neg)}


def bond_yield_pct(tnx_close: float | None) -> float | None:
    """^TNX を利回り（%）に。古い表記（42.5 = 4.25%）なら10で割る。"""
    if tnx_close is None:
        return None
    return tnx_close / 10 if tnx_close > 20 else tnx_close
