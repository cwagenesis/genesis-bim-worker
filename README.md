# Genesis BIM bids worker

Reads a bid proposal with Claude under the frozen rules in the database (bids.read, bids.csi, bids.verify, bids.level), checks the result with fixed code and leaves it as a proposal (needs_review). It never writes bid_lines or bid_price_lines.

Flow: `select bid_run_request('<trade>','<bidder>')` queues a run -> the worker takes it (`worker_claim_run`) -> downloads the PDFs from Dropbox -> text layer or OCR + page images -> 4 Claude calls -> code checks (coverage, amounts on the page, arithmetic, 3 %, VE) -> `app_tool_runs.output` (status needs_review) -> `select bid_review_from_run(<run id>)` saves it as a proposed review for Bid review.

Variables: see `.env.example`. Start command: `python -m genesis_worker.runner`.
Tests: `python tests/test_e2e.py` (fake Claude; needs a database with SQL 33-42 and LOCAL_FILES_DIR).
Limits: MAX_PAGES per proposal (default 30), RUN_TIMEOUT_SECONDS (default 900), MONTHLY_CAP_USD (default 50).
