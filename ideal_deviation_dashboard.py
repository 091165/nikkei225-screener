#!/usr/bin/env python3
"""
理想乖離ダッシュボード (Ideal Deviation Dashboard)
====================================================
長期投資の買い場を一目で判断するためのツール。
主要株価指数の25日移動平均線乖離率を算出し、
統計的な -2σ / -3σ の買いゾーンを可視化する。

対象指数:
  - 日経225 (^N225)
  - TOPIX (1306.T ETF代替)
  - ダウ平均 (^DJI)
  - S&P500 (^GSPC)
  - ナスダック (^IXIC)
  - SOX半導体 (^SOX)

GitHub Actions対応: cron実行 → PNG出力 → 自動コミット可能
"""

import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch
from datetime import datetime, timedelta
import warnings
import os

warnings.filterwarnings("ignore")

# ─── 日本語フォント設定 ─────────────────────────
plt.rcParams["font.family"] = "DejaVu Sans"
# GitHub Actions等で日本語フォントがない環境用にフォールバック
try:
    import matplotlib.font_manager as fm
    jp_fonts = [f.name for f in fm.fontManager.ttflist
                if "Gothic" in f.name or "Noto" in f.name or "IPAex" in f.name]
    if jp_fonts:
        plt.rcParams["font.family"] = jp_fonts[0]
except:
    pass

# ─── 設定 ─────────────────────────────────────
INDICES = {
    "Nikkei 225":   {"ticker": "^N225",  "label_jp": "日経225"},
    "Dow Jones":    {"ticker": "^DJI",   "label_jp": "ダウ平均"},
    "S&P 500":      {"ticker": "^GSPC",  "label_jp": "S&P500"},
    "NASDAQ":       {"ticker": "^IXIC",  "label_jp": "ナスダック"},
    "SOX":          {"ticker": "^SOX",   "label_jp": "SOX半導体"},
}

SMA_PERIOD = 25       # 移動平均の日数
LOOKBACK_YEARS = 20   # 統計に使う年数（最大取得可能期間）
CHART_YEARS = 2       # チャート表示期間（直近N年）


# ─── データ取得 & 計算 ────────────────────────────
def fetch_and_calc(ticker_symbol: str, name: str) -> dict | None:
    """指定ティッカーの株価を取得し、乖離率と理想乖離を算出"""
    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period="max")
        if df.empty or len(df) < SMA_PERIOD + 50:
            print(f"  [WARN] {name}: データ不足 ({len(df)} rows)")
            return None

        df = df[["Close"]].copy()
        df.columns = ["close"]
        df["sma25"] = df["close"].rolling(window=SMA_PERIOD).mean()
        df["deviation"] = ((df["close"] - df["sma25"]) / df["sma25"]) * 100
        df = df.dropna()

        # 統計量
        dev = df["deviation"]
        mean_dev = dev.mean()
        std_dev  = dev.std()

        sigma2_upper = mean_dev + 2 * std_dev
        sigma2_lower = mean_dev - 2 * std_dev
        sigma3_upper = mean_dev + 3 * std_dev
        sigma3_lower = mean_dev - 3 * std_dev

        latest = df.iloc[-1]
        current_dev = latest["deviation"]
        current_price = latest["close"]
        current_sma = latest["sma25"]

        # 買いゾーン価格ライン
        price_2sigma = current_sma * (1 + sigma2_lower / 100)
        price_3sigma = current_sma * (1 + sigma3_lower / 100)

        return {
            "name": name,
            "ticker": ticker_symbol,
            "df": df,
            "current_price": current_price,
            "current_sma": current_sma,
            "current_dev": current_dev,
            "mean_dev": mean_dev,
            "std_dev": std_dev,
            "sigma2_upper": sigma2_upper,
            "sigma2_lower": sigma2_lower,
            "sigma3_upper": sigma3_upper,
            "sigma3_lower": sigma3_lower,
            "price_2sigma": price_2sigma,
            "price_3sigma": price_3sigma,
            "total_days": len(dev),
            "start_date": df.index[0].strftime("%Y-%m-%d"),
        }
    except Exception as e:
        print(f"  [ERROR] {name}: {e}")
        return None


