"""株価・業績データの取得（yfinance）とキャッシュ。

キャッシュ（環境変数 LONGTERM_CACHE、既定はなし）を指定すると:
- 株価: 全期間を一度取得して Parquet に保存し、以降は直近分だけ取得してつなぐ（差分取得）。
  つなぎ目の終値が合わない（株式分割・配当で調整後株価が変わった）場合は全期間を取り直す。
- 業績: 取得から FIN_TTL_DAYS 日以内ならキャッシュを使う。
キャッシュは GitHub Actions の cache に置き、リポジトリにはコミットしない（.gitignore: data/cache/）。
"""

import os
import pickle
import time

import pandas as pd

COLS = ["open", "high", "low", "close", "volume"]
FIN_TTL_DAYS = 7
OVERLAP_DAYS = 10          # 差分取得時に重ねて取る日数（つなぎ目の検証用）
ADJ_TOLERANCE = 0.005      # つなぎ目の終値のずれがこれを超えたら全期間を取り直す


def _cache_dir():
    d = os.environ.get("LONGTERM_CACHE")
    if d:
        os.makedirs(os.path.join(d, "prices"), exist_ok=True)
        os.makedirs(os.path.join(d, "financials"), exist_ok=True)
    return d


def _safe(ticker: str) -> str:
    return ticker.replace("^", "_").replace("/", "_")


def _normalize(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=COLS)
    df = df.rename(columns=str.lower)
    df = df[COLS + (["dividends"] if "dividends" in df.columns else [])]   # 配当履歴（米国株の配当の推移に使う）
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df.index = pd.DatetimeIndex(df.index).normalize()
    df = df[~df.index.duplicated(keep="last")]
    return df.dropna(subset=["close"])


def _download(ticker: str, start=None) -> pd.DataFrame:
    import yfinance as yf
    t = yf.Ticker(ticker)
    if start is None:
        return _normalize(t.history(period="max", auto_adjust=True))
    return _normalize(t.history(start=start, auto_adjust=True))


def merge_incremental(cached: pd.DataFrame, recent: pd.DataFrame, tolerance: float = ADJ_TOLERANCE) -> pd.DataFrame | None:
    """キャッシュに直近分をつなぐ。つなぎ目の終値が tolerance 以上ずれていたら None（全期間を取り直す合図）。"""
    if recent.empty:
        return cached
    if set(recent.columns) - set(cached.columns):
        return None          # 列が増えた（古いキャッシュ）→ 全期間を取り直す
    overlap = cached.index.intersection(recent.index)
    if len(overlap) == 0:
        return None
    diff = (recent.loc[overlap, "close"] / cached.loc[overlap, "close"] - 1).abs().max()
    if diff > tolerance:
        return None
    merged = pd.concat([cached[cached.index < recent.index[0]], recent])
    return merged[~merged.index.duplicated(keep="last")].sort_index()


def fetch_history(ticker: str, downloader=_download) -> pd.DataFrame:
    """全期間の日足（分割・配当調整後）。列: open, high, low, close, volume。"""
    cache = _cache_dir()
    if not cache:
        return downloader(ticker)
    path = os.path.join(cache, "prices", f"{_safe(ticker)}.parquet")
    if os.path.exists(path):
        try:
            cached = pd.read_parquet(path)
            start = (cached.index[-1] - pd.Timedelta(days=OVERLAP_DAYS)).strftime("%Y-%m-%d")
            merged = merge_incremental(cached, downloader(ticker, start))
            if merged is not None:
                merged.to_parquet(path)
                return merged
        except Exception:
            pass
    df = downloader(ticker)
    if not df.empty:
        df.to_parquet(path)
    return df


def fetch_financials(ticker: str) -> dict:
    """通期・四半期の損益計算書、通期のキャッシュフロー・貸借対照表を返す（取れないものは None）。"""
    cache = _cache_dir()
    path = os.path.join(cache, "financials", f"{_safe(ticker)}.pkl") if cache else None
    if path and os.path.exists(path) and time.time() - os.path.getmtime(path) < FIN_TTL_DAYS * 86400:
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    import yfinance as yf
    t = yf.Ticker(ticker)
    out = {}
    for key, attr in [("income", "income_stmt"), ("quarterly_income", "quarterly_income_stmt"),
                      ("cashflow", "cashflow"), ("balance", "balance_sheet")]:
        try:
            df = getattr(t, attr)
            out[key] = df if df is not None and not df.empty else None
        except Exception:
            out[key] = None
    if path and any(v is not None for v in out.values()):
        with open(path, "wb") as f:
            pickle.dump(out, f)
    return out
