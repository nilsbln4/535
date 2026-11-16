"use strict";

const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const pct = (x, d = 1) => (x == null || isNaN(x) ? "–" : (x >= 0 ? "+" : "−") + Math.abs(x * 100).toFixed(d) + "%");
const pctPlain = (x, d = 1) => (x == null || isNaN(x) ? "–" : (x * 100).toFixed(d) + "%");
const fmtDate = (s) => new Date(s + "T12:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const set = (key, html) => document.querySelectorAll(`[data-f="${key}"]`).forEach((el) => (el.innerHTML = html));
const sign = (x) => (x >= 0 ? "pos" : "neg");

async function getJSON(path) {
  try {
    const r = await fetch(path, { cache: "no-store" });
    return r.ok ? await r.json() : null;
  } catch (e) {
    return null;
  }
}

/* ---------- line chart: SVG drawn at the container's real width ---------- */
function lineChart(el, cfg) {
  const draw = () => {
    const W = el.clientWidth, H = el.clientHeight;
    const m = { t: 22, r: 8, b: 24, l: 42 };
    const n = cfg.dates.length;
    if (n < 2) { el.innerHTML = ""; return; }
    const t = cfg.dates.map((d) => Date.parse(d.length === 7 ? d + "-01" : d));
    const vals = cfg.series.flatMap((s) => s.values).filter((v) => v != null);
    let lo = Math.min(...vals, cfg.base ?? Infinity), hi = Math.max(...vals, cfg.base ?? -Infinity);
    const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
    const x = (i) => m.l + ((t[i] - t[0]) / (t[n - 1] - t[0])) * (W - m.l - m.r);
    const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);

    const step = niceStep((hi - lo) / 5);
    let svg = `<svg width="${W}" height="${H}" role="img">`;
    (cfg.bands || []).forEach((b) => {
      const i0 = cfg.dates.findIndex((d) => d >= b.from), i1 = cfg.dates.length - 1;
      if (i0 >= 0) svg += `<rect class="band" x="${x(i0)}" y="${m.t}" width="${x(i1) - x(i0)}" height="${H - m.t - m.b}"/>`;
    });
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      svg += `<line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>`;
      svg += `<text x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end">${cfg.yFmt ? cfg.yFmt(v) : Math.round(v)}</text>`;
    }
    if (cfg.base != null) svg += `<line class="zero" x1="${m.l}" x2="${W - m.r}" y1="${y(cfg.base)}" y2="${y(cfg.base)}"/>`;
    // x ticks: first and last plus a few in between
    const ticks = Math.max(2, Math.min(6, Math.floor(W / 130)));
    for (let k = 0; k < ticks; k++) {
      const i = Math.round((k / (ticks - 1)) * (n - 1));
      const lab = cfg.xFmt ? cfg.xFmt(cfg.dates[i]) : cfg.dates[i];
      svg += `<text x="${x(i)}" y="${H - 6}" text-anchor="${k === 0 ? "start" : k === ticks - 1 ? "end" : "middle"}">${lab}</text>`;
    }
    (cfg.markers || []).forEach((mk, j) => {
      const i = cfg.dates.findIndex((d) => d >= mk.date);
      if (i < 0) return;
      const xx = x(i);
      svg += `<line class="marker" x1="${xx}" x2="${xx}" y1="${m.t - 2}" y2="${H - m.b}"/>`;
      // Numbered markers; the numbers match §4. Labels would collide where events cluster.
      svg += `<circle cx="${xx}" cy="${m.t - 9}" r="7" fill="${css("--paper")}" stroke="${css("--accent")}"/>`;
      svg += `<text class="marker-label" x="${xx}" y="${m.t - 5.5}" text-anchor="middle">${j + 1}</text>`;
    });
    cfg.series.forEach((s) => {
      let d = "";
      s.values.forEach((v, i) => { if (v != null) d += (d ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1); });
      svg += `<path d="${d}" fill="none" stroke="${css(s.color)}" stroke-width="${s.width || 1.6}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ""}/>`;
    });
    svg += `<line id="xh" class="grid" y1="${m.t}" y2="${H - m.b}" style="display:none"/></svg><div class="tip"></div>`;
    el.innerHTML = svg;

    const tip = el.querySelector(".tip"), xh = el.querySelector("#xh");
    el.onmousemove = (ev) => {
      const rect = el.getBoundingClientRect(), px = ev.clientX - rect.left;
      let i = 0, best = Infinity;
      for (let k = 0; k < n; k++) { const dd = Math.abs(x(k) - px); if (dd < best) { best = dd; i = k; } }
      xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xh.style.display = "";
      tip.innerHTML = `<b>${cfg.dates[i]}</b><br>` + cfg.series.map((s) => `<span style="color:${css(s.color)}">■</span> ${s.name} ${s.values[i] == null ? "–" : (cfg.tipFmt ? cfg.tipFmt(s.values[i]) : s.values[i].toFixed(1))}`).join("<br>");
      tip.style.display = "block";
      tip.style.left = Math.min(px + 12, W - tip.offsetWidth - 4) + "px";
      tip.style.top = "6px";
    };
    el.onmouseleave = () => { tip.style.display = "none"; xh.style.display = "none"; };
  };
  draw();
  new ResizeObserver(draw).observe(el);
}

