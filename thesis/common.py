"""Shared pieces: config, LSEG session, and the one spread construction used everywhere.

Nothing here writes raw prices anywhere. Outputs are basket-level indices and ratios only.
"""
import json
import os
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "thesis" / "config.json").read_text())
DATA_DIR = ROOT / "docs" / "data"

LONG = [x["ric"] for x in CONFIG["long"]]
SHORT = [x["ric"] for x in CONFIG["short"]]
BENCH = CONFIG["benchmark"]["ric"]
ALL = LONG + SHORT + [BENCH]
TICKER = {x["ric"]: x["ticker"] for x in CONFIG["long"] + CONFIG["short"] + [CONFIG["benchmark"]]}


def load_env_file():
    """Local runs: read LSEG creds from the file named in LSEG_ENV_FILE (never committed)."""
    path = os.environ.get("LSEG_ENV_FILE")
    if path and Path(path).exists():
        for line in Path(path).read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                os.environ.setdefault(key, value)


def open_lseg():
    """Platform session (GitHub Actions) first; Workspace desktop session as the local fallback.

    Returns the lseg.data module with a default session, or None if neither opens.
    """
    load_env_file()
    import lseg.data as ld

    key = os.environ.get("LSEG_APP_KEY")
    user = os.environ.get("LSEG_USERNAME") or os.environ.get("LSEG_USER")
    pwd = os.environ.get("LSEG_PASSWORD") or os.environ.get("LSEG_PASS")
    if key and user and pwd:
        try:
            from lseg.data.session import platform
            s = platform.Definition(app_key=key, grant=platform.GrantPassword(username=user, password=pwd)).get_session()
            s.open()
            if str(s.open_state).endswith("Opened"):
                ld.session.set_default(s)
                return ld
        except Exception as e:  # session quota, network, bad creds
            print(f"platform session failed: {type(e).__name__}")
    if key:
        try:
            ld.open_session(name="desktop.workspace", app_key=key)
            ld.get_history(BENCH, fields=["TRDPRC_1"], count=1)  # probe
            return ld
        except Exception as e:
            print(f"desktop session failed: {type(e).__name__}")
    return None


def lseg_closes(ld, start, end=None):
    # get_history silently returns only 20 rows when `end` is omitted, so always pass it.
    end = end or (pd.Timestamp.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    frames = []
    for ric in ALL:
        df = ld.get_history(ric, fields=["TRDPRC_1"], interval="daily", start=start, end=end)
        df.columns = [ric]
        frames.append(df.astype(float))
    px = pd.concat(frames, axis=1)
    px.index = pd.to_datetime(px.index).normalize()
    return px.sort_index()


def yahoo_closes(start):
    """Public fallback: Yahoo chart API, unadjusted daily closes (same basis as LSEG TRDPRC_1).

    Needs no credentials, so the tracker keeps running when LSEG access is unavailable.
    Returns an empty frame if any leg can't be fetched, so a partial basket is never used.
    """
    import urllib.request
    p1 = int(pd.Timestamp(start, tz="UTC").timestamp())
    p2 = int(pd.Timestamp.now(tz="UTC").timestamp()) + 86400
    cols = {}
    for ric in ALL:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{TICKER[ric]}"
               f"?period1={p1}&period2={p2}&interval=1d")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            res = json.loads(urllib.request.urlopen(req, timeout=30).read())["chart"]["result"][0]
            days = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert("America/New_York").normalize().tz_localize(None)
            s = pd.Series(res["indicators"]["quote"][0]["close"], index=days, dtype=float)
        except Exception as e:
            print(f"yahoo {TICKER[ric]} failed: {type(e).__name__}")
            return pd.DataFrame()
        cols[ric] = s[~s.index.duplicated(keep="last")]
    return pd.DataFrame(cols).sort_index()


REBALANCE = CONFIG.get("rebalance", {"frequency": "monthly", "drift_band": 0.30})


def rebalance_days(index, freq):
    """True on the first session of each period. The position for that session is set at the prior close."""
    idx = pd.DatetimeIndex(index)
    key = {"daily": idx, "weekly": idx.to_period("W"), "monthly": idx.to_period("M"),
           "quarterly": idx.to_period("Q")}[freq]
    first = ~pd.Index(key).duplicated()
    first[0] = True
    return first


