"""Plumbing test with a FAKE Claude (canned answers). It proves the worker, the checks and the database path work.
It does NOT prove the quality of Claude's reading: that needs the real key."""
import json, os, re, sys, copy
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from genesis_worker.config import Config
from genesis_worker.db import DB
from genesis_worker.files import FileSource
from genesis_worker import runner

RMW = [  # read from the page image of the RM Ward proposal (verified visually earlier)
    ("Elevator pits (4)", 68000.00, 4, 17000.00), ("Building Type 1", 196500.00, None, None), ("Building Type 2 (3)", 600000.00, 3, 200000.00),
    ("Clubhouse", 146500.00, None, None), ("Pool Pavilion", 19000.00, None, None), ("Dog Wash", 7000.00, None, None),
    ("Maintenance Building", 13000.00, None, None), ("Compactor pad", 16500.00, None, None), ("Compactor pad (second item)", 4000.00, None, None)]

class FakeClaude:
    def __init__(self, mode="good"):
        self.usage, self.mode = [], mode
    def complete_json(self, rid, tool, system, content, max_tokens=0):
        txt = "\n".join(b["text"] for b in content if b["type"] == "text")
        ids = re.findall(r"\[(p\d+-l\d+)\]", txt)
        if tool == "bids.read":
            tk = json.loads(re.search(r"TAKEOFF \(.*?\): (\[.*?\])\nGPL", txt, re.S).group(1))
            cov = [{"line_id": f"{ids[0]}..{ids[-1]}", "page": 1, "category": "data", "reason": ""}] if ids else []
            if self.mode == "missing_coverage":
                cov = cov[:0]
            pls = [{"text": t, "qty": q, "unit": None, "unit_price": u, "amount": a, "page": 1, "maps_to": [tk[i]["line_id"]], "case": "one", "csi": None} for i, (t, a, q, u) in enumerate(RMW)]
            if self.mode == "wrong_amount":
                pls[1]["amount"] = 196501.00
            st = [{"takeoff_id": t["line_id"], "s": "I" if i < len(RMW) else "N", "quote": "", "page": 1, "bidder_qty": None} for i, t in enumerate(tk)]
            return {"bidder": {"name": "RM Ward", "total_as_written": 1070500.00, "page": 1}, "files": [], "price_lines": pls, "quoted_total": round(sum(p["amount"] for p in pls), 2),
                    "arithmetic": {"ok": True, "note": ""}, "line_status": st, "exclusions": [], "alternates": [], "qualifications": [], "flags": [], "coverage": cov,
                    "coverage_count": {"received": len(ids), "placed": len(ids)}, "shared_with_trade": None, "state": "pending_review", "questions_for_fabian": []}
        if tool == "bids.csi":
            return {"codes": [], "csi_corrections": [], "overlaps": [], "connections": [], "orphans": [], "unassigned": [], "arithmetic": [], "state": "pending_review", "questions_for_fabian": []}
        if tool == "bids.verify":
            return {"checks": [{"point": 1, "item": "amounts", "result": "ok", "page": 1, "quote": "", "record_says": "", "you_read": "", "note": ""}], "summary": {"ok": 1, "difference": 0, "cannot_verify": 0}, "result": "verified", "state": "pending_review", "questions_for_fabian": []}
        return {"bidder_questions": [{"bidder": "RM Ward", "questions": ["Please confirm the unit price basis of the pits."]}], "alerts": [], "questions_for_fabian": []}

def queue(db):
    db.q("delete from app_tool_runs where trade_id='3A' and status in ('queued','running','needs_review','failed')")
    return db.one("select bid_run_request('3A','RM Ward') as r")["r"]

def main():
    cfg = Config(); db = DB(cfg.database_url); fs = FileSource(cfg)
    results = {}
    for mode in ("good", "wrong_amount", "missing_coverage"):
        rid = queue(db)
        row = db.claim("test")
        assert row["id"] == rid
        runner.process(db, FakeClaude(mode), fs, cfg, row)
        r = db.one("select status, error, output->'verify_result' as v, output->'draft_errors' as de, output #> '{draft,flags}' as fl, evidence from app_tool_runs where id=%s", (rid,))
        results[mode] = r
        print("=== mode", mode, "->", r["status"], "| verify:", r["v"], "| draft errors:", r["de"], "| error:", (r["error"] or "")[:160])
        if mode == "good":
            print("   flags:", [(f["kind"], f["severity"], f["en"][:70]) for f in r["fl"]][:6])
            print("   pages:", r["evidence"]["pages"], "versions:", r["evidence"]["versions"])
            print("   open in review:", db.one("select bid_review_from_run(%s) as x", (rid,)) if not r["de"] else "draft has errors, cannot open yet")
    assert results["good"]["status"] == "needs_review" and results["good"]["v"] == "verified"
    assert results["wrong_amount"]["v"] == "needs_review"
    assert results["missing_coverage"]["status"] == "failed"
    print("ALL PLUMBING CHECKS PASSED")

if __name__ == "__main__":
    main()
