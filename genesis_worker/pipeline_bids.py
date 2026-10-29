"""The bids chain: read -> CSI -> second reading (verify) -> leveling preview. Writes nothing in bid tables."""
import json
from . import prep, validate, levelcalc
from .llm import extract_json

RULES_NOTE = ("Follow the frozen instructions in the system message exactly. Return ONE JSON object and nothing else. "
              "You may give a coverage range in `line_id` as `pX-lA..pX-lB` when consecutive lines share a category. "
              "Fixed code will recheck amounts, arithmetic, percentages and coverage; it will not correct anything for you.")

def _blocks(intro, pages, with_images=True):
    b = [{"type": "text", "text": intro}]
    for p in pages:
        b.append({"type": "text", "text": prep.page_text_block(p)})
        if with_images:
            b.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": p["image_b64"]}})
    return b

def _json(o):
    return json.dumps(o, ensure_ascii=False, default=str)

def load_context(db, trade, bidder):
    takeoff = db.q("""select line_id, csi, building_area, scope_item, qty, unit, gpl_unit_price, gpl_budget
                      from takeoff_lines where trade_id=%s order by nullif(regexp_replace(line_id,'\\D','','g'),'')::int""", (trade,))
    for t in takeoff:
        for k in ("qty", "gpl_budget"):
            if t[k] is not None:
                t[k] = float(t[k])
    files = db.q("""select distinct on (file_name) id, file_name, file_path, file_sha256, storage from bid_files
                    where trade_id=%s and bidder_name=%s and kind='proposal_pdf' and removed_at is null
                    order by file_name, version desc""", (trade, bidder))
    shared = []
    for f in files:
        if f["file_sha256"]:
            shared += [r["trade_id"] for r in db.q("select distinct trade_id from bid_files where file_sha256=%s and trade_id<>%s and removed_at is null", (f["file_sha256"], trade))]
    division = "".join(ch for ch in trade if ch.isdigit())
    related = [r["id"] for r in db.q("select id from trades where id<>%s and regexp_replace(id,'\\D','','g')=%s", (trade, division))]
    lumps = db.q("select lead_line_id, line_ids, budget from gpl_lump_groups where trade_id=%s", (trade,))
    for g in lumps:
        g["budget"] = float(g["budget"]) if g["budget"] is not None else None
    b = db.one("select quoted_total, proposal_date_text, valid_until_text from bidders where trade_id=%s and name=%s", (trade, bidder))
    return {"takeoff": takeoff, "files": files, "shared_trades": sorted(set(shared)), "related_trades": related,
            "gpl_lump_groups": lumps, "bidder_row": {k: (float(v) if k == "quoted_total" and v is not None else v) for k, v in b.items()}}

