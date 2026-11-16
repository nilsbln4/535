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