def simulate(px, h, longs=None, shorts=None, freq=None, band=None, borrow=0.0, exec_bps=0.0):
    """Holdings-level book: $1 of equity long, $h short, equal weight within each side.

    Rebalanced (weights and hedge reset to target) on the first session of each `freq` period, and
    also whenever the short side has drifted more than `band` from its target size. Between
    rebalances the positions drift with prices. Session t's position is set at the close of t-1
    using h[t], which only uses data through t-1. The first session opens the book.

    Costs: `exec_bps` on every dollar traded; `borrow` is an annual fee on the short notional,
    charged daily (/252). Returns per-session: ret (net of the given costs), cost, turnover,
    and the long and short legs' own returns at their drifted weights.
    """
    longs, shorts = longs or LONG, shorts or SHORT
    freq = freq or REBALANCE["frequency"]
    band = REBALANCE["drift_band"] if band is None else band
    r = px.pct_change(fill_method=None).reindex(h.index).fillna(0.0)
    rL, rS, hv = r[longs].to_numpy(), r[shorts].to_numpy(), h.to_numpy()
    reb = rebalance_days(h.index, freq)
    nL, nS = len(longs), len(shorts)
    V, pL, pS = 1.0, np.zeros(nL), np.zeros(nS)
    out = np.zeros((len(h), 6))
    for t in range(len(h)):
        cost = turn = 0.0
        drift = t > 0 and band is not False and abs(pS.sum() / (hv[t] * V) - 1) > band
        if reb[t] or drift:
            tL, tS = np.full(nL, V / nL), np.full(nS, hv[t] * V / nS)
            turn = np.abs(tL - pL).sum() + np.abs(tS - pS).sum()
            pL, pS = tL, tS
            cost += exec_bps / 1e4 * turn
        cost += borrow / 252 * pS.sum()
        legL, legS = pL @ rL[t] / pL.sum(), pS @ rS[t] / pS.sum()
        pnl = pL @ rL[t] - pS @ rS[t]
        pL, pS = pL * (1 + rL[t]), pS * (1 + rS[t])
        Vn = V + pnl - cost
        out[t] = (Vn / V - 1, cost / V, turn / V, legL, legS, float(reb[t] or drift))
        V = Vn
    return pd.DataFrame(out, index=h.index, columns=["ret", "cost", "turnover", "long", "short", "rebalanced"])


def ib_closes():
    """Prices pushed by the course server (IB daily bars) into price_history.json, keyed by IB conId."""
    path = ROOT / "price_history.json"
    if not path.exists():
        return pd.DataFrame()
    raw = json.loads(path.read_text())
    cols = {}
    for leg in CONFIG["long"] + CONFIG["short"] + [CONFIG["benchmark"]]:
        cid = leg.get("ib_conid")
        if cid is None or str(cid) not in raw:
            continue
        hist = raw[str(cid)].get("price_history", {})
        cols[leg["ric"]] = pd.Series({pd.Timestamp(d): v["close"] for d, v in hist.items()})
    return pd.DataFrame(cols).sort_index()


def basket_returns(px):
    r = px.pct_change(fill_method=None)
    return r[LONG].mean(axis=1, skipna=True), r[SHORT].mean(axis=1, skipna=True), r[BENCH]


def hedge_ratios(rl, rs, rb, lookback):
    """Short dollars per $1 long, set on the first session of each month from the trailing
    `lookback` sessions that END THE DAY BEFORE (no look-ahead), held for the month."""
    bl = (rl.rolling(lookback).cov(rb) / rb.rolling(lookback).var()).shift(1)
    bs = (rs.rolling(lookback).cov(rb) / rb.rolling(lookback).var()).shift(1)
    raw = bl / bs
    months = rl.index.to_period("M")
    first = ~months.duplicated()
    return raw.where(first).ffill(), bl.where(first).ffill(), bs.where(first).ffill()


def spread_returns(rl, rs, h):
    return (rl - h * rs).dropna()


def to_index(r, base=100.0):
    return base * (1 + r).cumprod()


def ann_stats(r):
    r = r.dropna()
    if len(r) < 2:
        return {}
    idx = (1 + r).cumprod()
    dd = idx / idx.cummax() - 1
    return {
        "total": float(idx.iloc[-1] - 1),
        "vol": float(r.std() * np.sqrt(252)),
        "max_dd": float(dd.min()),
        "days": int(len(r)),
    }


def _clean(o):
    """NaN/inf are not valid JSON; browsers reject the whole file. Map them to null."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return float(o) if np.isfinite(o) else None
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    return o


def write_json(name, obj):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / name).write_text(json.dumps(_clean(obj), indent=1, default=str, allow_nan=False))