def run(db, claude, files_src, cfg, run_row):
    rid = run_row["id"]
    trade, bidder = run_row["target"]["trade_id"], run_row["target"]["bidder_name"]
    ev = {"steps": []}
    ctx = load_context(db, trade, bidder)
    if not ctx["files"]:
        raise RuntimeError("This bidder has no proposal file.")
    tools = {c: db.tool_current(c) for c in ("bids.read", "bids.csi", "bids.verify", "bids.level")}
    for c, t in tools.items():
        if not t:
            raise RuntimeError(f"Tool {c} has no current version.")
    ev["versions"] = {c: f"v{t['version']} {t['sha']}" for c, t in tools.items()}
    pack_bid = db.memory_pack("bid_reader")
    pack_lvl = db.memory_pack("leveling")

    # pages
    pages = []
    for f in ctx["files"]:
        pages += prep.prepare(files_src.get(f), f["file_name"], cfg.max_pages)
    if len(pages) > cfg.max_pages:
        raise RuntimeError(f"The proposal has {len(pages)} pages; the limit is {cfg.max_pages}.")
    prep.number_pages(pages)
    all_ids = [l["id"] for p in pages for l in p["lines"]]
    ev["pages"] = [{"page": p["page"], "file": p["file"], "source": p["source"], "lines": len(p["lines"])} for p in pages]
    tk_ids = [t["line_id"] for t in ctx["takeoff"]]

    # 1. read
    intro = (RULES_NOTE + "\n\nTRADE: " + trade + "  BIDDER: " + bidder +
             "\nTAKEOFF (id, csi, area, scope, qty, unit, GPL unit price, GPL budget): " + _json(ctx["takeoff"]) +
             "\nGPL LUMP GROUPS: " + _json(ctx["gpl_lump_groups"]) +
             "\nRELATED TRADES: " + _json(ctx["related_trades"]) + "\nFILES ALSO FILED IN TRADES: " + _json(ctx["shared_trades"]) +
             "\nTHE WORKER HANDED " + str(len(all_ids)) + " TEXT LINES (set coverage_count.received to this number)." +
             "\nMEMORY PACK: " + _json(pack_bid) + "\n\nPAGES:")
    content = _blocks(intro, pages)
    rec, last_err = None, None
    for attempt in range(2):
        c = content if attempt == 0 else content + [{"type": "text", "text": "The code rejected your answer: " + "; ".join(last_err) + ". Return the complete corrected JSON."}]
        rec = claude.complete_json(rid, "bids.read", tools["bids.read"]["body"], c)
        try:
            validate.hard_checks(rec, tk_ids, all_ids)
            last_err = None
            break
        except validate.ValidationError as e:
            last_err = e.errors
            ev["steps"].append({"step": "read", "attempt": attempt + 1, "rejected": e.errors[:10]})
    if last_err:
        raise RuntimeError("The read was rejected by the code checks twice: " + "; ".join(last_err[:6]))
    checks, code_flags = validate.soft_checks(rec, pages, ctx["takeoff"])
    ev["steps"].append({"step": "read", "ok": True, "price_lines": len(rec["price_lines"])})

    # 2. CSI
    csi_table = db.tool_current("bids.csi_table")
    intro2 = (RULES_NOTE + "\n\nRECORD FROM bids.read: " + _json({k: rec[k] for k in ("price_lines", "line_status", "exclusions", "alternates") if k in rec}) +
              "\nTAKEOFF: " + _json(ctx["takeoff"]) + "\nGPL LUMP GROUPS: " + _json(ctx["gpl_lump_groups"]) +
              "\nCSI TABLE OF EARLIER TRADES (examples): " + (csi_table["body"][:60000] if csi_table else "not available") +
              "\nMEMORY PACK: " + _json(pack_bid))
    csi = claude.complete_json(rid, "bids.csi", tools["bids.csi"]["body"], [{"type": "text", "text": intro2}])
    ev["steps"].append({"step": "csi", "ok": True})

    # 3. second reading, independent: pages + the record, no reasoning of the first call
    intro3 = (RULES_NOTE + "\n\nRECORD TO VERIFY (bids.read + bids.csi): " + _json({"read": rec, "csi": csi}) +
              "\nTAKEOFF: " + _json(ctx["takeoff"]) + "\nTHE WORKER HANDED " + str(len(all_ids)) + " TEXT LINES.\nMEMORY PACK: " + _json(pack_bid) + "\n\nPAGES:")
    ver = claude.complete_json(rid, "bids.verify", tools["bids.verify"]["body"], _blocks(intro3, pages))
    vchecks = ver.get("checks", []) if isinstance(ver, dict) else []
    ev["steps"].append({"step": "verify", "ok": True, "claude_result": ver.get("result") if isinstance(ver, dict) else None})
    all_checks = checks + [dict(c, origin="verify") for c in vchecks]
    bad = [c for c in all_checks if c.get("result") in ("difference", "cannot_verify")]
    verify_result = "verified" if not bad else "needs_review"

    # 4. leveling preview (code) + bidder questions (Claude, amounts untouched)
    statuses = {s["takeoff_id"]: s["s"] for s in rec["line_status"]}
    prev = levelcalc.preview(db, trade, bidder, ctx["takeoff"], statuses)
    intro4 = (RULES_NOTE + "\n\nLEVELING PREVIEW computed by code (do not change any amount): " + _json(prev) +
              "\nRECORD: " + _json({"line_status": rec["line_status"], "flags": rec["flags"] + code_flags, "alternates": rec.get("alternates")}) +
              "\nTAKEOFF: " + _json(ctx["takeoff"]) + "\nMEMORY PACK: " + _json(pack_lvl) +
              "\nReturn only: bidder_questions, alerts, questions_for_fabian. Never name another bidder.")
    lvl = claude.complete_json(rid, "bids.level", tools["bids.level"]["body"], [{"type": "text", "text": intro4}], max_tokens=6000)
    ev["steps"].append({"step": "level", "ok": True})

    # draft in the format of Bid review
    price_lines = [{"n": i, "label": p.get("text", ""), "amount": float(p["amount"]), "page": p.get("page"), "quote": p.get("text", ""),
                    "case": p.get("case"), "csi": p.get("csi")} for i, p in enumerate(rec["price_lines"], 1)]
    pl_of = {}
    for i, p in enumerate(rec["price_lines"], 1):
        for t in p.get("maps_to") or []:
            pl_of.setdefault(t, []).append(i)
    lines = {}
    for s in rec["line_status"]:
        t, st = s["takeoff_id"], s["s"]
        if st in ("I", "A"):
            pl = pl_of.get(t) or pl_of.get(s.get("covered_by_line")) or []
            lines[t] = {"code": "I", "pl": pl, "assumed": st == "A", "quote": s.get("quote", ""), "page": s.get("page")}
        else:
            lines[t] = {"code": "E" if st == "E" else "N/M", "quote": s.get("quote", ""), "page": s.get("page")}
            if t in prev:
                lines[t]["level_preview"] = prev[t]
    flags = [dict(f, origin="reader") for f in rec["flags"]] + code_flags
    draft = {"price_lines": price_lines, "lines": lines,
             "proposal": {"bidder": rec.get("bidder"), "files": rec.get("files"), "exclusions": rec.get("exclusions"), "alternates": rec.get("alternates"),
                          "qualifications": rec.get("qualifications"), "quoted_total": rec.get("quoted_total"), "arithmetic": rec.get("arithmetic"),
                          "shared_with_trade": rec.get("shared_with_trade") or (ctx["shared_trades"] or None)},
             "flags": flags, "csi": csi, "verify": {"result": verify_result, "checks": all_checks},
             "bidder_questions": lvl.get("bidder_questions"), "alerts": lvl.get("alerts"),
             "questions_for_fabian": (rec.get("questions_for_fabian") or []) + (lvl.get("questions_for_fabian") or []),
             "coverage_count": rec.get("coverage_count"), "state": "pending_review", "versions": ev["versions"]}
    errs = db.one("select bid_review_errors(%s, %s::jsonb) as e", (trade, json.dumps(draft)))["e"]
    ev["draft_errors"] = errs
    ev["usage"] = claude.usage
    return {"draft": draft, "verify_result": verify_result, "draft_errors": errs}, ev