def get_zone_label(current_dev, s2l, s3l, s2u, s3u):
    """現在の乖離率がどのゾーンにいるかを判定"""
    if current_dev <= s3l:
        return "STRONG BUY (-3σ)", "#d32f2f", "white"
    elif current_dev <= s2l:
        return "BUY ZONE (-2σ)", "#f57c00", "white"
    elif current_dev <= 0:
        return "MILD DIP", "#fbc02d", "black"
    elif current_dev <= s2u:
        return "NEUTRAL / UP", "#66bb6a", "white"
    elif current_dev <= s3u:
        return "OVERBOUGHT (+2σ)", "#7e57c2", "white"
    else:
        return "EXTREME HIGH (+3σ)", "#880e4f", "white"


# ─── サマリーカード描画 ──────────────────────────
def draw_summary_card(ax, data, idx, total):
    """1指数ぶんのサマリーカードを描画"""
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")

    zone_label, zone_color, zone_text_color = get_zone_label(
        data["current_dev"],
        data["sigma2_lower"], data["sigma3_lower"],
        data["sigma2_upper"], data["sigma3_upper"],
    )

    # 背景
    bg = FancyBboxPatch((0.2, 0.2), 9.6, 9.6,
                         boxstyle="round,pad=0.3",
                         facecolor="#f5f5f5", edgecolor="#bdbdbd", linewidth=1.5)
    ax.add_patch(bg)

    # ゾーンバッジ
    badge = FancyBboxPatch((0.5, 7.8), 9.0, 1.6,
                            boxstyle="round,pad=0.2",
                            facecolor=zone_color, edgecolor="none")
    ax.add_patch(badge)
    ax.text(5.0, 8.6, zone_label, ha="center", va="center",
            fontsize=13, fontweight="bold", color=zone_text_color)

    # 指数名
    label_jp = INDICES.get(data["name"], {}).get("label_jp", data["name"])
    ax.text(5.0, 7.1, f"{label_jp}  ({data['ticker']})",
            ha="center", va="center", fontsize=12, fontweight="bold", color="#212121")

    # 現在値
    ax.text(1.0, 5.8, "Price:", fontsize=9, color="#757575")
    ax.text(6.0, 5.8, f"{data['current_price']:,.1f}", fontsize=11, fontweight="bold", color="#212121")

    # 現在乖離率
    dev_color = "#d32f2f" if data["current_dev"] < 0 else "#2e7d32"
    ax.text(1.0, 4.8, "Deviation:", fontsize=9, color="#757575")
    ax.text(6.0, 4.8, f"{data['current_dev']:+.2f}%", fontsize=11, fontweight="bold", color=dev_color)

    # -2σ / -3σ ライン
    ax.text(1.0, 3.6, "-2σ (95%):", fontsize=9, color="#757575")
    ax.text(6.0, 3.6, f"{data['sigma2_lower']:+.2f}%  →  {data['price_2sigma']:,.0f}",
            fontsize=9, color="#e65100")

    ax.text(1.0, 2.7, "-3σ (99%):", fontsize=9, color="#757575")
    ax.text(6.0, 2.7, f"{data['sigma3_lower']:+.2f}%  →  {data['price_3sigma']:,.0f}",
            fontsize=9, color="#b71c1c")

    # 距離
    dist_2s = ((data["current_price"] / data["price_2sigma"]) - 1) * 100
    ax.text(1.0, 1.6, "Dist to -2σ:", fontsize=9, color="#757575")
    ax.text(6.0, 1.6, f"{dist_2s:+.1f}%", fontsize=10, fontweight="bold",
            color="#d32f2f" if dist_2s < 3 else "#757575")

    # 統計期間
    ax.text(1.0, 0.6, f"Stats: {data['total_days']:,}d from {data['start_date']}",
            fontsize=7, color="#9e9e9e")


