"""Builds docs/data/research.json: the evidence behind the thesis.

Run locally (needs LSEG: Workspace desktop or platform creds), whenever the evidence should be
refreshed. Publishes derived numbers only: basket indices, ratios, and returns. No raw prices.

    LSEG_ENV_FILE=path/to/.env python thesis/research.py
"""
import io
import subprocess
import urllib.request
from datetime import date

import numpy as np
import pandas as pd

from common import (BENCH, CONFIG, LONG, SHORT, TICKER, ann_stats, basket_returns, daily_costs, hedge_ratios,
                    lseg_closes, open_lseg, spread_returns, to_index, write_json)

START_PRICES = "2025-01-02"
START_SPREAD = "2025-07-01"  # first month with 60 sessions of CRWV history (IPO 2025-03-28)
END = pd.Timestamp(CONFIG["inception"])  # evidence stops at the inception close; after that is the live test

# Effective yields (ICE BofA indices via FRED): what it costs each tier of borrower to fund itself.
CREDIT = [("AA-rated corporates (Microsoft, Amazon, Alphabet tier)", "BAMLC0A2CAAEY"),
          ("All investment-grade corporates", "BAMLC0A0CMEY"),
          ("All high-yield corporates", "BAMLH0A0HYM2EY"),
          ("CCC and below", "BAMLH0A3HYCEY")]

# Dated, sourced events. Shown on the chart and in the timeline.
EVENTS = [
    {"date": "2026-05-12", "label": "PA large-load tariff",
     "text": "Pennsylvania PUC final order: data centers over 50 MW pay for their own grid upgrades.",
     "url": "https://www.puc.pa.gov/press-release/2026/puc-acts-to-protect-ratepayers-guide-data-center-growth-with-new-large-load-tariff-framework-4-30-26"},
    {"date": "2026-06-04", "label": "NY moratorium bill",
     "text": "New York legislature passes a one-year moratorium on state permits for new data centers of 20 MW or more.",
     "url": "https://www.davispolk.com/insights/client-update/new-york-state-enacts-data-center-moratorium"},
    {"date": "2026-07-14", "label": "NY executive order",
     "text": "Governor Hochul signs Executive Order 62, the first statewide pause on permits for new hyperscale data centers.",
     "url": "https://www.cnbc.com/2026/07/14/new-york-ai-data-center-ban.html"},
    {"date": "2026-11-04", "label": "Midterms",
     "text": "Democrats win both chambers; local data-center measures go against development in 40 of 41 votes; Texas elects a governor who campaigned on a data-center pause.",
     "url": "https://www.cnbc.com/2026/11/06/ai-data-center-midterms-selloff.html"},
    {"date": "2026-11-09", "label": "Lenders pull back",
     "text": "Banks that funded GPU clouds say they would hesitate to lend more; CoreWeave's quarterly interest is five times its adjusted operating income and its 2032 notes yield above 16%.",
     "url": "https://finance.yahoo.com/markets/stocks/articles/banks-raced-fund-neoclouds-coreweave-020300823.html"},
]


def fred(series):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        raw = urllib.request.urlopen(req, timeout=30).read().decode()
    except OSError:  # some networks stall Python's TLS to FRED; curl gets through
        raw = subprocess.run(["curl", "-sS", "-m", "60", url], capture_output=True, text=True, check=True).stdout
    s = pd.read_csv(io.StringIO(raw), index_col=0, parse_dates=True).iloc[:, 0]
    return pd.to_numeric(s, errors="coerce").dropna()


def fundamentals(ld):
    rics = LONG + SHORT
    ltm = ld.get_data(rics, ["TR.Revenue", "TR.CapitalExpenditures"], {"Period": "LTM"})
    q0 = ld.get_data(rics, ["TR.Revenue", "TR.InterestExpense", "TR.Revenue.periodenddate"], {"Period": "FQ0"})
    q4 = ld.get_data(rics, ["TR.Revenue"], {"Period": "FQ-4"})
    rows = []
    for ric in rics:
        a = ltm[ltm.Instrument == ric].iloc[0]
        b = q0[q0.Instrument == ric].iloc[0]
        c = q4[q4.Instrument == ric].iloc[0]
        rev_ltm, capex = a["Revenue"], a["Capital Expenditures, Cumulative"]
        rev_q, intr, rev_q4 = b["Revenue"], b["Interest Expense"], c["Revenue"]
        f = lambda x: None if pd.isna(x) else float(x)
        rows.append({
            "ticker": TICKER[ric], "side": "long" if ric in LONG else "short",
            "capex_to_revenue": f(abs(capex) / rev_ltm) if rev_ltm else None,
            "interest_to_revenue": f(intr / rev_q) if rev_q and not pd.isna(intr) else None,
            "revenue_growth": f(rev_q / rev_q4 - 1) if rev_q4 and not pd.isna(rev_q4) else None,
            "quarter": str(b["Period End Date"])[:10],
        })
    return rows


