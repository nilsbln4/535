"""Daily live tracking from the inception date. Writes docs/data/live.json (derived numbers only).

Price source, in order:
  1. price_history.json pushed by the course server (IB daily bars), if it covers every leg;
  2. LSEG (platform session in GitHub Actions, Workspace desktop locally).
If neither works, the previous live.json is left untouched and the run exits cleanly.
"""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from common import (ALL, BENCH, CONFIG, DATA_DIR, ann_stats, basket_returns, hedge_ratios,
                    ib_closes, lseg_closes, open_lseg, spread_returns, to_index, write_json)

INCEPTION = pd.Timestamp(CONFIG["inception"])
LOOKBACK = CONFIG["hedge"]["lookback_sessions"]
DRAWDOWN_LIMIT = -0.15      # kill criterion 1: spread down 15% from its high since inception
SHORTS_OUTRUN_LIMIT = 0.25  # kill criterion 2: short basket beats long basket by 25 pts since inception


def previous():
    p = DATA_DIR / "live.json"
    return json.loads(p.read_text()) if p.exists() else {}


def get_prices():
    ib = ib_closes()
    if not ib.empty and set(ALL) <= set(ib.columns) and ib.index.min() <= INCEPTION - pd.Timedelta(days=100):
        return ib, "course server (IB daily bars)"
    ld = open_lseg()
    if ld is not None:
        try:
            start = (INCEPTION - pd.Timedelta(days=130)).strftime("%Y-%m-%d")
            px = lseg_closes(ld, start)
            if not ib.empty:  # server bars win where they exist
                px = ib.combine_first(px)
            return px, "LSEG"
        finally:
            ld.close_session()
    return None, None


def main():
    prev = previous()
    px, source = get_prices()
    if px is None:
        print("no price source available; live.json unchanged")
        return

    px = px[ALL].dropna(how="all")
    rl, rs, rb = basket_returns(px)

    # Hedge at inception: trailing LOOKBACK sessions ending at the inception close.
    w = pd.DataFrame({"l": rl, "s": rs, "b": rb}).loc[:INCEPTION].dropna().tail(LOOKBACK)
    bl0 = w["l"].cov(w["b"]) / w["b"].var()
    bs0 = w["s"].cov(w["b"]) / w["b"].var()
    kept = prev.get("hedge_at_inception")  # frozen once set, so data revisions can't move it
    h0 = float(kept) if isinstance(kept, (int, float)) and np.isfinite(kept) else float(bl0 / bs0)
    if not np.isfinite(h0):
        raise SystemExit("not enough history before inception to set the hedge")

    h_monthly, _, _ = hedge_ratios(rl, rs, rb, LOOKBACK)
    after = rl.index > INCEPTION
    first_month_end = (INCEPTION + pd.offsets.MonthBegin(1))
    h = pd.Series(h0, index=rl.index)
    roll = after & (rl.index >= first_month_end)
    h[roll] = h_monthly[roll]
    h = h.ffill()

    live = slice(INCEPTION + pd.Timedelta(days=1), None)
    spr = spread_returns(rl[live], rs[live], h[live])
    dates = [str(INCEPTION.date())] + [str(d.date()) for d in spr.index]
    idx = lambda r: [100.0] + [round(x, 2) for x in to_index(r.loc[spr.index])]

    s_index = idx(spr)
    l_index, sh_index, q_index = idx(rl), idx(rs), idx(rb)
    peak = max(s_index)
    dd = s_index[-1] / peak - 1
    outrun = (sh_index[-1] - l_index[-1]) / 100

    write_json("live.json", {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": source,
        "inception": str(INCEPTION.date()),
        "hedge_at_inception": round(float(h0), 4),
        "hedge_now": round(float(h.iloc[-1]), 4),
        "dates": dates,
        "spread": s_index, "long": l_index, "short": sh_index, "qqq": q_index,
        "stats": ann_stats(spr),
        "kill": [
            {"name": "Spread drawdown from its high since inception", "value": dd,
             "limit": DRAWDOWN_LIMIT, "tripped": dd <= DRAWDOWN_LIMIT},
            {"name": "Short basket minus long basket since inception", "value": outrun,
             "limit": SHORTS_OUTRUN_LIMIT, "tripped": outrun >= SHORTS_OUTRUN_LIMIT},
        ],
    })
    print(f"live.json: {len(spr)} sessions since inception, spread {s_index[-1]}, source {source}")


if __name__ == "__main__":
    main()
