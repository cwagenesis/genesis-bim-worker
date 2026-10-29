"""Checks done by code on what Claude returns. Fixed code decides what a rule can decide; nothing is corrected silently."""
import re
from .prep import numbers_in

class ValidationError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors[:6]))
        self.errors = errors

CATS = {"data", "price", "exclusion", "alternate", "qualification", "other"}
STATUS = {"I", "A", "E", "N"}
VE_WORDS = re.compile(r"\b(value engineering|VE|deduct|alternate|option(al)?)\b", re.I)

def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)

def expand_coverage(entries, all_ids):
    """Entries may give a range 'p1-l3..p1-l9' in line_id. Returns (placed_ids list, errors)."""
    order = {lid: i for i, lid in enumerate(all_ids)}
    placed, errs = [], []
    for e in entries:
        lid = str(e.get("line_id", ""))
        ids = [lid]
        if ".." in lid:
            a, b = [s.strip() for s in lid.split("..", 1)]
            if a not in order or b not in order or order[a] > order[b]:
                errs.append(f"Coverage range {lid} is not valid.")
                continue
            ids = all_ids[order[a]:order[b] + 1]
        elif lid not in order:
            errs.append(f"Coverage names a line that does not exist: {lid}.")
            continue
        if e.get("category") not in CATS:
            errs.append(f"Coverage category invalid for {lid}.")
        if e.get("category") == "other" and not str(e.get("reason", "")).strip():
            errs.append(f"Coverage 'other' without a reason for {lid}.")
        placed.extend(ids)
    return placed, errs

def hard_checks(rec, takeoff_ids, all_line_ids):
    errs = []
    if not isinstance(rec, dict):
        raise ValidationError(["The answer is not a JSON object."])
    for k in ("price_lines", "line_status", "coverage", "quoted_total", "flags"):
        if k not in rec:
            errs.append(f"Missing key {k}.")
    if errs:
        raise ValidationError(errs)
    pl = rec["price_lines"]
    for i, p in enumerate(pl, 1):
        if not _num(p.get("amount")):
            errs.append(f"Price line {i} has no numeric amount.")
        for t in p.get("maps_to") or []:
            if t not in takeoff_ids:
                errs.append(f"Price line {i} maps to unknown takeoff line {t}.")
    seen = {}
    for s in rec["line_status"]:
        t = s.get("takeoff_id")
        if t not in takeoff_ids:
            errs.append(f"Status for unknown takeoff line {t}.")
        if s.get("s") not in STATUS:
            errs.append(f"Invalid status for {t}.")
        seen[t] = seen.get(t, 0) + 1
    for t in takeoff_ids:
        if seen.get(t, 0) != 1:
            errs.append(f"Takeoff line {t} has {seen.get(t, 0)} statuses (must be exactly 1).")
    placed, cerrs = expand_coverage(rec["coverage"], all_line_ids)
    errs += cerrs
    dup = {x for x in placed if placed.count(x) > 1}
    if dup:
        errs.append(f"Lines placed twice: {sorted(dup)[:5]}.")
    missing = [x for x in all_line_ids if x not in set(placed)]
    if missing:
        errs.append(f"{len(missing)} text lines have no category (first: {missing[:5]}).")
    cc = rec.get("coverage_count") or {}
    if cc.get("received") != len(all_line_ids):
        errs.append(f"coverage_count.received is {cc.get('received')} but the worker handed {len(all_line_ids)} lines.")
    if errs:
        raise ValidationError(errs)

