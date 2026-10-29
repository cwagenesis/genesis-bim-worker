import json
import psycopg
from psycopg.rows import dict_row

class DB:
    def __init__(self, url):
        self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)

    def q(self, sql, params=None):
        with self.conn.cursor() as c:
            c.execute(sql, params or ())
            return c.fetchall() if c.description else []

    def one(self, sql, params=None):
        r = self.q(sql, params)
        return r[0] if r else None

    def claim(self, worker):
        r = self.one("select * from worker_claim_run(%s)", (worker,))
        return r if r and r.get("id") else None

    def requeue_stuck(self, minutes):
        return self.one("select worker_requeue_stuck(%s) as n", (minutes,))["n"]

    def finish(self, run_id, status, output=None, evidence=None, error=None):
        self.q("""update app_tool_runs set status=%s, output=%s::jsonb, evidence=coalesce(evidence,'{}'::jsonb) || %s::jsonb,
                  error=%s, finished_at=now() where id=%s""",
               (status, json.dumps(output) if output is not None else None, json.dumps(evidence or {}), error, run_id))

    def month_spend(self):
        return float(self.one("select coalesce(sum(cost_usd),0) as s from worker_usage where created_at >= date_trunc('month', now())")["s"])

    def log_usage(self, run_id, tool, model, tin, tout, cost):
        self.q("insert into worker_usage(run_id,tool_code,model,input_tokens,output_tokens,cost_usd) values (%s,%s,%s,%s,%s,%s)",
               (run_id, tool, model, tin, tout, cost))

    def tool_current(self, code):
        return self.one("""select t.code, v.id as version_id, v.version, v.body, left(v.sha256,12) as sha
                           from app_tools t join app_tool_versions v on v.tool_id=t.id and v.is_current where t.code=%s""", (code,))

    def memory_pack(self, agent):
        return self.one("select memory_pack(%s) as p", (agent,))["p"]
