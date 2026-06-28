#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deterministic Persian assistant — turns a signal_service plan into a readable
Farsi explanation. NO LLM: every sentence is templated from the structured fields,
so it is reproducible and free. It explains bias, the chosen zone and WHY, the
entry/SL/TP/RR, confluences, honesty caveats, and a clear "this is analysis, not an
order — you execute manually" footer.
"""

CONF_FA = {"HIGH": "بالا", "MEDIUM": "متوسط", "LOW": "پایین"}
SRC_FA = {
    "OB-1h": "اوردربلاکِ ۱ساعته (HTF — اج اثبات‌شده)",
    "OB-15m": "اوردربلاکِ ۱۵دقیقه‌ای",
    "FL-1h": "فلگ‌لیمیتِ ۱ساعته (منبعِ مکملِ RTM)",
}


def _fmt(x):
    if x is None:
        return "—"
    ax = abs(x)
    d = 2 if ax >= 100 else (4 if ax >= 1 else 6)
    return f"{x:,.{d}f}"


# normalize any Farsi/Arabic-Indic digits to Latin so the panel's numerals match the
# Latin price figures (one numeral system per panel — avoids the "mixed numerals" tell)
_LATIN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
def _ltn(s):
    return s.translate(_LATIN)


def narrate(sig):
    sym = sig["symbol"]; tf = sig["tf"]; price = sig["price"]
    L = []
    L.append(f"## {sym} — تایم‌فریم {tf}")
    L.append(f"قیمتِ لحظه‌ای: **{_fmt(price)}**")

    # bias
    bias = sig["bias"]
    pc = "صعودی" if sig["bias_val"] == 1 else ("نزولی" if sig["bias_val"] == -1 else "خنثی")
    dp = "تخفیف (Discount)" if sig["discount"] else ("پریمیوم (Premium)" if sig["premium"] else "میانهٔ رنج")
    L.append(f"سوگیریِ بازار (H1+H4+Daily): **{pc}** — قیمت در ناحیهٔ **{dp}**.")

    # TF honesty
    hc = sig["tf_health"]
    L.append(f"وضعیتِ این تایم‌فریم: _{hc['note']}_")

    # ---- HEADLINE: what to do right now ----
    v = sig.get("verdict") or {}
    head = {"BUY_NOW": "اقدامِ الان — آمادهٔ خرید",
            "SELL_NOW": "اقدامِ الان — آمادهٔ فروش",
            "WAIT": "اقدامِ الان — صبر کن"}.get(v.get("state"), "اقدامِ الان")
    L.append(f"\n## {head}")
    L.append(v.get("text", ""))

    # dominance backdrop (crypto)
    dom = sig.get("dominance")
    if dom:
        L.append(f"• دامیننسِ تتر: **{dom['usdt_d']}٪** (روند {dom['trend']}) — {dom['note']}")

    p = sig["primary"]
    if not p:
        L.append("\nهیچ ناحیهٔ معتبرِ فعالی نزدیکِ قیمت نیست. صبر کن تا ستاپ شکل بگیرد.")
        L.append(_footer())
        return _ltn("\n".join(L))

    dr_fa = "خرید (LONG)" if p["dir"] == "LONG" else "فروش (SHORT)"
    L.append(f"\n## بهترین ستاپ — {dr_fa}")
    L.append(f"• منبعِ ناحیه: {SRC_FA.get(p['src'], p['src'])} | کیفیت(grade): {p['grade']}/2")
    L.append(f"• محدودهٔ ناحیه: {_fmt(p['bot'])} تا {_fmt(p['top'])}")
    L.append(f"• اعتمادِ سیستم: **{CONF_FA[p['confidence']]}**" +
             (" — هم‌جهت با روند" if p["with_trend"] else " — خلافِ روند (محتاط باش)"))
    rr = p.get("risk_rating") or {}
    if rr:
        why = ("، ".join(rr["reasons"][:3])) if rr.get("reasons") else ""
        L.append(f"• ریسکِ این ناحیه: **{rr['level']}**" + (f" ({why})" if why else ""))
    cs = p.get("combo_score")
    if cs is not None:
        styles = "، ".join(p.get("combo_styles") or []) or "—"
        tag = " ✅ تأییدِ ترکیبی" if p.get("combo_confirmed") else ""
        line = f"• تلفیقِ سبک‌ها: **{cs}/۳** سبک هم‌جهت‌اند ({styles}){tag}"
        clr, cln = p.get("combo_learned_rate"), p.get("combo_learned_n")
        if clr is not None and cln and cln >= 30:
            line += f" — دقتِ آموخته‌شده با ≥{cs} سبکِ هم‌جهت: **{int(clr*100)}٪** (روی {cln} نمونه)"
        elif cs < 2:
            line += " — کمتر از ۲؛ اج ضعیف‌تر"
        L.append(line)
    sr, sn = p.get("learned_stop_rate"), p.get("learned_setups_n")
    if sr is not None and sn and sn >= 20:
        exp = p.get("learned_expR")
        L.append(f"• از استاپ‌های گذشته: ناحیه‌هایی شبیهِ این **{sr}٪** استاپ خورده‌اند "
                 f"(روی {sn} ستاپ" + (f"، بازدهِ میانگین {exp:+}R" if exp is not None else "") + ").")
    if p.get("setup_type") == "reversal":
        e = p.get("rev_edge") or {}
        L.append(f"• نوعِ ستاپ: **بازگشتی (خلافِ روند) — مثلِ سبکِ خودت**. هدفِ پیشنهادی ۳R: "
                 f"**{_fmt(p.get('rev_target'))}**. در بک‌تست این سبک اج مثبت داشت "
                 f"(expR {e.get('expR','?')}، WR ~{e.get('wr','?')}٪ روی {e.get('n','?')} ترید) — "
                 f"اغلب استاپ می‌خورد ولی بردهای ۳R جبران می‌کنند؛ ریسک را کوچک نگه دار.")

    # why this zone
    why = []
    if p["src"] == "OB-1h":
        why.append("ناحیهٔ ۱ساعته است (تنها منبعی که در بک‌تستِ OOS اج داشت)")
    if p["grade"] >= 2:
        why.append("grade ۲ = ایمپالسِ قوی + گپ/FTR بعد از تشکیل")
    if p["with_trend"]:
        why.append("هم‌جهت با سوگیریِ HTF")
    if p["room_R"] is not None and p["room_R"] >= 2:
        why.append(f"فضای کافی تا ناحیهٔ مقابل ({p['room_R']}R ≥ ۲R)")
    elif p["room_R"] is not None:
        why.append(f"⚠️ فضا تا ناحیهٔ مقابل کم است ({p['room_R']}R < ۲R) — TP2 ممکن است بسته نشود")
    if why:
        L.append("• چرا این ناحیه: " + "؛ ".join(why) + ".")

    # confluences (display-only RTM tags)
    if p["confluence"]:
        tags = "، ".join(t["fa"] for t in p["confluence"])
        L.append(f"• تأییدهای کمکیِ RTM (نمایشی، نه گیتِ سیگنال): {tags}.")

    # distance
    if p["dist_atr"] is not None:
        if p["dist_atr"] <= 1.5:
            L.append(f"• قیمت همین حالا نزدیکِ ناحیه است ({p['dist_atr']} ATR) — آماده‌باش.")
        else:
            L.append(f"• قیمت هنوز {p['dist_atr']} ATR با ناحیه فاصله دارد — صبر کن تا برسد، سپس تأییدیه بگیر.")

    # the plan
    L.append("\n## پلنِ معامله — مدلِ R-grid (اعتبارسنجی‌شده)")
    L.append(f"• ورود (لبهٔ پروگزیمال): **{_fmt(p['entry'])}**")
    L.append(f"• استاپ‌لاس: **{_fmt(p['sl'])}**  (ریسک ≈ {_fmt(p['risk'])})")
    L.append(f"• اهداف: TP1 {_fmt(p['tp1'])} (۱R) | TP2 **{_fmt(p['tp2'])} (۲R ← خروجِ اصلی)** | TP3 {_fmt(p['tp3'])} (۳R)")
    L.append("• پیشنهادِ مدیریت: حدِ ضرر را پس از TP1 به نقطهٔ سربه‌سر ببر؛ خروجِ اصلی روی TP2 (۲R).")

    # entry confirmation guidance (from what the books teach, even though it isn't a hard gate)
    rdir = "صعودی" if p["dir"] == "LONG" else "نزولی"
    L.append("\n## تأییدیه‌های پیش از ورود (توصیهٔ RTM)")
    L.append(f"۱) قیمت واقعاً به ناحیه واکنش نشان دهد (کندلِ اِنگالف/پین‌بارِ {rdir} داخلِ ناحیه).")
    L.append("۲) سشن مناسب باشد (کریپتو: نیویورک؛ طلا: لندن).")
    L.append("۳) فضای حداقل ۲R تا ناحیهٔ مقابل باز باشد (room).")
    L.append("۴) خلافِ ایمپالسِ خیلی تندِ HTF وارد نشو (نشانهٔ شکستِ ناحیه).")

    # ---- RSI + divergence + trend projection ----
    L.append(_rsi_proj(sig))

    L.append(_footer())
    return _ltn("\n".join(L))


def _rsi_proj(sig):
    out = ["\n## دیدِ تکمیلی — RSI و روند"]
    rsiobj = sig.get("rsi") or {}
    rsi = rsiobj.get("last")
    if rsi is not None:
        state = "اشباعِ خرید (احتمالِ برگشت/اصلاح)" if rsi >= 70 else \
                ("اشباعِ فروش (احتمالِ برگشت/اصلاح)" if rsi <= 30 else "خنثی")
        out.append(f"• RSI(14) = **{rsi}** — {state}.")
        if rsiobj.get("state_fa"):
            out.append(f"  {rsiobj['state_fa']}")
        out.append("  (نکته: RSI و واگرایی در تایم‌فریم‌های بالاتر — ۱۵م/۱ساعته/۴ساعته/روزانه — معتبرترند.)")
    divs = sig.get("divergences") or []
    if divs:
        d = divs[-1]
        out.append(f"• آخرین واگرایی: **{d['fa']}** (RSI={d['rsi']}). "
                   "واگرایی نشانهٔ ضعفِ روندِ فعلی است — تأییدِ کمکی، نه سیگنالِ مستقل.")
    else:
        out.append("• واگراییِ فعالی در محدودهٔ اخیر دیده نشد.")
    p = sig.get("projection") or {}
    if p:
        out.append(f"• پیش‌بینیِ جهت (هیپوتزِ سیستم، نه تضمین): **{p.get('dir')}** "
                   f"با اطمینانِ ~{int(p.get('confidence',0)*100)}٪.")
        if p.get("notes"):
            out.append("  دلایل: " + "؛ ".join(p["notes"]) + ".")
        if p.get("scenario"):
            out.append("  " + p["scenario"])
            for ev in (p.get("events") or []):
                z = ev.get("zone", {})
                verb = "احتمالِ واکنش/برگشت" if ev["type"] == "bounce" else "احتمالِ شکست و ادامه"
                out.append(f"  ◦ برخورد به ناحیهٔ {z.get('action_fa','')} "
                           f"{z.get('bot')}–{z.get('top')}: **{verb}** ({ev.get('reason')}).")
        if p.get("learned_n"):
            out.append(f"  دقتِ پیش‌بینی‌های گذشتهٔ سیستم روی این نماد/تایم‌فریم: "
                       f"~{int((p.get('learned_rate') or 0)*100)}٪ (روی {p['learned_n']} پیش‌بینیِ بسته‌شده).")
        out.append("  خطِ نقطه‌چینِ روی چارت فقط مسیرِ محتمل است؛ معیارِ ورود همان پلنِ ناحیه‌ای بالاست.")
    return "\n".join(out)


def _footer():
    return ("\n———\n⚠️ این یک **تحلیلِ سیستمی** است، نه سفارش. هیچ معامله‌ای به‌جای تو اجرا نمی‌شود — "
            "ورود/خروج را خودت دستی انجام می‌دهی. ریسکِ هر معامله را ۱٪ نگه دار.")


if __name__ == "__main__":
    import sys, signal_service as S
    s = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    tf = sys.argv[2] if len(sys.argv) > 2 else "M5"
    print(narrate(S.compute(s, tf)))
