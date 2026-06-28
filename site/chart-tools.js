/* ============================================================================
   chart-tools.js — TradingView-style drawing tools + indicators on Lightweight
   Charts, layered on top of the RTM engine overlays (zones/signals stay intact).
   Reads window.RTM = {chart, candles, chartEl, C, LWC} exported by app.js.
   Drawings persist per-user in MySQL via drawings.php; indicator choices in
   localStorage. Everything is guarded so a tool error never breaks the chart.
   ============================================================================ */
(function () {
  "use strict";
  function boot() {
    var R = window.RTM;
    if (!R || !R.chart || !R.candles) { return setTimeout(boot, 120); }
    var chart = R.chart, candles = R.candles, chartEl = R.chartEl, C = R.C;
    var CSRF = window.RTM_CSRF || "";
    var state = { bars: [], symbol: null, tf: null, tool: null, drawings: [], sel: null, temp: null };

    /* ---------------- indicator math ---------------- */
    function SMA(v, p) { var o = [], s = 0; for (var i = 0; i < v.length; i++) { s += v[i]; if (i >= p) s -= v[i - p]; o.push(i >= p - 1 ? s / p : null); } return o; }
    function EMA(v, p) { var o = [], k = 2 / (p + 1), e = null; for (var i = 0; i < v.length; i++) { e = (e == null) ? v[i] : v[i] * k + e * (1 - k); o.push(i >= p - 1 ? e : null); } return o; }
    function STD(v, p, ma) { var o = []; for (var i = 0; i < v.length; i++) { if (i < p - 1) { o.push(null); continue; } var s = 0; for (var j = i - p + 1; j <= i; j++) s += Math.pow(v[j] - ma[i], 2); o.push(Math.sqrt(s / p)); } return o; }
    function MACD(v, f, s, sig) { var ef = EMA(v, f), es = EMA(v, s), line = v.map(function (_, i) { return (ef[i] == null || es[i] == null) ? null : ef[i] - es[i]; }); var lv = line.map(function (x) { return x == null ? 0 : x; }), sg = EMA(lv, sig); var signal = line.map(function (x, i) { return x == null ? null : sg[i]; }); var hist = line.map(function (x, i) { return (x == null || signal[i] == null) ? null : x - signal[i]; }); return { line: line, signal: signal, hist: hist }; }
    function STOCH(h, l, c, p, d) { var k = []; for (var i = 0; i < c.length; i++) { if (i < p - 1) { k.push(null); continue; } var hi = -Infinity, lo = Infinity; for (var j = i - p + 1; j <= i; j++) { hi = Math.max(hi, h[j]); lo = Math.min(lo, l[j]); } k.push(hi === lo ? 50 : 100 * (c[i] - lo) / (hi - lo)); } var kv = k.map(function (x) { return x == null ? 0 : x; }), dd = SMA(kv, d), D = k.map(function (x, i) { return x == null ? null : dd[i]; }); return { k: k, d: D }; }
    function ATR(h, l, c, p) { var tr = []; for (var i = 0; i < c.length; i++) { tr.push(i === 0 ? h[i] - l[i] : Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1]))); } return EMA(tr, p); }
    function VWAP(h, l, c, vol) { var o = [], cumPV = 0, cumV = 0; var hasV = vol && vol.some(function (x) { return x > 0; }); for (var i = 0; i < c.length; i++) { var tp = (h[i] + l[i] + c[i]) / 3, v = hasV ? (vol[i] || 0) : 1; cumPV += tp * v; cumV += v; o.push(cumV ? cumPV / cumV : null); } return o; }

    /* ---------------- indicators: state + rendering ---------------- */
    // overlay = on price scale; osc = single bottom oscillator slot
    var IND = {
      ema9: { name: "EMA ۹", kind: "overlay", on: false, mk: function (b) { return line(EMA(b.c, 9), "#5b8def"); } },
      ema21: { name: "EMA ۲۱", kind: "overlay", on: false, mk: function (b) { return line(EMA(b.c, 21), "#e0a83a"); } },
      ema50: { name: "EMA ۵۰", kind: "overlay", on: false, mk: function (b) { return line(EMA(b.c, 50), "#a78bfa"); } },
      sma200: { name: "SMA ۲۰۰", kind: "overlay", on: false, mk: function (b) { return line(SMA(b.c, 200), "#9aa3b2"); } },
      bb: { name: "Bollinger ۲۰", kind: "overlay", on: false, mk: function (b) { var m = SMA(b.c, 20), sd = STD(b.c, 20, m); return [["#26a69a55", m.map(function (x, i) { return x == null ? null : x + 2 * sd[i]; })], ["#9aa3b2", m], ["#26a69a55", m.map(function (x, i) { return x == null ? null : x - 2 * sd[i]; })]].map(function (p) { return line(p[1], p[0]); }); } },
      vwap: { name: "VWAP", kind: "overlay", on: false, mk: function (b) { return line(VWAP(b.h, b.l, b.c, b.v), "#d4af37"); } },
      macd: { name: "MACD", kind: "osc", on: false },
      stoch: { name: "Stochastic", kind: "osc", on: false },
      atr: { name: "ATR ۱۴", kind: "osc", on: false },
      vol: { name: "حجم (Volume)", kind: "osc", on: false },
    };
    var overlaySeries = [], oscSeries = [], oscScaleId = "ind-osc";

    function line(arr, color) {
      var s = chart.addLineSeries({ color: color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(toPts(arr)); return s;
    }
    function toPts(arr) { var o = []; for (var i = 0; i < arr.length; i++) { if (arr[i] != null && isFinite(arr[i]) && state.bars[i]) o.push({ time: state.bars[i].time, value: arr[i] }); } return o; }

    function clearInds() {
      overlaySeries.forEach(function (s) { try { chart.removeSeries(s); } catch (e) {} });
      oscSeries.forEach(function (s) { try { chart.removeSeries(s); } catch (e) {} });
      overlaySeries = []; oscSeries = [];
    }
    function renderInds() {
      clearInds();
      var b = barsCols(); if (!b.c.length) return;
      var oscActive = null;
      Object.keys(IND).forEach(function (k) {
        var ind = IND[k]; if (!ind.on) return;
        if (ind.kind === "overlay") { var r = ind.mk(b); (Array.isArray(r) ? r : [r]).forEach(function (s) { overlaySeries.push(s); }); }
        else if (ind.kind === "osc" && !oscActive) { oscActive = k; }
      });
      if (oscActive) renderOsc(oscActive, b); else chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.06, bottom: 0.27 } });
    }
    function oscLine(arr, color, w) { var s = chart.addLineSeries({ priceScaleId: oscScaleId, color: color, lineWidth: w || 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }); s.setData(toPts(arr)); oscSeries.push(s); return s; }
    function oscHist(arr, color) { var s = chart.addHistogramSeries({ priceScaleId: oscScaleId, priceLineVisible: false, lastValueVisible: false }); s.setData((function () { var o = []; for (var i = 0; i < arr.length; i++) { if (arr[i] != null && state.bars[i]) o.push({ time: state.bars[i].time, value: arr[i], color: arr[i] >= 0 ? "rgba(43,185,138,.5)" : "rgba(239,91,107,.5)" }); } return o; })()); oscSeries.push(s); return s; }
    function renderOsc(k, b) {
      // make room: shrink price pane, place oscillator in a mid-bottom band above RSI
      chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.06, bottom: 0.42 } });
      chart.priceScale(oscScaleId).applyOptions({ scaleMargins: { top: 0.60, bottom: 0.16 } });
      if (k === "macd") { var m = MACD(b.c, 12, 26, 9); oscHist(m.hist); oscLine(m.line, "#5b8def"); oscLine(m.signal, "#e0a83a"); }
      else if (k === "stoch") { var s = STOCH(b.h, b.l, b.c, 14, 3); oscLine(s.k, "#5b8def"); oscLine(s.d, "#e0a83a"); }
      else if (k === "atr") { oscLine(ATR(b.h, b.l, b.c, 14), "#d4af37"); }
      else if (k === "vol") { var vs = chart.addHistogramSeries({ priceScaleId: oscScaleId, priceLineVisible: false, lastValueVisible: false }); vs.setData((function () { var o = []; for (var i = 0; i < b.c.length; i++) { if (state.bars[i]) o.push({ time: state.bars[i].time, value: b.v[i] || 0, color: (i > 0 && b.c[i] >= b.c[i - 1]) ? "rgba(43,185,138,.45)" : "rgba(239,91,107,.45)" }); } return o; })()); oscSeries.push(vs); }
    }
    function barsCols() { var b = state.bars; return { c: b.map(function (x) { return x.close; }), h: b.map(function (x) { return x.high; }), l: b.map(function (x) { return x.low; }), v: b.map(function (x) { return x.volume || 0; }) }; }

    /* ---------------- drawing layer (one primitive draws all shapes) ---------------- */
    function px(pt) { var x = chart.timeScale().timeToCoordinate(pt.time); var y = candles.priceToCoordinate(pt.price); return (x == null || y == null) ? null : { x: x, y: y }; }
    function DrawRenderer(layer) { this.l = layer; }
    DrawRenderer.prototype.draw = function (target) {
      var L = this.l;
      try {
        target.useBitmapCoordinateSpace(function (scope) {
          var ctx = scope.context, r = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio, W = scope.bitmapSize.width;
          var all = state.drawings.concat(state.temp ? [state.temp] : []);
          all.forEach(function (d) { drawOne(ctx, d, r, vr, W, d === state.sel); });
        });
      } catch (e) {}
    };
    function drawOne(ctx, d, r, vr, W, selected) {
      var col = d.style && d.style.color || "#d4af37", lw = (d.style && d.style.width || 1.6) * vr;
      ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = lw; ctx.setLineDash([]);
      var p = (d.points || []).map(px);
      if (d.type === "hline" && p[0]) { ctx.beginPath(); ctx.moveTo(0, p[0].y * vr); ctx.lineTo(W, p[0].y * vr); ctx.stroke(); tag(ctx, d.points[0].price, 6 * r, p[0].y * vr, col, vr); }
      else if (d.type === "vline" && p[0]) { ctx.beginPath(); ctx.moveTo(p[0].x * r, 0); ctx.lineTo(p[0].x * r, 9999); ctx.stroke(); }
      else if ((d.type === "trend" || d.type === "ray") && p[0] && p[1]) {
        var a = { x: p[0].x * r, y: p[0].y * vr }, b = { x: p[1].x * r, y: p[1].y * vr };
        if (d.type === "ray") { var dx = b.x - a.x, dy = b.y - a.y, t = dx !== 0 ? (W - a.x) / dx : 9999; b = { x: W, y: a.y + dy * t }; }
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        if (selected) { dot(ctx, a.x, a.y, col); dot(ctx, b.x, b.y, col); }
      }
      else if (d.type === "rect" && p[0] && p[1]) {
        var x1 = Math.min(p[0].x, p[1].x) * r, x2 = Math.max(p[0].x, p[1].x) * r, y1 = Math.min(p[0].y, p[1].y) * vr, y2 = Math.max(p[0].y, p[1].y) * vr;
        ctx.globalAlpha = 0.10; ctx.fillRect(x1, y1, x2 - x1, y2 - y1); ctx.globalAlpha = 1; ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
      }
      else if (d.type === "fib" && p[0] && p[1]) {
        var levels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1], hi = d.points[0].price, lo = d.points[1].price;
        var xL = Math.min(p[0].x, p[1].x) * r, xR = Math.max(p[0].x, p[1].x) * r;
        ctx.font = "700 " + Math.round(10 * vr) + "px IRANYekan, Tahoma"; ctx.textBaseline = "middle";
        levels.forEach(function (lv) { var price = hi - (hi - lo) * lv, y = candles.priceToCoordinate(price); if (y == null) return; y = y * vr; ctx.globalAlpha = 0.7; ctx.beginPath(); ctx.moveTo(xL, y); ctx.lineTo(xR, y); ctx.stroke(); ctx.globalAlpha = 1; ctx.fillText((lv * 100).toFixed(1) + "%  " + price.toFixed(2), xL + 4 * r, y - 7 * vr); });
      }
      else if (d.type === "text" && p[0]) { ctx.font = "700 " + Math.round(12 * vr) + "px IRANYekan, Tahoma"; ctx.textBaseline = "middle"; ctx.fillText(d.text || "متن", p[0].x * r, p[0].y * vr); }
    }
    function tag(ctx, price, x, y, col, vr) { ctx.font = "700 " + Math.round(10 * vr) + "px IRANYekan, Tahoma"; ctx.textBaseline = "middle"; var t = price.toFixed(2); ctx.globalAlpha = 0.85; ctx.fillText(t, x, y - 8 * vr); ctx.globalAlpha = 1; }
    function dot(ctx, x, y, col) { ctx.save(); ctx.fillStyle = "#fff"; ctx.strokeStyle = col; ctx.beginPath(); ctx.arc(x, y, 4, 0, 7); ctx.fill(); ctx.stroke(); ctx.restore(); }
    var DrawLayer = {
      _pv: { renderer: function () { return new DrawRenderer(DrawLayer); }, zOrder: function () { return "top"; }, update: function () {} },
      attached: function (p) { this._req = p.requestUpdate; }, detached: function () {},
      updateAllViews: function () {}, paneViews: function () { return [this._pv]; },
      redraw: function () { if (this._req) this._req(); },
    };
    candles.attachPrimitive(DrawLayer);

    /* ---------------- mouse capture overlay ---------------- */
    var ov = document.createElement("div"); ov.className = "draw-ov"; ov.style.cssText = "position:absolute;inset:0;z-index:5;display:none;cursor:crosshair";
    chartEl.style.position = "relative"; chartEl.appendChild(ov);
    function toPt(e) { var rect = ov.getBoundingClientRect(); var x = e.clientX - rect.left, y = e.clientY - rect.top; var time = chart.timeScale().coordinateToTime(x); var price = candles.coordinateToPrice(y); if (time == null) { var lt = state.bars.length ? state.bars[state.bars.length - 1].time : null; time = lt; } return (time == null || price == null) ? null : { time: time, price: price }; }
    var draft = null;
    ov.addEventListener("mousedown", function (e) {
      if (!state.tool) return;
      var pt = toPt(e); if (!pt) return;
      if (state.tool === "hline") { finish({ type: "hline", points: [pt] }); return; }
      if (state.tool === "vline") { finish({ type: "vline", points: [pt] }); return; }
      if (state.tool === "text") { var t = prompt("متنِ یادداشت:"); if (t) finish({ type: "text", points: [pt], text: t }); return; }
      draft = { type: ({ trend: "trend", ray: "ray", rect: "rect", fib: "fib" })[state.tool], points: [pt, pt], style: {} };
      state.temp = draft; DrawLayer.redraw();
    });
    ov.addEventListener("mousemove", function (e) { if (!draft) return; var pt = toPt(e); if (!pt) return; draft.points[1] = pt; DrawLayer.redraw(); });
    window.addEventListener("mouseup", function () { if (draft) { var d = draft; draft = null; state.temp = null; finish(d); } });

    function finish(d) { d.style = d.style || { color: "#d4af37", width: 1.6 }; state.drawings.push(d); DrawLayer.redraw(); save(d); setTool(null); }

    /* ---------------- persistence ---------------- */
    function save(d) {
      fetch("drawings.php", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF": CSRF }, body: JSON.stringify({ action: "save", symbol: state.symbol, tf: state.tf, type: d.type, data: { points: d.points, style: d.style, text: d.text } }) })
        .then(function (r) { return r.json(); }).then(function (r) { if (r && r.id) d.id = r.id; }).catch(function () {});
    }
    function loadDrawings() {
      state.drawings = []; state.sel = null;
      fetch("drawings.php?symbol=" + encodeURIComponent(state.symbol) + "&tf=" + encodeURIComponent(state.tf))
        .then(function (r) { return r.json(); }).then(function (j) {
          state.drawings = (j.drawings || []).map(function (x) { var d = x.data || {}; return { id: x.id, type: x.type, points: d.points || [], style: d.style || {}, text: d.text }; });
          DrawLayer.redraw();
        }).catch(function () {});
    }
    function delSel() { if (!state.sel) return; var d = state.sel; state.drawings = state.drawings.filter(function (x) { return x !== d; }); state.sel = null; DrawLayer.redraw(); if (d.id) fetch("drawings.php", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF": CSRF }, body: JSON.stringify({ action: "delete", id: d.id }) }).catch(function () {}); }
    function clearAll() { if (!confirm("همهٔ رسم‌های این نماد/تایم پاک شوند؟")) return; state.drawings = []; state.sel = null; DrawLayer.redraw(); fetch("drawings.php", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF": CSRF }, body: JSON.stringify({ action: "clear", symbol: state.symbol, tf: state.tf }) }).catch(function () {}); }

    /* ---------------- selection (click near a shape when no tool active) ----------------
       Uses the chart's own click subscription so it NEVER blocks pan/zoom. */
    chart.subscribeClick(function (param) {
      if (state.tool || !param || !param.point) return;
      var mx = param.point.x, my = param.point.y, best = null, bd = 9;
      state.drawings.forEach(function (d) { (d.points || []).map(px).forEach(function (q) { if (q) { var dist = Math.hypot(q.x - mx, q.y - my); if (dist < bd) { bd = dist; best = d; } } }); });
      state.sel = best; DrawLayer.redraw();
    });
    document.addEventListener("keydown", function (e) { if ((e.key === "Delete" || e.key === "Backspace") && state.sel) { e.preventDefault(); delSel(); } });

    // overlay captures gestures ONLY while a drawing tool is active; otherwise it's gone so the
    // chart pans/zooms normally and subscribeClick handles selection.
    function setTool(t) {
      state.tool = t;
      ov.style.display = t ? "block" : "none";
      ov.style.pointerEvents = t ? "auto" : "none";
      ov.style.cursor = t ? "crosshair" : "default";
      Array.prototype.forEach.call(document.querySelectorAll(".tool-btn"), function (b) { b.classList.toggle("active", b.dataset.tool === t); });
    }

    /* ---------------- toolbars UI ---------------- */
    function buildUI() {
      var tools = [["trend", "خط روند", "M3 17 9 11l4 3 8-8"], ["ray", "ری", "M4 12h14M14 8l5 4-5 4"], ["hline", "خط افقی", "M3 12h18"], ["vline", "خط عمودی", "M12 3v18"], ["rect", "مستطیل/ناحیه", "M4 6h16v12H4z"], ["fib", "فیبوناچی", "M3 6h18M3 10h18M3 14h18M3 18h18"], ["text", "متن", "M5 5h14M12 5v14"]];
      var bar = document.createElement("div"); bar.className = "draw-toolbar";
      tools.forEach(function (t) { var b = document.createElement("button"); b.className = "tool-btn"; b.dataset.tool = t[0]; b.title = t[1]; b.innerHTML = "<svg width=16 height=16 viewBox='0 0 24 24' fill=none stroke=currentColor stroke-width=2><path d='" + t[2] + "'/></svg>"; b.onclick = function () { setTool(state.tool === t[0] ? null : t[0]); }; bar.appendChild(b); });
      var del = document.createElement("button"); del.className = "tool-btn danger"; del.title = "حذفِ انتخاب‌شده"; del.innerHTML = "🗑"; del.onclick = delSel; bar.appendChild(del);
      var clr = document.createElement("button"); clr.className = "tool-btn"; clr.title = "پاک‌کردنِ همه"; clr.textContent = "⌫"; clr.onclick = clearAll; bar.appendChild(clr);
      chartEl.appendChild(bar);

      // indicators menu button lives in the header controls
      var btn = document.createElement("button"); btn.id = "indBtn"; btn.textContent = "ƒ اندیکاتورها";
      var menu = document.createElement("div"); menu.className = "ind-menu"; menu.hidden = true;
      Object.keys(IND).forEach(function (k) {
        var row = document.createElement("label"); row.className = "ind-row";
        var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = IND[k].on;
        cb.onchange = function () { if (IND[k].kind === "osc" && cb.checked) { Object.keys(IND).forEach(function (j) { if (IND[j].kind === "osc" && j !== k) { IND[j].on = false; } }); Array.prototype.forEach.call(menu.querySelectorAll("input"), function (x, i) {}); } IND[k].on = cb.checked; persistInds(); renderInds(); syncMenu(menu); };
        row.appendChild(cb); row.appendChild(document.createTextNode(" " + IND[k].name + (IND[k].kind === "osc" ? " ▾" : ""))); menu.appendChild(row);
      });
      btn.onclick = function (e) { e.stopPropagation(); menu.hidden = !menu.hidden; };
      document.addEventListener("click", function () { menu.hidden = true; });
      menu.onclick = function (e) { e.stopPropagation(); };
      var wrap = document.createElement("div"); wrap.className = "ind-wrap"; wrap.appendChild(btn); wrap.appendChild(menu);
      var controls = document.querySelector(".controls"); if (controls) controls.insertBefore(wrap, controls.firstChild);
      setTool(null);
    }
    function syncMenu(menu) { var inputs = menu.querySelectorAll("input"); Object.keys(IND).forEach(function (k, i) { if (inputs[i]) inputs[i].checked = IND[k].on; }); }
    function persistInds() { try { var on = Object.keys(IND).filter(function (k) { return IND[k].on; }); localStorage.setItem("rtm_inds", JSON.stringify(on)); } catch (e) {} }
    function restoreInds() { try { var on = JSON.parse(localStorage.getItem("rtm_inds") || "[]"); on.forEach(function (k) { if (IND[k]) IND[k].on = true; }); } catch (e) {} }

    /* ---------------- public hook: app.js calls on every candle load ---------------- */
    window.ChartTools = {
      onCandles: function (bars, symbol, tf) {
        state.bars = bars || [];
        var changed = (symbol !== state.symbol || tf !== state.tf);
        state.symbol = symbol; state.tf = tf;
        renderInds();
        if (changed) loadDrawings(); else DrawLayer.redraw();
      },
    };
    restoreInds(); buildUI();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
