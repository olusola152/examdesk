"""File storage: Supabase Storage when SUPABASE_URL + SUPABASE_SERVICE_KEY are set, otherwise local disk.

Keys look like 'recordings/sub_4_1.webm', 'snapshots/x.jpg', 'live/sub_4.jpg', 'lectures/abc.pdf'.
"""
import os
from urllib.parse import quote

from flask import redirect, send_file, abort
import io

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
BUCKET = os.environ.get("SUPABASE_BUCKET", "examdesk")
IS_REMOTE = bool(SUPABASE_URL and SUPABASE_KEY)

if IS_REMOTE:
    import requests
    _s = requests.Session()
    _s.headers.update({"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY})


def _obj(key):
    return f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{quote(key)}"


class Storage:
    def __init__(self, local_root):
        self.root = local_root

    # ---------------------------------------------------------------- setup
    def ensure_bucket(self):
        if not IS_REMOTE:
            return
        try:
            r = _s.post(f"{SUPABASE_URL}/storage/v1/bucket", json={"id": BUCKET, "name": BUCKET, "public": False}, timeout=20)
            if r.status_code not in (200, 201, 400, 409):
                print("Supabase bucket check:", r.status_code, r.text[:200])
        except Exception as e:
            print("Supabase bucket check failed:", e)

    def _path(self, key):
        p = os.path.join(self.root, *key.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    # ---------------------------------------------------------------- basic ops
    def put(self, key, data, content_type="application/octet-stream"):
        if IS_REMOTE:
            r = _s.post(_obj(key), data=data, headers={"Content-Type": content_type, "x-upsert": "true",
                                                       "cache-control": "no-cache"}, timeout=120)
            if r.status_code >= 300:
                raise IOError(f"Supabase upload failed ({r.status_code}): {r.text[:200]}")
            return
        with open(self._path(key), "wb") as f:
            f.write(data)

    def get(self, key):
        if IS_REMOTE:
            r = _s.get(_obj(key), timeout=120)
            return r.content if r.status_code == 200 else None
        p = self._path(key)
        if not os.path.exists(p):
            return None
        with open(p, "rb") as f:
            return f.read()

    def exists(self, key):
        if IS_REMOTE:
            return self.get(key) is not None  # small objects only (frames); avoid for videos
        return os.path.exists(self._path(key))

    def delete(self, keys):
        keys = [k for k in keys if k]
        if not keys:
            return
        if IS_REMOTE:
            _s.delete(f"{SUPABASE_URL}/storage/v1/object/{BUCKET}", json={"prefixes": keys}, timeout=60)
            return
        for k in keys:
            try:
                os.remove(self._path(k))
            except FileNotFoundError:
                pass

    # ---------------------------------------------------------------- serving to the browser
    def serve(self, key, mimetype):
        """Flask response for a stored object (signed-URL redirect on Supabase, file on disk locally)."""
        if IS_REMOTE:
            r = _s.post(f"{SUPABASE_URL}/storage/v1/object/sign/{BUCKET}/{quote(key)}", json={"expiresIn": 3600}, timeout=20)
            if r.status_code != 200:
                abort(404)
            signed = r.json().get("signedURL") or r.json().get("signedUrl")
            return redirect(f"{SUPABASE_URL}/storage/v1{signed}" if signed.startswith("/") else signed)
        p = self._path(key)
        if not os.path.exists(p):
            abort(404)
        return send_file(p, mimetype=mimetype, conditional=True, max_age=0)
