"""Gets the proposal PDFs. Dropbox (production) or a local folder (tests)."""
import os
import requests

class FileSource:
    def __init__(self, cfg):
        self.cfg = cfg
        self._token = None

    def _dropbox_token(self):
        if self._token:
            return self._token
        c = self.cfg
        if not (c.dropbox_key and c.dropbox_secret and c.dropbox_refresh):
            raise RuntimeError("Dropbox variables are missing (DROPBOX_APP_KEY, DROPBOX_APP_SECRET, DROPBOX_REFRESH_TOKEN).")
        r = requests.post("https://api.dropbox.com/oauth2/token",
                          data={"grant_type": "refresh_token", "refresh_token": c.dropbox_refresh},
                          auth=(c.dropbox_key, c.dropbox_secret), timeout=60)
        r.raise_for_status()
        self._token = r.json()["access_token"]
        return self._token

    def get(self, f):
        """f is a bid_files row. Returns the PDF bytes."""
        if self.cfg.local_files_dir:
            p = os.path.join(self.cfg.local_files_dir, f["file_name"])
            with open(p, "rb") as fh:
                return fh.read()
        if f["storage"] == "dropbox":
            import json
            r = requests.post("https://content.dropboxapi.com/2/files/download",
                              headers={"Authorization": "Bearer " + self._dropbox_token(),
                                       "Dropbox-API-Arg": json.dumps({"path": f["file_path"]})}, timeout=120)
            if r.status_code == 401:
                self._token = None
            r.raise_for_status()
            return r.content
        raise RuntimeError(f"Storage '{f['storage']}' is not supported by the worker.")
