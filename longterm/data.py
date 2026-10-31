"""株価データの取得（yfinance）。キャッシュ・差分取得は後の段階で追加する。"""

import pandas as pd


def fetch_history(ticker: str) -> pd.DataFrame:
    """全期間の日足（分割・配当調整後）を返す。列: open, high, low, close, volume。

    yfinance の history() は既定で auto_adjust=True（調整後株価）。
    """
    import yfinance as yf
    df = yf.Ticker(ticker).history(period="max", auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df.index = df.index.normalize()
    df = df[~df.index.duplicated(keep="last")]
    return df.dropna(subset=["close"])


def fetch_financials(ticker: str) -> dict:
    """通期・四半期の損益計算書、通期のキャッシュフロー・貸借対照表を返す（取れないものは None）。"""
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
    return out
