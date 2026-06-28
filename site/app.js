/* RTM Trading Assistant — frontend (Lightweight Charts + Persian panel) */
const $ = (s) => document.querySelector(s);
/* STATIC mode: no Python backend. Signals come from precomputed JSON (built by GitHub
   Actions, served by the PHP host); candles + live price come straight from Binance. */
const STATIC = !!window.RTM_STATIC;
const BINANCE = "https://api.binance.com/api/v3";
const TF2IV = { M1: "1m", M5: "5m", M15: "15m", H1: "1h", H4: "4h" };
const binSym = (s) => /USDT$/.test(s) ? s : null;   // XAUUSD has no Binance ticker
// fetch with a timeout so a slow/blocked Binance never hangs the whole UI
async function bfetch(url, ms = 8000) {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), ms);
  try { return await fetch(url, { signal: ctl.signal }).then((r) => r.json()); }
  finally { clearTimeout(t); }
}
// Binance market data via the host-side proxy FIRST (api.binance.com is geo-blocked in
// some regions, e.g. Iran, so a direct browser fetch returns nothing and the chart stays
// empty). If the proxy is missing/unreachable we fall back to a direct Binance call so the
// site still works for visitors whose network can reach Binance.
async function binFetch(path, params, ms = 8000) {
  const qs = new URLSearchParams(params).toString();
  try {
    const r = await bfetch(`proxy.php?path=${path}&${qs}`, ms);
    if (r && !r.__error) return r;
  } catch (e) { /* fall through to direct */ }
  const direct = path === "ticker" ? `${BINANCE}/ticker/price?${qs}` : `${BINANCE}/klines?${qs}`;
  return bfetch(direct, ms);
}

async function api(p) {
  if (!STATIC) return fetch(p).then((r) => r.json());
  const u = new URL(p, location.href), q = u.searchParams;
  if (p.startsWith("/api/symbols")) return fetch("data/symbols.json").then((r) => r.json());
  if (p.startsWith("/api/timeframes")) return fetch("data/timeframes.json").then((r) => r.json());
  if (p.startsWith("/api/signal")) return fetch(`data/sig_${q.get("symbol")}_${q.get("tf")}.json`).then((r) => r.json()).then((d) => d.signal);
  if (p.startsWith("/api/assistant")) return fetch(`data/sig_${q.get("symbol")}_${q.get("tf")}.json`).then((r) => r.json());
  if (p.startsWith("/api/quote")) return staticQuote(q.get("symbol"));
  if (p.startsWith("/api/journal")) return fetch("journal.php").then((r) => r.json()).catch(() => ({ entries: [] }));
  return {};
}
const post = (p, body) => STATIC
  ? fetch("journal.php", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => r.json()).catch(() => ({ ok: false }))
  : fetch(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => r.json());

async function staticQuote(sym) {
  const b = binSym(sym);
  if (b) { try { const t = await binFetch("ticker", { symbol: b }); return { price: +t.price, delayed: false }; } catch (e) { return { price: null, delayed: false }; } }
  return { price: null, delayed: true };
}
async function staticCandles(sym, tf, limit) {
  const b = binSym(sym);
  if (b) {
    try {
      const rows = await binFetch("klines", { symbol: b, interval: TF2IV[tf] || "5m", limit: Math.min(limit || 1000, 1000) });
      return rows.map((k) => ({ time: Math.floor(k[0] / 1000), open: +k[1], high: +k[2], low: +k[3], close: +k[4] }));
    } catch (e) { return []; }   // Binance slow/blocked -> render signals without candles
  }
  const d = await fetch(`data/ohlcv_${sym}_${tf}.json`).then((r) => r.json()).catch(() => ({ bars: [] }));
  return (d.bars || []).map((x) => ({ time: Math.floor(Date.parse(x.time.replace(" ", "T") + "Z") / 1000), open: x.open, high: x.high, low: x.low, close: x.close })).filter((x) => Number.isFinite(x.time));
}

let STATE = { symbol: null, tf: "M5", health: {}, lastSig: null, candleTimes: [] };