def main():
    ld = open_lseg()
    if ld is None:
        raise SystemExit("No LSEG session; research.json not rebuilt.")
    try:
        px = lseg_closes(ld, START_PRICES, end=(END + pd.Timedelta(days=1)).strftime("%Y-%m-%d")).loc[:END]
        fund = fundamentals(ld)
    finally:
        ld.close_session()

    lb = CONFIG["hedge"]["lookback_sessions"]
    rl, rs, rb = basket_returns(px)
    h, bl, bs = hedge_ratios(rl, rs, rb, lb)
    win = slice(START_SPREAD, None)
    spr = spread_returns(rl[win], rs[win], h[win])
    # Long-only alternative: the same long basket, hedged with QQQ at its own beta (same monthly reset).
    lq = spread_returns(rl[win], rb[win], bl[win]).reindex(spr.index)
    rl, rs, rb, h = rl.loc[spr.index], rs.loc[spr.index], rb.loc[spr.index], h.loc[spr.index]

    # Costs: base case for the headline net numbers, plus a grid of borrow fee x execution cost.
    C = CONFIG["costs"]
    cost_base, turnover = daily_costs(px, h, C["borrow_base"], C["exec_bps_base"])
    net = spr - cost_base
    grid_total, grid_drag = [], []
    for bfee in C["borrow_grid"]:
        rt, rd = [], []
        for eb in C["exec_bps_grid"]:
            c, _ = daily_costs(px, h, bfee, eb)
            rt.append(float((1 + spr - c).prod() - 1))
            rd.append(float(c.iloc[1:].mean() * 252))  # running cost per year, excluding the entry trade
        grid_total.append(rt)
        grid_drag.append(rd)

    s_idx = to_index(rs)
    peak = s_idx.idxmax()
    regimes = []
    for name, a, b in [("Build everything", spr.index[0], peak), ("Built beats building", peak + pd.Timedelta(days=1), spr.index[-1])]:
        sl = slice(a, b)
        tot = lambda r: float((1 + r.loc[sl]).prod() - 1)
        regimes.append({"name": name, "from": str(a.date()), "to": str(b.date()),
                        "long": tot(rl), "short": tot(rs), "spread": tot(spr), "qqq": tot(rb),
                        "long_qqq": tot(lq), "spread_net": tot(net)})

    beta = float(np.cov(spr, rb)[0, 1] / rb.var())
    election = []
    for d in ["2026-11-04", "2026-11-05"]:
        t = pd.Timestamp(d)
        if t in spr.index:
            election.append({"date": d, "long": float(rl[t]), "short": float(rs[t]), "spread": float(spr[t]),
                             "qqq": float(rb[t]), "long_qqq": float(lq[t])})

    credit = []
    for name, sid in CREDIT:
        y = fred(sid).loc[:END]
        credit.append({"name": name, "series": sid, "yield": float(y.iloc[-1]) / 100,
                       "year_ago": float(y.loc[:END - pd.Timedelta(days=365)].iloc[-1]) / 100})
    credit_date = str(fred(CREDIT[0][1]).loc[:END].index[-1].date())

    elec, cpi = fred("CUSR0000SEHF01"), fred("CPIAUCSL")
    e = pd.DataFrame({"elec": elec, "cpi": cpi}).dropna().loc["2021-01-01":]
    e = 100 * e / e.iloc[0]

    write_json("research.json", {
        "built": str(date.today()),
        "legs": {side: [{"ticker": x["ticker"], "why": x["why"]} for x in CONFIG[side]] for side in ("long", "short")},
        "spread": {"dates": [str(x.date()) for x in spr.index],
                   "index": [round(x, 2) for x in to_index(spr)],
                   "hedge": [round(x, 3) for x in h]},
        "spread_stats": {**ann_stats(spr), "beta_qqq": beta, "from": str(spr.index[0].date())},
        "long_qqq": {"index": [round(x, 2) for x in to_index(lq)],
                     "stats": {**ann_stats(lq), "beta_qqq": float(np.cov(lq, rb)[0, 1] / rb.var())}},
        "credit": {"date": credit_date, "rows": credit},
        "costs": {"borrow_base": C["borrow_base"], "exec_bps_base": C["exec_bps_base"],
                  "borrow_grid": C["borrow_grid"], "exec_bps_grid": C["exec_bps_grid"],
                  "net_total": grid_total, "drag_per_year": grid_drag,
                  "turnover_per_year": float(turnover.iloc[1:].mean() * 252),
                  "avg_hedge": float(h.mean()), "net_index": [round(x, 2) for x in to_index(net)],
                  "net_stats": ann_stats(net)},
        "short_peak": str(peak.date()),
        "regimes": regimes,
        "election": election,
        "events": EVENTS,
        "fundamentals": fund,
        "power": {"dates": [str(x.date())[:7] for x in e.index],
                  "electricity": [round(x, 1) for x in e["elec"]],
                  "cpi": [round(x, 1) for x in e["cpi"]],
                  "latest": str(e.index[-1].date())[:7]},
    })
    print("research.json written:", len(spr), "sessions; short-basket peak", peak.date())


if __name__ == "__main__":
    main()
