"""Main loop. One run at a time; hard timeout per run; stuck runs go back to the queue."""
import signal
import sys
import time
import traceback
from .config import Config
from .db import DB
from .files import FileSource
from .llm import Claude, CapReached
from . import pipeline_bids

class RunTimeout(Exception):
    pass

def _alarm(signum, frame):
    raise RunTimeout()

_stop = False

def _term(signum, frame):
    global _stop
    _stop = True

def process(db, claude, files_src, cfg, run_row):
    rid = run_row["id"]
    try:
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(cfg.run_timeout)
        if (run_row.get("target") or {}).get("pipeline") != "bids":
            raise RuntimeError("This worker only runs the bids pipeline.")
        out, ev = pipeline_bids.run(db, claude, files_src, cfg, run_row)
        signal.alarm(0)
        db.finish(rid, "needs_review", output=out, evidence=ev)
        print(f"run {rid}: needs_review ({out['verify_result']}, draft errors: {len(out['draft_errors'] or [])})", flush=True)
    except RunTimeout:
        db.finish(rid, "failed", error=f"Stopped: the run took more than {cfg.run_timeout} seconds.")
    except CapReached as e:
        db.finish(rid, "failed", error=str(e))
    except Exception as e:
        traceback.print_exc()
        db.finish(rid, "failed", error=f"{type(e).__name__}: {e}"[:1500])
    finally:
        signal.alarm(0)

def main():
    cfg = Config()
    db = DB(cfg.database_url)
    files_src = FileSource(cfg)
    signal.signal(signal.SIGTERM, _term)
    last_requeue = 0
    print("worker started", cfg.worker_name, flush=True)
    while not _stop:
        try:
            if time.time() - last_requeue > 60:
                n = db.requeue_stuck(cfg.run_timeout // 60 + 5)
                if n:
                    print(f"requeued {n} stuck runs", flush=True)
                last_requeue = time.time()
            row = db.claim(cfg.worker_name)
            if not row:
                time.sleep(cfg.poll_seconds)
                continue
            claude = Claude(cfg, db)
            process(db, claude, files_src, cfg, row)
        except Exception:
            traceback.print_exc()
            time.sleep(15)
            try:
                db = DB(cfg.database_url)
            except Exception:
                pass
    print("worker stopped", flush=True)

if __name__ == "__main__":
    sys.exit(main())
