"""
Strategy rules for the 3-strategy portfolio bot. PURE functions: market data in -> target weights out.
The live bot (portfolio_bot.py) and the historical replay (replay_backtest.py) both import this file, so the
backtest validates exactly the logic that trades.

All targets are "weights to hold AFTER the close of day t" (orders are market-on-close on day t).
Weights are fractions of total account equity.

  A1     short-term index mean reversion   SPY QQQ IWM DIA MDY      sleeve 1/3, 20% of sleeve per ETF
         enter at close t if close < SMA10 - 1.5*ATR10(Wilder); exit at the close of the first day whose close >
         the prior day's high, or at the close of the 10th day held (entry day = day 1). No same-day re-entry.
  D      asset-class momentum rotation     SPY EFA EEM TLT IEF GLD DBC VNQ   sleeve 1/3, 1/3 of sleeve per pick
         at the close of the last trading day of each month: top-3 by 126-trading-day return, only if > 0;
         held until the next month's last-day close.
  TSY    Treasury / IG month-end            TLT IEF LQD            sleeve 1/3, 1/3 of sleeve per ETF
         enter at the close of the 6th-to-last trading day of the month, exit at the close of the last trading day.
"""
import numpy as np
import pandas as pd

A1_UNIVERSE = ["SPY", "QQQ", "IWM", "DIA", "MDY"]
D_UNIVERSE = ["SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "VNQ"]
TSY_UNIVERSE = ["TLT", "IEF", "LQD"]
ALL_SYMBOLS = sorted(set(A1_UNIVERSE + D_UNIVERSE + TSY_UNIVERSE))

SLEEVE = {"A1": 1 / 3, "D": 1 / 3, "TSY": 1 / 3}
A1_SMA, A1_ATR, A1_K, A1_MAX_HOLD, A1_W = 10, 10, 1.5, 10, 0.20
D_LOOKBACK, D_TOPK = 126, 3
TSY_ENTRY_FROM_END = 6                 # 6th-to-last trading day (1 = last)


# ------------------------------------------------------------------ A1
def a1_signal(df):
    pc = df.close.shift(1)
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / A1_ATR, adjust=False, min_periods=A1_ATR).mean()
    sma = df.close.rolling(A1_SMA, min_periods=A1_SMA).mean()
    return (df.close < sma - A1_K * atr).fillna(False).values


def a1_positions(df):
    """bool array: in an A1 position after the close of each day (MOC entry/exit semantics)."""
    sig = a1_signal(df)
    c, h = df.close.values, df.high.values
    out = np.zeros(len(df), bool)
    pos, ei = False, -1
    for i in range(len(df)):
        if pos:
            k = i - ei + 1
            if c[i] > h[i - 1] or k >= A1_MAX_HOLD:
                pos = False          # exit at this close; no re-entry on the same close
                out[i] = False
                continue
            out[i] = True
        elif sig[i]:
            pos, ei = True, i
            out[i] = True
    return out


# ------------------------------------------------------------------ calendar helpers
def month_position_from_end(dates, calendar=None):
    """1 = last trading day of its month. `calendar` (sorted DatetimeIndex of trading days, may extend into the
    future) is used so the current, incomplete month is handled correctly in live trading."""
    cal = pd.DatetimeIndex(calendar) if calendar is not None else pd.DatetimeIndex(dates)
    s = pd.Series(1, index=cal)
    pos = s.groupby(cal.to_period("M")).cumcount(ascending=False) + 1
    return pos.reindex(pd.DatetimeIndex(dates)).values


# ------------------------------------------------------------------ D
def d_selection(closes, t, calendar=None):
    """The picks in force after the close of day t: made at the most recent month-end close <= t."""
    idx = closes.index[closes.index <= t]
    pfe = month_position_from_end(idx, calendar)
    me = idx[pfe == 1]
    if len(me) == 0:
        return []
    m = me[-1]
    hist = closes.loc[:m]
    if len(hist) <= D_LOOKBACK:
        return []
    mom = (hist.iloc[-1] / hist.iloc[-1 - D_LOOKBACK] - 1).dropna()
    if len(mom) < D_TOPK + 1:
        return []
    top = mom.nlargest(D_TOPK)
    return list(top[top > 0].index)


# ------------------------------------------------------------------ combined
def target_weights(data, t, calendar=None, a1_cache=None):
    """data: dict symbol -> DataFrame(open, high, low, close) indexed by date (day t may be a provisional bar).
    Returns (weights dict symbol->fraction of equity, detail dict for logging)."""
    w = {s: 0.0 for s in ALL_SYMBOLS}
    detail = {"A1": [], "D": [], "TSY": []}
    # A1
    for s in A1_UNIVERSE:
        df = data[s].loc[:t]
        if len(df) < 30 or df.index[-1] != t:
            continue
        pos = a1_cache[s][df.index.get_loc(t)] if a1_cache is not None else a1_positions(df)[-1]
        if pos:
            w[s] += SLEEVE["A1"] * A1_W
            detail["A1"].append(s)
    # TSY
    pfe = month_position_from_end([t], calendar if calendar is not None else data["TLT"].index)[0]
    if 2 <= pfe <= TSY_ENTRY_FROM_END:
        for s in TSY_UNIVERSE:
            w[s] += SLEEVE["TSY"] / len(TSY_UNIVERSE)
            detail["TSY"].append(s)
    # D
    closes = pd.DataFrame({s: data[s].close for s in D_UNIVERSE})
    picks = d_selection(closes, t, calendar if calendar is not None else closes.index)
    for s in picks:
        w[s] += SLEEVE["D"] / D_TOPK
        detail["D"].append(s)
    return w, detail
