#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-running forward-test tracker for the RTM-SMC Auto zones.
Models the user's exact playbook: measured-move grid (W=zone width), SL=2W (slBreath=1),
TP1/2/3 = 1W/1.5W(gold)/3W, 1/3 scale-out each, BE after TP1.

Ledger : journal/forward_demo.json   (persistent state across ticks)
Live    : backtest/live_xau_m5.csv    (refreshed each tick from the live chart)
Journal : journal/FORWARD_TESTS.md    (human-readable log of resolutions + learnings)

Commands:
  init  '<zones-json>'   -> create ledger from current zones (PENDING)
  check                  -> evaluate open setups vs live CSV, update ledger + journal, print report
  status                 -> print the ledger
  learn                  -> aggregate resolved setups -> stats + lessons appended to LEARNINGS
"""
import json, os, sys, csv, datetime as dt

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYM  = os.environ.get("FD_SYM", "XAUUSD")   # per-symbol ledgers so styles/symbols don't mix
TAG  = os.environ.get("FD_TAG", "")          # optional ledger suffix (e.g. v4) to keep rule-sets separate
_lname = "forward_demo" + ("" if SYM == "XAUUSD" else f"_{SYM}") + (f"_{TAG}" if TAG else "")
LEDGER = os.path.join(BASE, "journal", _lname + ".json")
LIVE   = os.path.join(BASE, "backtest", "live_xau_m5.csv" if SYM == "XAUUSD" else f"live_{SYM.lower()}_m5.csv")
JOURNAL= os.path.join(BASE, "journal", "FORWARD_TESTS.md")

def _load():
    if not os.path.exists(LEDGER): return {"setups": [], "last_ts": 0, "meta": {}}
    with open(LEDGER) as f: return json.load(f)

def _save(d):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w") as f: json.dump(d, f, indent=2)

def _grid(z, slB):
    # TPs are RISK-multiples (1R/2R/3R) so RR_TP3 = 3.0 by construction.
    lo, hi = float(z["lo"]), float(z["hi"]); W = hi - lo
    if z["dir"] == "short":
        entry = lo; sl = hi + slB*W; risk = sl - entry
        tp = [entry - 1.0*risk, entry - 2.0*risk, entry - 3.0*risk]
    else:
        entry = hi; sl = lo - slB*W; risk = entry - sl
        tp = [entry + 1.0*risk, entry + 2.0*risk, entry + 3.0*risk]
    return dict(W=round(W,3), entry=round(entry,3), sl=round(sl,3),
                tp1=round(tp[0],3), tp2=round(tp[1],3), tp3=round(tp[2],3))

# realized R for 1/3 scale-out with BE after TP1. risk = |entry-sl| (=2W).
def _realized_R(s, best_tp, stopped):
    risk = abs(s["entry"] - s["sl"])
    if risk == 0: return 0.0
    def rmul(px): return (s["entry"]-px)/risk if s["dir"]=="short" else (px-s["entry"])/risk
    r = 0.0
    if best_tp >= 1: r += (1/3.0)*rmul(s["tp1"])
    if best_tp >= 2: r += (1/3.0)*rmul(s["tp2"])
    if best_tp >= 3: r += (1/3.0)*rmul(s["tp3"])
    # remaining unfilled thirds:
    rem = (3 - best_tp)/3.0
    if stopped:
        # BE after TP1 -> remaining exit at entry (0R) if best_tp>=1, else full -1R on the whole position
        if best_tp == 0: r = -1.0
        # else remaining thirds exit at BE (0R), already not added
    return round(r, 3)

def cmd_init(zones_json):
    z = json.loads(zones_json)
    slB = float(z.get("slBreath", 0.5))
    d = _load()
    existing = {(s["dir"], s["lo"], s["hi"]) for s in d["setups"]}
    added = 0
    for zone in z["zones"]:
        key = (zone["dir"], float(zone["lo"]), float(zone["hi"]))
        if key in existing: continue
        # explicit structure TPs from the v4 indicator take precedence over recomputed grid
        if all(k in zone for k in ("entry","sl","tp1","tp2","tp3")):
            g = dict(W=round(float(zone["hi"])-float(zone["lo"]),3),
                     entry=float(zone["entry"]), sl=float(zone["sl"]),
                     tp1=float(zone["tp1"]), tp2=float(zone["tp2"]), tp3=float(zone["tp3"]))
        else:
            g = _grid(zone, slB)
        d["setups"].append(dict(
            id=f"FD-{len(d['setups'])+1:03d}", dir=zone["dir"],
            lo=float(zone["lo"]), hi=float(zone["hi"]),
            grade=zone.get("grade"), src=zone.get("src","chart"), tier=zone.get("tier","-"),
            **g, status="PENDING", best_tp=0,
            created_ts=int(z["ts"]), created_price=float(z["price"]),
            trig_ts=None, resolved_ts=None, result=None, realizedR=None, mfe=None, mae=None))
        added += 1
    d["meta"] = dict(symbol=z.get("symbol","XAUUSD"), gold=z.get("gold",True),
                     slBreath=slB, mode=z.get("mode","grid"))
    if d["last_ts"] == 0: d["last_ts"] = int(z["ts"])
    _save(d)
    print(f"init: +{added} setups (total {len(d['setups'])}). R-based TPs (RR_TP3=3.0) slBreath={slB}")

def cmd_regrid(slB=None):
    """Recompute entry/SL/TP for setups still PENDING (keeps resolved history)."""
    d = _load()
    if slB is None: slB = d.get("meta",{}).get("slBreath", 0.5)
    slB = float(slB); n = 0
    for s in d["setups"]:
        if s["status"] != "PENDING": continue
        g = _grid({"dir": s["dir"], "lo": s["lo"], "hi": s["hi"]}, slB)
        s.update(g); n += 1
    d.setdefault("meta", {})["slBreath"] = slB
    _save(d)
    print(f"regrid: updated {n} PENDING setups to R-based TPs, slBreath={slB}")

def _read_bars():
    if not os.path.exists(LIVE): return []
    bars = []
    with open(LIVE) as f:
        r = csv.reader(f, delimiter="\t" if "\t" in f.readline() else ",")
        f.seek(0); first = f.readline(); delim = "\t" if "\t" in first else ","
        f.seek(0); r = csv.reader(f, delimiter=delim)
        for i, row in enumerate(r):
            if i == 0 and not row[0].replace("-","").replace(":","").replace(" ","").isdigit() and "Time" in row[0]:
                continue
            if len(row) < 5: continue
            try:
                t = row[0]
                # accept unix int or ISO
                ts = int(t) if t.isdigit() else int(dt.datetime.fromisoformat(t.replace("Z","")).timestamp())
                bars.append(dict(ts=ts, o=float(row[1]), h=float(row[2]), l=float(row[3]), c=float(row[4])))
            except Exception:
                continue
    bars.sort(key=lambda b: b["ts"])
    return bars

def cmd_check():
    d = _load(); bars = _read_bars()
    if not bars:
        print("check: no live bars found at "+LIVE); return
    mode = d.get("meta", {}).get("mode", "grid")
    newlog = []
    for s in d["setups"]:
        if s["status"] in ("WIN","FAIL","WIN_PARTIAL"): continue
        # bars after this setup's creation (or after trigger)
        start = s["trig_ts"] or s["created_ts"]
        rel = [b for b in bars if b["ts"] >= s["created_ts"]]
        if not rel: continue
        short = s["dir"] == "short"
        # update MFE/MAE in price terms
        for b in rel:
            just_trig = False
            if s["status"] == "PENDING":
                touched = (b["h"] >= s["entry"]) if short else (b["l"] <= s["entry"])
                if touched:
                    s["status"] = "TRIGGERED"; s["trig_ts"] = b["ts"]; just_trig = True
            # don't resolve TP/SL on the trigger bar itself (intrabar order is unknown)
            if s["status"] == "TRIGGERED" and not just_trig and mode == "single2R":
                # validated edge: single take-profit at 2R (=tp2), single SL; SL-first on tie
                stop_hit = (b["h"] >= s["sl"]) if short else (b["l"] <= s["sl"])
                tp_hit   = (b["l"] <= s["tp2"]) if short else (b["h"] >= s["tp2"])
                if stop_hit:
                    s["status"] = "FAIL"; s["result"] = ("SL+TP same bar" if tp_hit else "SL")
                    s["realizedR"] = -1.0; s["resolved_ts"] = b["ts"]; break
                if tp_hit:
                    s["status"] = "WIN"; s["result"] = "TP(2R)"
                    s["realizedR"] = 2.0; s["resolved_ts"] = b["ts"]; break
            elif s["status"] == "TRIGGERED" and not just_trig and mode == "single1to1":
                # single TP at tp1 (=1:1), single SL; conservative SL-first on same-bar tie
                stop_hit = (b["h"] >= s["sl"]) if short else (b["l"] <= s["sl"])
                tp_hit   = (b["l"] <= s["tp1"]) if short else (b["h"] >= s["tp1"])
                if stop_hit:
                    s["status"] = "FAIL"; s["result"] = ("SL+TP same bar" if tp_hit else "SL")
                    s["realizedR"] = -1.0; s["resolved_ts"] = b["ts"]; break
                if tp_hit:
                    s["status"] = "WIN"; s["result"] = "TP(1:1)"
                    s["realizedR"] = 1.0; s["resolved_ts"] = b["ts"]; break
            elif s["status"] == "TRIGGERED" and not just_trig:
                # progress TPs (favorable extreme of the bar)
                fav = b["l"] if short else b["h"]
                while s["best_tp"] < 3:
                    nxt = s[f"tp{s['best_tp']+1}"]
                    hit = (fav <= nxt) if short else (fav >= nxt)
                    if hit: s["best_tp"] += 1
                    else: break
                # effective stop: BE after TP1
                eff_sl = s["entry"] if s["best_tp"] >= 1 else s["sl"]
                adv = b["h"] if short else b["l"]
                stopped = (adv >= eff_sl) if short else (adv <= eff_sl)
                if s["best_tp"] >= 3:
                    s["status"] = "WIN"; s["resolved_ts"] = b["ts"]
                    s["realizedR"] = _realized_R(s, 3, False); s["result"] = "TP3"; break
                if stopped:
                    if s["best_tp"] == 0:
                        s["status"] = "FAIL"; s["result"] = "SL"
                    else:
                        s["status"] = "WIN_PARTIAL"; s["result"] = f"TP{s['best_tp']}+BE"
                    s["resolved_ts"] = b["ts"]; s["realizedR"] = _realized_R(s, s["best_tp"], True); break
        if s["status"] in ("WIN","FAIL","WIN_PARTIAL") and s["id"] not in d.get("logged", []):
            newlog.append(s)
    d["last_ts"] = bars[-1]["ts"]
    logged = set(d.get("logged", []))
    for s in newlog: logged.add(s["id"])
    d["logged"] = sorted(logged)
    _save(d)
    # journal append
    if newlog:
        os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
        with open(JOURNAL, "a") as f:
            for s in newlog:
                ts = dt.datetime.utcfromtimestamp(s["resolved_ts"]).strftime("%Y-%m-%d %H:%M")
                ic = {"WIN":"✅", "WIN_PARTIAL":"🟡", "FAIL":"❌"}[s["status"]]
                verdict = ic + " " + str(s.get("result",""))
                f.write(f"\n- **{s['id']}** {s['dir'].upper()} zone {s['lo']}-{s['hi']} g{s['grade']} [{s['src']}] "
                        f"entry {s['entry']} SL {s['sl']} TP {s['tp1']}/{s['tp2']}/{s['tp3']} "
                        f"→ {verdict} @ {ts} UTC | realized {s['realizedR']:+}R")
    # report
    cnt = {}
    for s in d["setups"]: cnt[s["status"]] = cnt.get(s["status"],0)+1
    print("check @ bar", dt.datetime.utcfromtimestamp(bars[-1]["ts"]).strftime("%Y-%m-%d %H:%M"),
          "close", bars[-1]["c"])
    print("  states:", dict(sorted(cnt.items())))
    if newlog:
        print("  NEW resolutions:")
        for s in newlog:
            print(f"    {s['id']} {s['dir']} {s['lo']}-{s['hi']} g{s['grade']} [{s['src']}] -> {s['status']} ({s['result']}) {s['realizedR']:+}R")
    else:
        print("  no new resolutions this tick")

def cmd_status():
    d = _load()
    print(f"=== forward_demo ledger ({len(d['setups'])} setups) ===")
    for s in d["setups"]:
        print(f"  {s['id']} {s['dir']:5s} {s['lo']}-{s['hi']} g{s['grade']} [{s['src']:5s}] "
              f"E{s['entry']} SL{s['sl']} TP{s['tp1']}/{s['tp2']}/{s['tp3']} | {s['status']}"
              + (f" {s['result']} {s['realizedR']:+}R" if s.get('result') else ""))

def cmd_learn():
    d = _load()
    res = [s for s in d["setups"] if s["status"] in ("WIN","FAIL","WIN_PARTIAL")]
    if not res:
        print("learn: no resolved setups yet"); return
    def agg(items):
        n = len(items);
        if n == 0: return None
        wins = [s for s in items if s["status"] in ("WIN","WIN_PARTIAL")]
        R = sum(s["realizedR"] for s in items)
        return dict(n=n, wr=round(100*len(wins)/n,1), netR=round(R,2), expR=round(R/n,3))
    overall = agg(res)
    by_grade = {g: agg([s for s in res if s["grade"]==g]) for g in sorted({s["grade"] for s in res})}
    by_dir   = {dd: agg([s for s in res if s["dir"]==dd]) for dd in sorted({s["dir"] for s in res})}
    by_src   = {sc: agg([s for s in res if s["src"]==sc]) for sc in sorted({s["src"] for s in res})}
    print("=== LEARN (resolved forward tests) ===")
    print(" overall:", overall)
    print(" by grade:", by_grade)
    print(" by dir  :", by_dir)
    print(" by src  :", by_src)
    return overall, by_grade, by_dir, by_src

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "init": cmd_init(sys.argv[2])
    elif cmd == "setmode":
        d = _load(); d.setdefault("meta", {})["mode"] = sys.argv[2]
        # switching exit logic invalidates prior resolutions -> reset all to PENDING
        for s in d["setups"]:
            s.update(status="PENDING", best_tp=0, trig_ts=None, resolved_ts=None, result=None, realizedR=None)
        d["logged"] = []
        _save(d)
        print(f"mode set to {sys.argv[2]}; reset {len(d['setups'])} setups to PENDING")
    elif cmd == "regrid": cmd_regrid(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "check": cmd_check()
    elif cmd == "learn": cmd_learn()
    else: cmd_status()