# ─── 乖離率チャート描画 ──────────────────────────
def draw_deviation_chart(ax, data):
    """直近N年の乖離率チャートにσバンドを重ねて描画"""
    df = data["df"].copy()
    cutoff = pd.Timestamp.now(tz=df.index.tz) - timedelta(days=CHART_YEARS * 365)
    df = df[df.index >= cutoff]

    if df.empty:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
        return

    dates = df.index
    dev = df["deviation"]

    # 乖離率の線
    ax.plot(dates, dev, color="#1565c0", linewidth=0.8, alpha=0.9)
    ax.fill_between(dates, 0, dev, where=(dev < 0), color="#ef9a9a", alpha=0.3)
    ax.fill_between(dates, 0, dev, where=(dev >= 0), color="#a5d6a7", alpha=0.3)

    # σバンド (水平線)
    ax.axhline(y=data["sigma2_lower"], color="#e65100", linestyle="--", linewidth=1.0, alpha=0.8,
               label=f'-2σ ({data["sigma2_lower"]:+.1f}%)')
    ax.axhline(y=data["sigma3_lower"], color="#b71c1c", linestyle="--", linewidth=1.0, alpha=0.8,
               label=f'-3σ ({data["sigma3_lower"]:+.1f}%)')
    ax.axhline(y=data["sigma2_upper"], color="#2e7d32", linestyle=":", linewidth=0.7, alpha=0.5,
               label=f'+2σ ({data["sigma2_upper"]:+.1f}%)')
    ax.axhline(y=0, color="#616161", linestyle="-", linewidth=0.5, alpha=0.4)

    # -2σ/-3σ突入ポイントをマーク
    buy_2s = dev[dev <= data["sigma2_lower"]]
    buy_3s = dev[dev <= data["sigma3_lower"]]
    if not buy_2s.empty:
        ax.scatter(buy_2s.index, buy_2s.values, color="#f57c00", s=8, zorder=5, alpha=0.6)
    if not buy_3s.empty:
        ax.scatter(buy_3s.index, buy_3s.values, color="#d32f2f", s=12, zorder=6, alpha=0.8)

    label_jp = INDICES.get(data["name"], {}).get("label_jp", data["name"])
    ax.set_title(f'{label_jp} - 25d MA Deviation (past {CHART_YEARS}yr)',
                 fontsize=10, fontweight="bold", pad=8)
    ax.set_ylabel("Deviation %", fontsize=8)
    ax.legend(fontsize=7, loc="lower left", framealpha=0.7)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax.tick_params(axis="both", labelsize=7)
    ax.grid(True, alpha=0.2)
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")


# ─── ヒストグラム描画 ────────────────────────────
def draw_histogram(ax, data):
    """乖離率の分布ヒストグラム"""
    dev = data["df"]["deviation"]
    ax.hist(dev, bins=80, color="#90caf9", edgecolor="#42a5f5", alpha=0.7, density=True)

    # σライン
    ax.axvline(x=data["sigma2_lower"], color="#e65100", linestyle="--", linewidth=1.2)
    ax.axvline(x=data["sigma3_lower"], color="#b71c1c", linestyle="--", linewidth=1.2)
    ax.axvline(x=data["current_dev"], color="#000000", linestyle="-", linewidth=2.0, alpha=0.8)

    ax.set_title("Distribution", fontsize=9, fontweight="bold")
    ax.tick_params(axis="both", labelsize=7)
    ax.set_xlabel("Deviation %", fontsize=7)


