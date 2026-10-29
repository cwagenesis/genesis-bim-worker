import os

def env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and not v:
        raise RuntimeError(f"Missing variable {name}")
    return v

class Config:
    def __init__(self):
        self.database_url = env("DATABASE_URL", required=True)
        self.model = env("CLAUDE_MODEL", "claude-sonnet-5-5")
        self.monthly_cap = float(env("MONTHLY_CAP_USD", "50"))
        self.run_timeout = int(env("RUN_TIMEOUT_SECONDS", "900"))
        self.max_pages = int(env("MAX_PAGES", "30"))
        self.price_in = float(env("PRICE_INPUT_PER_MTOK", "3"))
        self.price_out = float(env("PRICE_OUTPUT_PER_MTOK", "15"))
        self.poll_seconds = int(env("POLL_SECONDS", "5"))
        self.local_files_dir = env("LOCAL_FILES_DIR")      # tests only: folder with the PDFs
        self.dropbox_key = env("DROPBOX_APP_KEY")
        self.dropbox_secret = env("DROPBOX_APP_SECRET")
        self.dropbox_refresh = env("DROPBOX_REFRESH_TOKEN")
        self.worker_name = env("WORKER_NAME", "railway-1")