function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / p;
  return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * p;
}

function legend(el, items) {
  el.innerHTML = items.map((s) => `<span><i style="background:${css(s.color)}${s.dash ? ";opacity:.6" : ""}"></i>${s.name}</span>`).join("");
}

const monthFmt = (d) => new Date(d + (d.length === 7 ? "-01" : "") + "T12:00:00Z").toLocaleDateString("en-GB", { month: "short", year: "2-digit", timeZone: "UTC" });

/* ---------- page ---------- */
(async function () {
  const [R, L] = await Promise.all([getJSON("data/research.json"), getJSON("data/live.json")]);

  if (R) {
    const li = (x) => `<li>${x.ticker} <span>· ${x.why.split(";")[0]}</span></li>`;
    document.getElementById("long-list").innerHTML = R.legs.long.map(li).join("");
    document.getElementById("short-list").innerHTML = R.legs.short.map(li).join("");

    set("bt-from", fmtDate(R.spread_stats.from));
    set("peak", fmtDate(R.short_peak));
    set("bt-vol", pctPlain(R.spread_stats.vol, 0));
    set("bt-dd", pct(R.spread_stats.max_dd, 0));
    set("bt-beta", R.spread_stats.beta_qqq.toFixed(2));
    set("built", fmtDate(R.built));

    const regimeSeries = [{ name: "Beta-neutral spread", values: R.spread.index, color: "--spread", width: 2 }];
    lineChart(document.getElementById("chart-regime"), {
      dates: R.spread.dates, series: regimeSeries, base: 100, xFmt: monthFmt,
      markers: R.events.map((e) => ({ date: e.date, label: e.label })),
      bands: [{ from: R.short_peak }],
    });

    const rg = R.regimes;
    document.getElementById("regimes").innerHTML =
      `<tr><th>Period</th><th>Long</th><th>Short</th><th>Spread</th><th>QQQ</th></tr>` +
      rg.map((g) => `<tr><td>${g.name}<br><span class="caption">${fmtDate(g.from)} – ${fmtDate(g.to)}</span></td>
        <td class="l">${pct(g.long, 0)}</td><td class="s">${pct(g.short, 0)}</td>
        <td><b class="${sign(g.spread)}">${pct(g.spread, 1)}</b></td><td>${pct(g.qqq, 0)}</td></tr>`).join("");

    const F = R.fundamentals;
    const maxCap = Math.max(...F.map((f) => f.capex_to_revenue || 0));
    document.getElementById("fund").innerHTML =
      `<tr><th></th><th class="bar">Capex ÷ revenue</th><th>Capex ÷ rev.</th><th>Interest ÷ rev.</th><th>Rev. growth</th></tr>` +
      F.map((f, i) => {
        const c = f.side === "long" ? "l" : "s";
        const split = i > 0 && F[i - 1].side !== f.side ? ' class="split"' : "";
        const w = Math.max(1, (100 * (f.capex_to_revenue || 0)) / maxCap);
        return `<tr${split}><td class="${c}">${f.ticker}</td>
          <td class="bar"><div class="barwrap"><b class="${c}" style="width:${w}%"></b></div></td>
          <td>${f.capex_to_revenue == null ? "–" : f.capex_to_revenue.toFixed(2) + "×"}</td>
          <td>${pctPlain(f.interest_to_revenue)}</td>
          <td>${pct(f.revenue_growth, 0)}</td></tr>`;
      }).join("");

    const P = R.power;
    const powerSeries = [
      { name: "Electricity prices (CPI)", values: P.electricity, color: "--accent", width: 2 },
      { name: "All prices (CPI)", values: P.cpi, color: "--bench", width: 1.6 },
    ];
    lineChart(document.getElementById("chart-power"), { dates: P.dates, series: powerSeries, base: 100, xFmt: monthFmt });
    legend(document.getElementById("legend-power"), powerSeries.map((s) => ({ ...s, name: s.name + " · Jan 2021 = 100" })));

    document.getElementById("election").innerHTML =
      `<tr><th>Session</th><th>Long</th><th>Short</th><th>Spread</th><th>QQQ</th></tr>` +
      R.election.map((e) => `<tr><td>${fmtDate(e.date)}</td><td class="l">${pct(e.long)}</td><td class="s">${pct(e.short)}</td><td><b class="${sign(e.spread)}">${pct(e.spread)}</b></td><td>${pct(e.qqq)}</td></tr>`).join("");

    document.getElementById("timeline").innerHTML = R.events.map((e, i) =>
      `<li><time><span class="n">${i + 1}</span>${fmtDate(e.date)} · ${e.label}</time><p>${e.text} <a href="${e.url}" rel="noopener">Source</a></p></li>`).join("");
  }

  if (L) {
    set("inception", fmtDate(L.inception));
    set("hedge", "$" + L.hedge_now.toFixed(2));
    set("updated", L.updated);
    const sessions = L.dates.length - 1;
    set("live-days", String(sessions));
    const total = L.spread[L.spread.length - 1] / 100 - 1;
    set("live-total", sessions ? `<span class="${sign(total)}">${pct(total, 2)}</span>` : "0.00%");

    const liveSeries = [
      { name: "Spread (beta-neutral)", values: L.spread, color: "--spread", width: 2.2 },
      { name: "Long basket", values: L.long, color: "--long" },
      { name: "Short basket", values: L.short, color: "--short" },
      { name: "QQQ", values: L.qqq, color: "--bench", dash: "4 3" },
    ];
    if (sessions < 2) {
      document.getElementById("chart-live").style.display = "none";
      document.getElementById("legend-live").style.display = "none";
      set("live-note", `Tracking runs from the ${fmtDate(L.inception)} close. The chart appears after the second session. Price source: ${L.source}.`);
    } else {
      set("live-note", `Indexed to 100 at the ${fmtDate(L.inception)} close. Price source: ${L.source}.`);
      lineChart(document.getElementById("chart-live"), { dates: L.dates, series: liveSeries, base: 100, xFmt: (d) => d.slice(5) });
    }
    legend(document.getElementById("legend-live"), liveSeries);

    document.getElementById("kill").innerHTML =
      `<tr><th>Wrong-if test</th><th>Now</th><th>Limit</th><th>Status</th></tr>` +
      L.kill.map((k) => `<tr><td>${k.name}</td><td>${pct(k.value)}</td><td>${pct(k.limit, 0)}</td>
        <td class="${k.tripped ? "trip" : "ok"}">${k.tripped ? "Tripped" : "Holding"}</td></tr>`).join("") +
      `<tr><td>Federal override of local limits; cheap GPU-cloud refinancing</td><td colspan="2">checked by hand</td><td>–</td></tr>`;
  }
})();
