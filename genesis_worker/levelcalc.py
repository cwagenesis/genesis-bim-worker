"""Rule 6.2 preview: what the leveling would add for each line the bidder excludes or does not mention.
Same order as bid_review_apply: own add already saved, GPL, lowest price another bidder wrote, PRICE GAP."""

def preview(db, trade, bidder, takeoff, statuses):
    own = {r["line_id"]: r for r in db.q("""select l.line_id, l.added_amount from bid_lines l join bidders b on b.id=l.bidder_id
                                           where b.trade_id=%s and b.name=%s and l.added_source='own_add'""", (trade, bidder))}
    others = {}
    for r in db.q("""select l.line_id, l.price from bid_lines l join bidders b on b.id=l.bidder_id
                     where b.trade_id=%s and b.name<>%s and coalesce(l.price,0)>0""", (trade, bidder)):
        others.setdefault(r["line_id"], []).append(float(r["price"]))
    for r in db.q("select map_line_ids, amount from bid_price_lines where trade_id=%s and bidder_name<>%s and amount>0", (trade, bidder)):
        if len(r["map_line_ids"]) == 1:
            others.setdefault(r["map_line_ids"][0], []).append(float(r["amount"]))
    out = {}
    for t in takeoff:
        s = statuses.get(t["line_id"])
        if s in ("I", "A") or s is None:
            continue
        lid = t["line_id"]
        if lid in own:
            out[lid] = {"step": "own", "amount": float(own[lid]["added_amount"] or 0), "price_gap": False}
        elif t.get("gpl_budget") and float(t["gpl_budget"]) > 0:
            out[lid] = {"step": "gpl", "amount": float(t["gpl_budget"]), "price_gap": False}
        elif others.get(lid):
            out[lid] = {"step": "lowest_other", "amount": min(others[lid]), "price_gap": False}
        else:
            out[lid] = {"step": "price_gap", "amount": None, "price_gap": True}   # no amount; never shown as $0
    return out