/* ---------- chart ---------- */
const chartEl = $("#chart");
const chart = LightweightCharts.createChart(chartEl, {
  layout: { background: { color: "#12151a" }, textColor: "#a7b0bd", fontFamily: "IRANYekan, Tahoma" },
  grid: { vertLines: { color: "rgba(255,255,255,.035)" }, horzLines: { color: "rgba(255,255,255,.045)" } },
  rightPriceScale: { borderColor: "rgba(255,255,255,.08)" },
  timeScale: { borderColor: "rgba(255,255,255,.08)", timeVisible: true, secondsVisible: false,
    rightOffset: 6, barSpacing: 8, minBarSpacing: 0.4 },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal,
    vertLine: { color: "rgba(212,175,55,.35)", labelBackgroundColor: "#d4af37" },
    horzLine: { color: "rgba(212,175,55,.35)", labelBackgroundColor: "#d4af37" } },
});
const C = { green: "#2bb98a", red: "#ef5b6b", blue: "#5b8def", gold: "#d4af37", rsi: "#8aa0c8" };
const candles = chart.addCandlestickSeries({
  upColor: C.green, downColor: C.red, borderUpColor: C.green,
  borderDownColor: C.red, wickUpColor: C.green, wickDownColor: C.red,
});
// leave room at the bottom for the RSI sub-pane
chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.06, bottom: 0.27 } });
// RSI(14) on its own bottom-pinned scale, with 70/30 guide lines + in-pane text labels
const rsiSeries = chart.addLineSeries({ priceScaleId: "rsi", color: C.rsi, lineWidth: 1.5,
  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: true });
