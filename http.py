"""Small GET-only HTTP client, with conditional downloads and bounded retries."""

import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


class DataError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


class Client:
    def __init__(self, cache_dir, timeout=40, offline=False):
        self.root = Path(cache_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.offline = offline
        self.sources = []

    def get(self, url, cache=True, optional=False):
        key = hashlib.sha256(url.encode()).hexdigest()
        payload = self.root / (key + ".bin")
        meta = self.root / (key + ".json")
        previous = json.loads(meta.read_text()) if meta.exists() else {}
        if self.offline:
            if not payload.exists():
                if optional:
                    return None
                raise DataError(f"No cached data for {url}. Run an online refresh first.")
            body = payload.read_bytes()
            self.sources.append({**previous, "offline": True})
            return body
        headers = {"User-Agent": "FPL-Advisor/1.0 (personal research)", "Accept": "*/*"}
        if cache and payload.exists() and previous.get("etag"):
            headers["If-None-Match"] = previous["etag"]
        # Live JSON is fetched afresh so that stale prices/flags cannot masquerade as current.
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read(40 * 1024 * 1024 + 1)
                    if len(body) > 40 * 1024 * 1024:
                        raise DataError(f"Response is unexpectedly large: {url}")
                    details = {"url": url, "sha256": hashlib.sha256(body).hexdigest(),
                               "etag": response.headers.get("ETag"), "fetched_at": utc_now()}
                    temp = payload.with_suffix(".tmp")
                    temp.write_bytes(body)
                    temp.replace(payload)
                    write_json(meta, details)
                    self.sources.append(details)
                    return body
            except urllib.error.HTTPError as exc:
                if exc.code == 304 and payload.exists():
                    details = {**previous, "checked_at": utc_now()}
                    self.sources.append(details)
                    return payload.read_bytes()
                if exc.code == 404 and optional:
                    return None
                if exc.code not in (429, 500, 502, 503, 504):
                    raise DataError(f"HTTP {exc.code} downloading {url}") from exc
                problem = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                problem = exc
            if attempt < 2:
                time.sleep(min(2 ** attempt, 4))
        raise DataError(f"Could not refresh {url}: {problem}. Previous reports remain saved.")

    def json(self, url, **kwargs):
        body = self.get(url, **kwargs)
        if body is None:
            return None
        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DataError(f"Expected JSON from {url}") from exc
