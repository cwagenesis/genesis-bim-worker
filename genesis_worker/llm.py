"""Claude calls. Every call is counted against the monthly cap and logged."""
import json
import re

class CapReached(Exception):
    pass

def extract_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < a:
        raise ValueError("No JSON object in the answer.")
    return json.loads(t[a:b + 1])

class Claude:
    def __init__(self, cfg, db):
        import anthropic
        self.cfg, self.db = cfg, db
        self.client = anthropic.Anthropic(timeout=600.0, max_retries=2)
        self.usage = []   # per call, for the run evidence

    def complete(self, run_id, tool_code, system, content, max_tokens=16000):
        if self.db.month_spend() >= self.cfg.monthly_cap:
            raise CapReached(f"Monthly Claude cap of ${self.cfg.monthly_cap:.2f} reached. Nothing was called.")
        with self.client.messages.stream(model=self.cfg.model, max_tokens=max_tokens, system=system,
                                         messages=[{"role": "user", "content": content}]) as s:
            msg = s.get_final_message()
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        tin, tout = msg.usage.input_tokens, msg.usage.output_tokens
        cost = tin / 1e6 * self.cfg.price_in + tout / 1e6 * self.cfg.price_out
        self.db.log_usage(run_id, tool_code, self.cfg.model, tin, tout, cost)
        self.usage.append({"tool": tool_code, "in": tin, "out": tout, "cost_usd": round(cost, 4)})
        return text

    def complete_json(self, run_id, tool_code, system, content, max_tokens=16000):
        text = self.complete(run_id, tool_code, system, content, max_tokens)
        try:
            return extract_json(text)
        except ValueError:
            fix = content + [{"type": "text", "text": "Your previous answer was not valid JSON. Return the complete JSON object again, nothing else."}]
            return extract_json(self.complete(run_id, tool_code, system, fix, max_tokens))