chart.priceScale("rsi").applyOptions({ scaleMargins: { top: 0.76, bottom: 0.02 } });
rsiSeries.createPriceLine({ price: 70, color: "rgba(239,91,107,.5)", lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
rsiSeries.createPriceLine({ price: 30, color: "rgba(43,185,138,.5)", lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
rsiSeries.createPriceLine({ price: 50, color: "rgba(255,255,255,.12)", lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
let rsiNowLine = null;   // a moving line showing where RSI is RIGHT NOW

/* ---- RSI text labels drawn inside the sub-pane (overlay scales can't show axis labels) ---- */
class RsiLabelsRenderer {
  constructor(src) { this._s = src; }
  draw(target) {
    try {
      const s = this._s._series; if (!s) return;
      target.useBitmapCoordinateSpace((scope) => {
        const ctx = scope.context, vr = scope.verticalPixelRatio, hr = scope.horizontalPixelRatio;
        ctx.font = `700 ${Math.round(10.5 * vr)}px IRANYekan, Tahoma`;
        ctx.textBaseline = "middle";
        const x = 8 * hr;                       // LEFT edge of the pane (price axis is on the right)
        for (const it of this._s._items()) {
          const y = s.priceToCoordinate(it.v);
          if (y == null || !isFinite(y)) continue;
          const tw = ctx.measureText(it.text).width;
          ctx.fillStyle = it.bg || "rgba(12,14,18,.72)";
          ctx.fillRect(x - 3 * hr, y * vr - 8 * vr, tw + 7 * hr, 16 * vr);
          ctx.fillStyle = it.color;
          ctx.fillText(it.text, x, y * vr);
        }
      });
    } catch (e) {}
  }
}
class RsiLabelsPaneView { constructor(s) { this._s = s; } update() {} renderer() { return new RsiLabelsRenderer(this._s); } zOrder() { return "top"; } }
class RsiLabels {
  constructor() { this._series = null; this._now = null; this._pv = new RsiLabelsPaneView(this); }
  attached(p) { this._series = p.series; this._req = p.requestUpdate; }
  detached() { this._series = null; }
  updateAllViews() {}
  paneViews() { return [this._pv]; }
  setNow(v) { this._now = v; if (this._req) this._req(); }
  _items() {
    const it = [{ v: 70, text: "۷۰ اشباعِ خرید", color: "#ef8b96" },
                { v: 30, text: "۳۰ اشباعِ فروش", color: "#5fceac" },
                { v: 50, text: "۵۰", color: "rgba(255,255,255,.45)" }];
    if (this._now != null) {
      const v = this._now, c = v >= 70 ? "#ef5b6b" : v <= 30 ? "#2bb98a" : "#d4af37";
      it.push({ v, text: `RSI اکنون ${Math.round(v)}`, color: "#0c0e12", bg: c });
    }
    return it;
  }
}
const rsiLabels = new RsiLabels();
rsiSeries.attachPrimitive(rsiLabels);
// trend projection (dashed gold) — a hypothesis, clearly secondary
const projSeries = chart.addLineSeries({ color: C.gold, lineWidth: 2, lineStyle: 2,
  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
let _sized = false;
function fitChart() {
  const w = chartEl.clientWidth, h = chartEl.clientHeight;
  if (w > 50 && h > 50) {
    chart.applyOptions({ width: w, height: h });   // guard tiny/collapsed sizes
    _sized = true;   // (no fitContent — fixed barSpacing keeps candles readable + scrollable)
  }
}
new ResizeObserver(fitChart).observe(chartEl);
window.addEventListener("resize", fitChart);
fitChart();

/* ---------- zone-band primitive (filled horizontal band between two prices) ----------
   Canonical LWC v4 plugin shape: primitive -> paneViews() -> paneView.renderer() (a
   METHOD) -> renderer.draw(target). draw is fully guarded so it can NEVER throw and
   abort the candle frame. */
class ZoneBandRenderer {
  constructor(src) { this._src = src; }
  draw(target) {
    try {
      const s = this._src._series; if (!s) return;
      const yt = s.priceToCoordinate(this._src._top), yb = s.priceToCoordinate(this._src._bottom);
      if (yt == null || yb == null || !isFinite(yt) || !isFinite(yb)) return;
      target.useBitmapCoordinateSpace((scope) => {
        const ctx = scope.context, vr = scope.verticalPixelRatio, hr = scope.horizontalPixelRatio;
        const y1 = Math.min(yt, yb) * vr, y2 = Math.max(yt, yb) * vr;
        ctx.fillStyle = this._src._fill;
        ctx.fillRect(0, y1, scope.bitmapSize.width, Math.max(1, y2 - y1));
        const lbl = this._src._label;
        if (lbl) {                                   // zone label at the left edge
          ctx.font = `700 ${Math.round(11 * vr)}px IRANYekan, Tahoma`;
          ctx.textBaseline = "top"; ctx.fillStyle = this._src._labelColor;
          ctx.fillText(lbl, 8 * hr, Math.min(y1, y2) + 3 * vr);
        }
      });
    } catch (e) { /* never abort the frame */ }
  }
}
class ZoneBandPaneView {
  constructor(src) { this._src = src; }
  update() {}
  renderer() { return new ZoneBandRenderer(this._src); }
  zOrder() { return "bottom"; }
}
class ZoneBand {
  constructor(top, bottom, fill, label, labelColor) {
    this._top = top; this._bottom = bottom; this._fill = fill;
    this._label = label || ""; this._labelColor = labelColor || "rgba(255,255,255,.55)";
    this._series = null; this._pv = new ZoneBandPaneView(this);
  }
  attached(p) { this._series = p.series; this._requestUpdate = p.requestUpdate; }
  detached() { this._series = null; }
  updateAllViews() {}
  paneViews() { return [this._pv]; }
}
let bands = [], priceLines = [];
function clearLines() {                                  // entry/SL/TP lines only (not the zone bands)
  priceLines.forEach((l) => { try { candles.removePriceLine(l); } catch (e) {} });
  priceLines = [];
}
function clearOverlays() {
  bands.forEach((b) => { try { candles.detachPrimitive(b); } catch (e) {} });
  bands = [];
  clearLines();
}
function band(top, bottom, fill, label, labelColor) { const b = new ZoneBand(top, bottom, fill, label, labelColor); candles.attachPrimitive(b); bands.push(b); }
function pline(price, color, style, title) {
  priceLines.push(candles.createPriceLine({ price, color, lineWidth: style === "solid" ? 2 : 1,
    lineStyle: style === "solid" ? 0 : 2, axisLabelVisible: true, title }));
}
// Draw the entry / stop / targets for ONE zone. Called for the primary on load, and
// re-called for whichever zone the user clicks — so every zone gets its own levels.
function drawZoneLines(z) {
  clearLines();
  if (!z) return;
  STATE.activeZone = z;
  pline(z.entry, C.blue, "solid", "ورود");
  pline(z.sl, C.red, "dash", "استاپ");
  if (z.tp1 != null) pline(z.tp1, C.green, "dash", "TP1");
  pline(z.tp2, C.green, "solid", "TP2 (2R)");
  if (z.tp3 != null) pline(z.tp3, C.green, "dash", "TP3");
}

/* ---------- data loading ---------- */
async function loadSymbols() {
  const d = await api("/api/symbols");
  const sel = $("#symbolSel");
  sel.innerHTML = "";
  d.symbols.forEach((s) => {
    const o = document.createElement("option");
    o.value = s.symbol; o.textContent = s.label + (s.delayed ? " (تأخیری)" : "");
    sel.appendChild(o);
  });
  STATE.symbol = d.symbols[0].symbol;
  sel.value = STATE.symbol;
  sel.onchange = () => { STATE.symbol = sel.value; reload(true); };
}
async function loadTFs() {
  const d = await api("/api/timeframes");
  const g = $("#tfGroup");
  g.innerHTML = "";
  d.timeframes.forEach((tf) => {
    const b = document.createElement("button");
    b.textContent = tf; b.dataset.tf = tf;
    const dot = document.createElement("span"); dot.className = "dot"; b.appendChild(dot);
    if (tf === STATE.tf) b.classList.add("active");
    b.onclick = () => { STATE.tf = tf; document.querySelectorAll("#tfGroup button").forEach((x) => x.classList.remove("active")); b.classList.add("active"); reload(true); };
    g.appendChild(b);
  });
}

function fmt(x) {
  if (x == null) return "—";
  const a = Math.abs(x), d = a >= 100 ? 2 : a >= 1 ? 4 : 6;
  return x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

async function loadChart(keepView, deep) {
  let bars;
  if (STATIC) {                          // candles direct from Binance (or XAUUSD JSON)
    bars = await staticCandles(STATE.symbol, STATE.tf, 1000);
  } else {
    const limit = deep ? 20000 : 1000;   // server paginates months of history
    const d = await api(`/api/ohlcv?symbol=${STATE.symbol}&tf=${STATE.tf}&limit=${limit}`);
    bars = d.bars.map((b) => ({ time: Math.floor(Date.parse(b.time.replace(" ", "T") + "Z") / 1000), open: b.open, high: b.high, low: b.low, close: b.close }))
      .filter((b) => Number.isFinite(b.time));
  }
  STATE.candleTimes = bars.map((b) => b.time);
  candles.setData(bars);
  // On first load / symbol switch, snap to the latest bars at the fixed readable barSpacing
  // (NOT fitContent — that crams everything into one screen, making candles invisible).
  // On the 60s auto-refresh / background deep-load (keepView) we leave the view untouched.
  if (!keepView) chart.timeScale().scrollToRealTime();
  return bars.length;
}
// extend the chart months into the past WITHOUT moving the current view
async function loadHistory() { try { await loadChart(true, true); } catch (e) {} }
// keep only the newest candle(s) live, so the loaded deep history isn't wiped each minute
async function tickCandle() {
  try {
    if (STATIC) {
      const b = binSym(STATE.symbol); if (!b) return;
      const rows = await binFetch("klines", { symbol: b, interval: TF2IV[STATE.tf] || "5m", limit: 2 }, 6000);
      rows.forEach((k) => candles.update({ time: Math.floor(k[0] / 1000), open: +k[1], high: +k[2], low: +k[3], close: +k[4] }));
      return;
    }
    const d = await api(`/api/ohlcv?symbol=${STATE.symbol}&tf=${STATE.tf}&limit=3`);
    (d.bars || []).forEach((b) => {
      const t = Math.floor(Date.parse(b.time.replace(" ", "T") + "Z") / 1000);
      if (Number.isFinite(t)) candles.update({ time: t, open: b.open, high: b.high, low: b.low, close: b.close });
    });
  } catch (e) {}
}

function drawSignalOverlays(sig) {
  clearOverlays();
  const colDem = "rgba(43,185,138,0.12)", colSup = "rgba(239,91,107,0.11)", colFL = "rgba(212,175,55,0.11)";
  STATE.zones = sig.zones || [];
  (sig.zones || []).forEach((z) => {
    const fill = z.src.startsWith("FL") ? colFL : (z.dir === "LONG" ? colDem : colSup);
    const lc = z.dir === "LONG" ? "rgba(43,185,138,.8)" : "rgba(239,91,107,.8)";
    const rk = (z.risk && z.risk.level) || (z.risk_rating && z.risk_rating.level) || "";
    const label = `${z.action_fa || (z.dir === "LONG" ? "خرید" : "فروش")} · ${z.src}` + (rk ? ` · ریسک ${rk}` : "");
    band(z.top, z.bot, fill, label, lc);
  });
  drawZoneLines(sig.primary);   // primary by default; clicking another zone re-draws its levels
  // RSI line + a "now" marker line so the user sees exactly where RSI sits vs 30/70
  rsiSeries.setData((sig.rsi && sig.rsi.series) ? sig.rsi.series : []);
  if (rsiNowLine) { try { rsiSeries.removePriceLine(rsiNowLine); } catch (e) {} rsiNowLine = null; }
  const rv = sig.rsi && sig.rsi.last;
  if (rv != null) {
    const col = rv >= 70 ? C.red : rv <= 30 ? C.green : C.gold;
    rsiNowLine = rsiSeries.createPriceLine({ price: rv, color: col, lineWidth: 2, lineStyle: 0,
      axisLabelVisible: false });
  }
  rsiLabels.setNow(rv != null ? rv : null);   // in-pane text: ۷۰/۳۰/۵۰ + current RSI
  // RSI divergence markers on price
  const marks = (sig.divergences || []).map((d) => ({
    time: d.time, position: d.type === "bull" ? "belowBar" : "aboveBar",
    color: d.type === "bull" ? C.green : C.red,
    shape: d.type === "bull" ? "arrowUp" : "arrowDown", text: "RSI" }));
  candles.setMarkers(marks);
  // trend projection (start at the last candle so it connects to price)
  if (sig.projection && sig.projection.points && sig.projection.points.length) {
    const lt = STATE.candleTimes[STATE.candleTimes.length - 1];
    const start = (lt != null) ? [{ time: lt, value: sig.price }] : [];
    projSeries.setData([...start, ...sig.projection.points]);
    // mark each contact point: REACT (bounce/return) vs BREAK (pierce + continue)
    const evMarks = (sig.projection.events || []).map((e) => ({
      time: e.time,
      position: e.type === "bounce" ? "aboveBar" : "belowBar",
      color: e.type === "bounce" ? C.gold : C.blue,
      shape: e.type === "bounce" ? "circle" : "arrowDown",
      text: e.type === "bounce" ? "واکنش" : "شکست",
    }));
    projSeries.setMarkers(evMarks);
  } else { projSeries.setData([]); projSeries.setMarkers([]); }
  renderVerdict(sig.verdict);
}

/* ---------- headline verdict ---------- */
function renderVerdict(v) {
  const el = $("#verdict");
  if (!v) { el.hidden = true; return; }
  el.hidden = false;
  el.className = "verdict " + v.state + (v.reversal ? " reversal" : "");
  let tag = { BUY_NOW: "بخر", SELL_NOW: "بفروش", WAIT: "صبر کن" }[v.state] || "—";
  if (v.reversal) tag += " · بازگشتی";
  el.innerHTML = `<span class="tag">${tag}</span><span class="vtext">${(v.text || "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")}</span>`;
}

/* ---------- click a zone -> explain + TP/SL + risk ---------- */
function fmtP(x) { return x == null ? "—" : fmt(x); }
function showZonePop(z, xpx, ypx) {
  const pop = $("#zonePop");
  const rk = (z.risk_rating && z.risk_rating.level) || z.risk_level || "—";
  const reasons = (z.risk_rating && z.risk_rating.reasons) || [];
  const buy = z.dir === "LONG";
  pop.innerHTML = `<span class="close">×</span>
    <h4><span class="pill ${buy ? "buy" : "sell"}">${z.action_fa || (buy ? "خرید" : "فروش")}</span>
        <span class="pill r-${rk}">ریسک ${rk}</span></h4>
    <div class="row"><span>منبع</span><b>${z.src} · g${z.grade}</b></div>
    <div class="row"><span>محدوده</span><b>${fmtP(z.bot)}–${fmtP(z.top)}</b></div>
    <div class="row"><span>ورود</span><b>${fmtP(z.entry)}</b></div>
    <div class="row"><span>استاپ</span><b style="color:var(--red)">${fmtP(z.sl)}</b></div>
    <div class="row"><span>هدف TP2 (2R)</span><b style="color:var(--green)">${fmtP(z.tp2)}</b></div>
    <div class="row"><span>اعتماد / فاصله</span><b>${z.confidence} · ${z.dist_atr ?? "—"} ATR</b></div>
    ${z.combo_score != null ? `<div class="row"><span>تلفیقِ سبک‌ها</span><b>${z.combo_score}/3${z.combo_confirmed ? " ✅" : ""}</b></div>` : ""}
    ${reasons.length ? `<div class="rk">دلایلِ ریسک: ${reasons.join("، ")}</div>` : ""}`;
  pop.hidden = false;
  const cw = chartEl.clientWidth, ch = chartEl.clientHeight;
  pop.style.left = Math.min(Math.max(8, xpx - 135), cw - 280) + "px";
  pop.style.top = Math.min(Math.max(8, ypx + 10), ch - 190) + "px";
  pop.querySelector(".close").onclick = () => { pop.hidden = true; };
}
chartEl.addEventListener("click", (e) => {
  const pop = $("#zonePop");
  if (e.target.closest(".zone-pop")) return;            // clicking inside the popover
  if (!STATE.zones || !STATE.zones.length) { pop.hidden = true; return; }
  const rect = chartEl.getBoundingClientRect();
  const y = e.clientY - rect.top, x = e.clientX - rect.left;
  const price = candles.coordinateToPrice(y);
  if (price == null) { pop.hidden = true; return; }
  let hit = STATE.zones.find((z) => price >= z.bot && price <= z.top);
  if (!hit) {
    hit = STATE.zones.slice().sort((a, b) =>
      Math.min(Math.abs(price - a.top), Math.abs(price - a.bot)) -
      Math.min(Math.abs(price - b.top), Math.abs(price - b.bot)))[0];
    const d = Math.min(Math.abs(price - hit.top), Math.abs(price - hit.bot));
    if (d > (STATE.lastSig && STATE.lastSig.atr || 0) * 1.0) { pop.hidden = true; return; }  // empty space
  }
  drawZoneLines(hit);   // move entry/SL/TP lines to the clicked zone
  showZonePop(hit, x, y);
});

/* ---------- assistant rendering ---------- */
function mdToHtml(t) {
  return t
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/^##\s*(.+)$/gm, '<span class="md-h">$1</span>')   // section heading
    .replace(/^———+$/gm, '<hr class="rule">')                   // divider
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/_(.+?)_/g, "<em>$1</em>");
}
async function analyze() {
  $("#assistant").innerHTML = "در حال تحلیل…";
  const d = await api(`/api/assistant?symbol=${STATE.symbol}&tf=${STATE.tf}`);
  if (d.error) { $("#assistant").textContent = "خطا: " + d.error; return; }
  STATE.lastSig = d.signal;
  $("#assistant").innerHTML = mdToHtml(d.text);
  drawSignalOverlays(d.signal);
  // learned win-rate note
  const pr = d.signal.primary;
  if (pr && pr.learned_n > 0) {
    $("#assistant").innerHTML += `<span class="md-h">از تریدهای واقعیِ خودت</span>نرخِ بردِ این نوع ستاپ ≈ <strong>${Math.round(pr.learned_wr*100)}٪</strong> (روی ${pr.learned_n} معامله)`;
  }
}

/* ---------- quote ticker ---------- */
async function tickQuote() {
  if (!STATE.symbol) return;
  try {
    const q = await api(`/api/quote?symbol=${STATE.symbol}`);
    if (q.price != null) $("#livePrice").textContent = fmt(q.price);
    $("#delayedTag").hidden = !q.delayed;
  } catch (e) {}
}

/* ---------- journal ---------- */
async function loadJournal() {
  const d = await api("/api/journal");
  const list = $("#journalList"); list.innerHTML = "";
  (d.entries || []).slice().reverse().forEach((e) => {
    const row = document.createElement("div"); row.className = "jrow";
    const closed = e.status === "CLOSED" || e.status === "SKIPPED";
    row.innerHTML = `<div class="meta">
        <span class="sym">${e.symbol} · ${e.dir === "LONG" ? "خرید" : "فروش"} <span class="badge ${e.confidence}">${e.confidence}</span></span>
        <span class="sub">${e.tf} · ${e.src} · ورود ${fmt(e.entry)} · ${e.ts}${e.outcome ? ` · <b class="oc-${e.outcome}">` + faOutcome(e.outcome) + "</b>" : ""}</span>
      </div>`;
    if (!closed) {
      const acts = document.createElement("div"); acts.className = "acts";
      acts.innerHTML = `<button class="win">برد</button><button class="loss">باخت</button><button>رد</button>`;
      acts.children[0].onclick = () => mark(e.id, "WIN");
      acts.children[1].onclick = () => mark(e.id, "LOSS");
      acts.children[2].onclick = () => mark(e.id, "SKIP");
      row.appendChild(acts);
    } else { const t = document.createElement("span"); t.className = "tag-closed"; t.textContent = "✓"; row.appendChild(t); }
    list.appendChild(row);
  });
  const o = d.learn && d.learn.overall;
  $("#journalStats").textContent = o && o.closed ?
    `${o.closed} معامله · برد ${Math.round((o.win_rate||0)*100)}٪ · شبیه‌سازی $${o.sim_balance}` : "هنوز معامله‌ای ثبت نشده";
}
function faOutcome(o) { return o === "WIN" ? "برد" : o === "LOSS" ? "باخت" : "رد شد"; }
async function mark(id, outcome) { await post("/api/journal", { action: "outcome", id, outcome }); toast(`ثبت شد: ${faOutcome(outcome)} — سیستم از این نتیجه یاد گرفت`); loadJournal(); }
async function logSetup() {
  // include the primary setup so the static (PHP/MySQL) journal can store it without a backend
  const setup = STATE.lastSig && STATE.lastSig.primary;
  const r = await post("/api/journal", { action: "log", symbol: STATE.symbol, tf: STATE.tf, setup });
  if (r.ok) { toast("ستاپ در ژورنال ثبت شد"); loadJournal(); }
  else toast("ستاپِ معتبری برای ثبت نبود");
}

/* ---------- misc ---------- */
let toastT;
function toast(m) { const t = $("#toast") || (() => { const e = document.createElement("div"); e.id = "toast"; e.className = "toast"; document.body.appendChild(e); return e; })(); t.textContent = m; t.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 2600); }

async function reload(analyzeToo) {
  $("#livePrice").textContent = "…";            // clear stale price immediately on switch
  if (analyzeToo) $("#assistant").textContent = "در حال تحلیل…";  // don't show prev symbol's analysis
  tickQuote();                                  // refresh the quote now (don't wait for the 5s tick)
  await loadChart(false);                       // fast recent view first (instant)
  loadHistory();                                // then extend months into the past in the background
  // health badge for the active TF
  const sig = await api(`/api/signal?symbol=${STATE.symbol}&tf=${STATE.tf}`);
  if (!sig.error) {
    STATE.lastSig = sig;
    drawSignalOverlays(sig);
    const h = sig.tf_health;
    $("#tfHealth").innerHTML = `سلامتِ تایم‌فریم: <b>${STATE.tf}</b> — ${h.note}`;
    document.querySelectorAll("#tfGroup button").forEach((b) => {
      const dot = b.querySelector(".dot"); if (dot) dot.className = "dot " + (b.dataset.tf === STATE.tf ? h.color : "");
    });
  }
  if (analyzeToo) analyze();
  loadJournal();
}

(async function init() {
  await loadSymbols();
  await loadTFs();
  $("#analyzeBtn").onclick = analyze;
  $("#logBtn").onclick = logSetup;
  $("#refreshBtn").onclick = () => reload(true);
  await reload(true);
  setInterval(tickQuote, 5000);
  tickQuote();
  setInterval(() => tickCandle(), 60000);      // update only the newest candle (keeps deep history)
})();