# ─── メイン ──────────────────────────────────
def main():
    print("=" * 60)
    print("  Ideal Deviation Dashboard")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    results = []
    for name, info in INDICES.items():
        print(f"\n  Fetching {name} ({info['ticker']})...")
        data = fetch_and_calc(info["ticker"], name)
        if data:
            results.append(data)
            zone_label, _, _ = get_zone_label(
                data["current_dev"],
                data["sigma2_lower"], data["sigma3_lower"],
                data["sigma2_upper"], data["sigma3_upper"],
            )
            print(f"    Price: {data['current_price']:,.1f}  "
                  f"Dev: {data['current_dev']:+.2f}%  "
                  f"-2σ: {data['sigma2_lower']:+.2f}%  "
                  f"-3σ: {data['sigma3_lower']:+.2f}%  "
                  f"→ {zone_label}")

    if not results:
        print("\n  [ERROR] No data retrieved. Exiting.")
        return

    n = len(results)

    # ========== Page 1: サマリーカード ==========
    fig1, axes1 = plt.subplots(1, n, figsize=(4.2 * n, 5.5))
    if n == 1:
        axes1 = [axes1]
    fig1.suptitle(
        f"Ideal Deviation Dashboard — {datetime.now().strftime('%Y-%m-%d')}",
        fontsize=14, fontweight="bold", y=0.98
    )
    for i, data in enumerate(results):
        draw_summary_card(axes1[i], data, i, n)
    fig1.tight_layout(rect=[0, 0, 1, 0.94])

    out_dir = os.environ.get("OUTPUT_DIR", ".")
    path1 = os.path.join(out_dir, "dashboard_summary.png")
    fig1.savefig(path1, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"\n  Saved: {path1}")

    # ========== Page 2: 乖離率チャート ==========
    fig2, axes2 = plt.subplots(n, 1, figsize=(14, 3.2 * n))
    if n == 1:
        axes2 = [axes2]
    fig2.suptitle(
        f"25-day MA Deviation Charts (past {CHART_YEARS} years)",
        fontsize=13, fontweight="bold", y=1.0
    )
    for i, data in enumerate(results):
        draw_deviation_chart(axes2[i], data)
    fig2.tight_layout(rect=[0, 0, 1, 0.97])

    path2 = os.path.join(out_dir, "dashboard_charts.png")
    fig2.savefig(path2, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"  Saved: {path2}")

    # ========== Page 3: ヒストグラム ==========
    cols = min(n, 3)
    rows = (n + cols - 1) // cols
    fig3, axes3 = plt.subplots(rows, cols, figsize=(5 * cols, 3.5 * rows))
    if n == 1:
        axes3 = np.array([axes3])
    axes3_flat = axes3.flatten() if hasattr(axes3, "flatten") else [axes3]
    for i, data in enumerate(results):
        draw_histogram(axes3_flat[i], data)
    for j in range(i + 1, len(axes3_flat)):
        axes3_flat[j].axis("off")
    fig3.suptitle("Deviation Distribution (full history)", fontsize=12, fontweight="bold")
    fig3.tight_layout(rect=[0, 0, 1, 0.95])

    path3 = os.path.join(out_dir, "dashboard_histograms.png")
    fig3.savefig(path3, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"  Saved: {path3}")

    # ========== CSV出力（GitHub Actions向け） ==========
    rows_csv = []
    for d in results:
        dist_2s = ((d["current_price"] / d["price_2sigma"]) - 1) * 100
        zone_label, _, _ = get_zone_label(
            d["current_dev"], d["sigma2_lower"], d["sigma3_lower"],
            d["sigma2_upper"], d["sigma3_upper"]
        )
        rows_csv.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "index": d["name"],
            "ticker": d["ticker"],
            "price": round(d["current_price"], 2),
            "sma25": round(d["current_sma"], 2),
            "deviation_pct": round(d["current_dev"], 3),
            "sigma2_lower": round(d["sigma2_lower"], 3),
            "sigma3_lower": round(d["sigma3_lower"], 3),
            "price_at_2sigma": round(d["price_2sigma"], 1),
            "price_at_3sigma": round(d["price_3sigma"], 1),
            "dist_to_2sigma_pct": round(dist_2s, 2),
            "zone": zone_label,
            "stat_days": d["total_days"],
        })
    df_csv = pd.DataFrame(rows_csv)
    csv_path = os.path.join(out_dir, "deviation_snapshot.csv")
    df_csv.to_csv(csv_path, index=False)
    print(f"  Saved: {csv_path}")

    # ========== コンソールサマリー ==========
    print("\n" + "=" * 80)
    print("  SUMMARY")
    print("=" * 80)
    print(f"  {'Index':<14} {'Price':>10} {'Dev%':>8} {'−2σ%':>8} {'−3σ%':>8} "
          f"{'Buy@−2σ':>10} {'Dist':>7}  Zone")
    print("  " + "-" * 78)
    for d in results:
        dist_2s = ((d["current_price"] / d["price_2sigma"]) - 1) * 100
        zone_label, _, _ = get_zone_label(
            d["current_dev"], d["sigma2_lower"], d["sigma3_lower"],
            d["sigma2_upper"], d["sigma3_upper"]
        )
        print(f"  {d['name']:<14} {d['current_price']:>10,.1f} {d['current_dev']:>+8.2f} "
              f"{d['sigma2_lower']:>+8.2f} {d['sigma3_lower']:>+8.2f} "
              f"{d['price_2sigma']:>10,.0f} {dist_2s:>+6.1f}%  {zone_label}")
    print("=" * 80)
    print("  Done.\n")

    plt.close("all")
    return results


if __name__ == "__main__":
    main()