def soft_checks(rec, pages, takeoff):
    """Returns (checks, flags). Flags are added to the record with origin 'code'."""
    checks, flags = [], []
    by_page = {p["page"]: p for p in pages}
    pl = rec["price_lines"]
    # 1 amounts on the cited page
    for i, p in enumerate(pl, 1):
        pg = by_page.get(p.get("page"))
        nums = numbers_in(" ".join(l["text"] for l in pg["lines"])) if pg else set()
        ok = round(float(p["amount"]), 2) in nums
        src = pg["source"] if pg else "none"
        res = "ok" if ok else ("cannot_verify" if src == "ocr" else "difference")
        checks.append({"point": 1, "item": f"price line {i}", "result": res, "page": p.get("page"),
                       "note": "amount found in the page text" if ok else f"amount {p['amount']} not found in the {src} text of the page"})
        if not ok:
            flags.append({"severity": "H" if src != "ocr" else "M", "kind": "Code check",
                          "en": f"Price line {i} ({p['amount']:,.2f}) was not found in the {'OCR' if src=='ocr' else 'text'} of page {p.get('page')}. Confirm it on the page image.",
                          "es": f"La línea de precio {i} ({p['amount']:,.2f}) no se encontró en el {'OCR' if src=='ocr' else 'texto'} de la página {p.get('page')}. Confirmar en la imagen.",
                          "quote": p.get("text", ""), "page": p.get("page"), "origin": "code"})
    # 2 arithmetic
    total = round(sum(float(p["amount"]) for p in pl), 2)
    qt = rec.get("quoted_total")
    if _num(qt) and abs(total - qt) > 0.01:
        checks.append({"point": 2, "item": "sum of price lines", "result": "difference", "note": f"lines add to {total:,.2f}, quoted_total says {qt:,.2f}"})
        flags.append({"severity": "H", "kind": "Arithmetic", "en": f"Price lines add to {total:,.2f} but the record says {qt:,.2f}.",
                      "es": f"Las líneas suman {total:,.2f} pero el registro dice {qt:,.2f}.", "quote": "", "page": None, "origin": "code"})
    else:
        checks.append({"point": 2, "item": "sum of price lines", "result": "ok", "note": f"{total:,.2f}"})
    for i, p in enumerate(pl, 1):
        q, u = p.get("qty"), p.get("unit_price")
        if _num(q) and _num(u) and abs(q * u - float(p["amount"])) > 0.01 * max(1, abs(q)):
            checks.append({"point": 2, "item": f"price line {i} multiplication", "result": "difference", "note": f"{q} x {u} = {q*u:,.2f}, written {p['amount']:,.2f}"})
            flags.append({"severity": "H", "kind": "Arithmetic", "en": f"Line {i}: {q} x {u} = {q*u:,.2f}, but the amount written is {p['amount']:,.2f}.",
                          "es": f"Línea {i}: {q} x {u} = {q*u:,.2f}, pero el monto escrito es {p['amount']:,.2f}.", "quote": p.get("text", ""), "page": p.get("page"), "origin": "code"})
    tw = (rec.get("bidder") or {}).get("total_as_written")
    if _num(tw) and abs(tw - total) > 0.01:
        flags.append({"severity": "M", "kind": "Arithmetic", "en": f"Total written in the proposal ({tw:,.2f}) differs from the sum of the price lines ({total:,.2f}).",
                      "es": f"El total escrito ({tw:,.2f}) difiere de la suma de las líneas ({total:,.2f}).", "quote": "", "page": (rec.get('bidder') or {}).get('page'), "origin": "code"})
    # 5 VE never a price
    for i, p in enumerate(pl, 1):
        if VE_WORDS.search(p.get("text", "")) and (p.get("maps_to") or []):
            checks.append({"point": 5, "item": f"price line {i}", "result": "difference", "note": "looks like a VE/deduct/alternate but it is mapped as a line price"})
            weak = not re.search(r"\b(value engineering|VE|deduct|alternate)\b", p.get("text", ""), re.I)
            flags.append({"severity": "M" if weak else "H", "kind": "Alternate or VE", "en": f"Price line {i} reads as a VE, deduct or alternate but is used as a line price.",
                          "es": f"La línea {i} parece un VE, deducto o alternate pero se usa como precio de línea.", "quote": p.get("text", ""), "page": p.get("page"), "origin": "code"})
    # 7 quantity difference (3 %, unconfirmed)
    tl = {t["line_id"]: t for t in takeoff}
    for s in rec["line_status"]:
        bq, t = s.get("bidder_qty"), tl.get(s.get("takeoff_id"))
        if _num(bq) and t and t.get("qty"):
            pct = round((bq - float(t["qty"])) / float(t["qty"]) * 100, 2)
            s["qty_diff_pct"] = pct
            if abs(pct) > 3:
                flags.append({"severity": "M", "kind": "Different quantity",
                              "en": f"{s['takeoff_id']}: bidder quantity {bq:,} vs takeoff {float(t['qty']):,} ({pct:+.2f} %). Threshold 3 % unconfirmed.",
                              "es": f"{s['takeoff_id']}: cantidad del bidder {bq:,} vs takeoff {float(t['qty']):,} ({pct:+.2f} %). Umbral de 3 % sin confirmar.",
                              "quote": s.get("quote", ""), "page": s.get("page"), "origin": "code"})
    return checks, flags
