"""Bounded public reads. A blocked source is coverage information, never evidence.

No cookies, proxy rotation, access bypass, private chats or platform writes.
Raw text stays in ignored local caches; reports expose short evidence passages.
"""

import hashlib
import html
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from defusedxml import ElementTree as ET

from .http import DataError, utc_now, write_json

DEFAULT_SOURCES = [
    {"kind": "youtube", "name": "FPL Mate", "handle": "FPLMate", "channel_id": "UCweDAlFm2LnVcOqaFU4_AGA", "enabled": True},
    {"kind": "youtube", "name": "FPL Tom", "handle": "FPLTom", "channel_id": "UC14BPCP-fxIDuG0I_dC2Kpg", "enabled": True},
    {"kind": "youtube", "name": "FPL Raptor", "handle": "FPLRaptor", "channel_id": "UC54QLWzsMifTRjNQ02z5pCw", "enabled": True},
    {"kind": "youtube", "name": "Let's Talk FPL", "handle": "LetsTalkFPL", "channel_id": "UCxeOc7eFxq37yW_Nc-69deA", "enabled": True},
    {"kind": "reddit", "name": "r/FantasyPL", "subreddit": "FantasyPL", "enabled": True},
]
OFFICIAL_HOSTS = {"premierleague.com", "arsenal.com", "avfc.co.uk", "afcb.co.uk", "brentfordfc.com",
                  "brightonandhovealbion.com", "chelseafc.com", "cpfc.co.uk", "evertonfc.com", "fulhamfc.com",
                  "leedsunited.com", "liverpoolfc.com", "mancity.com", "manutd.com", "nufc.co.uk",
                  "nottinghamforest.co.uk", "safc.com", "tottenhamhotspur.com", "westhamunited.com",
                  "wolves.co.uk", "burnleyfootballclub.com", "lcfc.com", "southamptonfc.com", "itfc.co.uk"}
_LOCK = threading.RLock()


def when(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise DataError("Source dates must include a time zone.")
    return dt.astimezone(timezone.utc)


def plain(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", str(text)))).strip()


def public_url(value):
    url = urllib.parse.urlparse(str(value))
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise DataError("Use an https source link without credentials.")
    return str(value)


def official_url(value):
    host = urllib.parse.urlparse(value).hostname or ""
    return any(host == h or host.endswith("." + h) for h in OFFICIAL_HOSTS)


def get(url, timeout=15, headers=None):
    url = urllib.parse.quote(url, safe=":/?&=%#,+@")
    request = urllib.request.Request(url, headers={"User-Agent": "FPL-Advisor/3.1 (personal FPL research)", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(6 * 1024 * 1024 + 1)
    if len(body) > 6 * 1024 * 1024:
        raise DataError("Source exceeds the 6 MB read limit.")
    return body


def settings(root):
    path = Path(root) / "user/qualitative_sources.json"
    return json.loads(path.read_text()) if path.exists() else {"sources": DEFAULT_SOURCES, "lookback_days": 7, "videos_per_channel": 2, "posts_per_community": 12, "comment_threads": 3}


def save_settings(root, payload):
    sources = payload.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 100:
        raise DataError("Supply 1–100 source entries.")
    clean, seen = [], set()
    for source in sources:
        kind = source.get("kind")
        key = str(source.get("handle" if kind == "youtube" else "subreddit", "")).lstrip("@")
        if kind not in ("youtube", "reddit") or not re.fullmatch(r"[A-Za-z0-9_-]{2,60}", key):
            raise DataError("Use a YouTube handle or subreddit name, not a URL.")
        identity = (kind, key.lower())
        if identity in seen:
            continue
        seen.add(identity)
        entry = {"kind": kind, "name": str(source.get("name", key))[:100], "enabled": source.get("enabled", True) is True,
                 "handle" if kind == "youtube" else "subreddit": key}
        cid = source.get("channel_id")
        if kind == "youtube" and cid:
            if not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", str(cid)):
                raise DataError("Invalid YouTube channel ID.")
            entry["channel_id"] = cid
        clean.append(entry)
    result = {"sources": clean}
    for key, default, low, high in [("lookback_days", 7, 1, 14), ("videos_per_channel", 2, 1, 5), ("posts_per_community", 12, 1, 25), ("comment_threads", 3, 0, 5)]:
        value = payload.get(key, default)
        if isinstance(value, bool) or not str(value).isdigit() or not low <= int(value) <= high:
            raise DataError(f"{key} must be {low}–{high}.")
        result[key] = int(value)
    with _LOCK:
        write_json(Path(root) / "user/qualitative_sources.json", result)
    return result


def imports(root):
    path = Path(root) / "user/qualitative_imports.json"
    return json.loads(path.read_text()) if path.exists() else []


def import_text(root, payload):
    text = str(payload.get("text", "")).strip()
    if not 20 <= len(text) <= 150_000:
        raise DataError("Paste 20–150,000 characters of source text or transcript.")
    url = public_url(payload.get("url", ""))
    published = when(payload.get("published_at", ""))
    now = when(utc_now())
    if published > now or published < now - timedelta(days=14):
        raise DataError("Import recent evidence, dated within the last 14 days.")
    origin = str(payload.get("origin_url", "")).strip()
    if origin:
        public_url(origin)
    host = urllib.parse.urlparse(url).hostname or ""
    kind = "official" if official_url(url) else "youtube" if host in ("youtube.com", "www.youtube.com", "youtu.be") else "reddit" if host in ("reddit.com", "www.reddit.com") else "other"
    doc = {"id": hashlib.sha256((url + text).encode()).hexdigest()[:24], "url": url,
           "source": str(payload.get("source", "Imported evidence"))[:100], "kind": kind,
           "published_at": published.isoformat(), "fetched_at": now.isoformat(), "title": str(payload.get("title", "Imported text"))[:200],
           "text": text, "coverage": "imported_transcript" if kind == "youtube" else "imported_text",
           "origin_url": origin or url, "manual": True}
    with _LOCK:
        rows = imports(root)
        rows = [r for r in rows if r["url"] != url and when(r["published_at"]) >= now - timedelta(days=14)]
        write_json(Path(root) / "user/qualitative_imports.json", [*rows, doc][-100:])
    return {"id": doc["id"], "source": doc["source"], "coverage": doc["coverage"]}


def atom(body):
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "media": "http://search.yahoo.com/mrss/"}
    result = []
    for e in ET.fromstring(body).findall("a:entry", ns):
        link = e.find("a:link", ns)
        links = e.findall("a:link", ns)
        link = next((x for x in links if x.attrib.get("rel", "alternate") == "alternate"), link)
        result.append({"url": link.attrib.get("href", "") if link is not None else "", "title": plain(e.findtext("a:title", "", ns)),
                       "published_at": e.findtext("a:published", "", ns) or e.findtext("a:updated", "", ns),
                       "author": e.findtext("a:author/a:name", "", ns), "video_id": e.findtext("yt:videoId", "", ns),
                       "text": plain(e.findtext("a:content", "", ns) or e.findtext("media:group/media:description", "", ns)),
                       "reference_urls": list(dict.fromkeys(re.findall(r'https://[^\s"<>]+', html.unescape(e.findtext("a:content", "", ns) or e.findtext("media:group/media:description", "", ns)))))})
    return result


def transcript(video_id, timeout=15):
    from requests import Session
    from youtube_transcript_api import YouTubeTranscriptApi

    class BoundedSession(Session):
        def request(self, *args, **kwargs):
            kwargs.setdefault("timeout", timeout)
            return super().request(*args, **kwargs)

    with BoundedSession() as session:
        data = YouTubeTranscriptApi(http_client=session).fetch(video_id, languages=["en", "en-GB", "en-US"])
        return {"segments": data.to_raw_data(), "language": data.language_code, "generated": data.is_generated}


def youtube(source, cfg, root, now):
    status = {"source": source["name"], "kind": "youtube", "items": 0, "transcripts": 0, "details": []}
    cid = source.get("channel_id")
    if not cid:
        page = get(f"https://www.youtube.com/@{source['handle']}").decode("utf-8")
        found = re.search(r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"', page) or re.search(r'https://www.youtube.com/channel/(UC[A-Za-z0-9_-]{22})', page)
        if not found:
            raise DataError("Channel ID was not discoverable; add its channel ID in Sources.")
        cid = found.group(1)
    rows = atom(get("https://www.youtube.com/feeds/videos.xml?channel_id=" + cid))
    documents = []
    for row in rows:
        # UCL fantasy content is not evidence about Premier League selection.
        if re.search(r"\b(?:UCL|Champions League|MD\d+)\b", row["title"], re.I) and not re.search(r"\bFPL\b", row["title"], re.I):
            continue
        if not now - timedelta(days=cfg["lookback_days"]) <= when(row["published_at"]) <= now:
            continue
        if len(documents) >= cfg["videos_per_channel"]:
            break
        vid = row["video_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            continue
        row.update({"id": "yt:" + vid, "source": source["name"], "kind": "youtube", "fetched_at": now.isoformat(),
                    "origin_url": row["url"], "coverage": "description_only"})
        path = Path(root) / "data/qualitative_cache/transcripts" / (vid + ".json")
        try:
            saved = json.loads(path.read_text()) if path.exists() else None
            # Failed reads get a six-hour backoff, not an endless retry loop.
            if saved and (saved.get("segments") or (now - when(saved["checked_at"])).total_seconds() < 21600):
                captured = saved
            else:
                try:
                    captured = {**transcript(vid), "checked_at": now.isoformat()}
                except Exception as exc:
                    captured = {"checked_at": now.isoformat(), "error": type(exc).__name__}
                write_json(path, captured)
            if captured.get("segments"):
                row.update({"segments": captured["segments"], "coverage": "transcript", "language": captured["language"], "generated_captions": captured["generated"]})
                status["transcripts"] += 1
            else:
                status["details"].append({"url": row["url"], "title": row["title"], "reason": captured.get("error", "No transcript")})
        except (OSError, ValueError, KeyError) as exc:
            status["details"].append({"url": row["url"], "reason": type(exc).__name__})
        documents.append(row)
    status.update({"items": len(documents), "status": "transcripts" if status["transcripts"] else "description_only" if documents else "no_recent_uploads"})
    return documents, status


def reddit(source, cfg, root, now):
    sub = source["subreddit"]
    status = {"source": source["name"], "kind": "reddit", "items": 0, "comments": 0, "details": [], "status": "rss_posts"}
    try:
        rows = atom(get(f"https://www.reddit.com/r/{sub}/new/.rss?limit={cfg['posts_per_community']}"))
    except Exception as exc:
        rows = []
        status.update({"status": "unavailable", "details": [{"reason": "RSS read failed: " + type(exc).__name__}]})
    documents = []
    for row in rows[:cfg["posts_per_community"]]:
        if not now - timedelta(days=cfg["lookback_days"]) <= when(row["published_at"]) <= now:
            continue
        row.update({"id": "rd:" + hashlib.sha256(row["url"].encode()).hexdigest()[:20], "source": source["name"], "kind": "reddit",
                    "origin_url": row["url"], "fetched_at": now.isoformat(), "coverage": "rss_post"})
        documents.append(row)
    for post in documents[:cfg["comment_threads"]]:
        parsed = urllib.parse.urlparse(post["url"])
        if parsed.hostname not in ("reddit.com", "www.reddit.com") or not re.fullmatch(r"/r/[A-Za-z0-9_]+/comments/[a-z0-9]+/[^/]+/?", parsed.path):
            continue
        try:
            # RSS is a separate public product. No fallback to old.reddit or proxy bypass.
            comments = atom(get("https://www.reddit.com" + parsed.path.rstrip("/") + "/.rss?limit=20"))
            for comment in comments:
                if comment["url"] == post["url"] or not now - timedelta(days=cfg["lookback_days"]) <= when(comment["published_at"]) <= now:
                    continue
                comment.update({"id": "rc:" + hashlib.sha256(comment["url"].encode()).hexdigest()[:20], "source": source["name"], "kind": "reddit",
                                "fetched_at": now.isoformat(), "coverage": "rss_comment", "origin_url": comment["url"]})
                documents.append(comment)
                status["comments"] += 1
        except Exception as exc:
            status["details"].append({"url": post["url"], "reason": type(exc).__name__})
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 429):
                break
    # An optional, already-configured approved OAuth token enables structured comments.
    token = os.environ.get("FPL_REDDIT_ACCESS_TOKEN")
    if token:
        try:
            listing = json.loads(get(f"https://oauth.reddit.com/r/{sub}/new?limit={cfg['posts_per_community']}", headers={"Authorization": "Bearer " + token}))
            for child in listing.get("data", {}).get("children", []):
                p = child["data"]
                published = datetime.fromtimestamp(p["created_utc"], timezone.utc)
                if not now - timedelta(days=cfg["lookback_days"]) <= published <= now:
                    continue
                url = "https://www.reddit.com" + p["permalink"]
                documents.append({"id": "rd:" + str(p["id"]), "source": source["name"], "kind": "reddit", "url": url, "origin_url": url,
                                  "published_at": published.isoformat(), "fetched_at": now.isoformat(), "title": p["title"], "text": p.get("selftext", ""), "coverage": "oauth_post", "author": p.get("author", "")})
            for child in listing.get("data", {}).get("children", [])[:cfg["comment_threads"]]:
                post_id = str(child["data"]["id"])
                if not re.fullmatch(r"[a-z0-9]+", post_id):
                    continue
                reply = json.loads(get(f"https://oauth.reddit.com/comments/{post_id}?limit=30&depth=2", headers={"Authorization": "Bearer " + token}))
                queue = list(reply[1].get("data", {}).get("children", [])) if len(reply) > 1 else []
                count = 0
                while queue and count < 80:
                    child = queue.pop(0)
                    if child.get("kind") != "t1":
                        continue
                    p = child["data"]
                    published = datetime.fromtimestamp(p["created_utc"], timezone.utc)
                    if now - timedelta(days=cfg["lookback_days"]) <= published <= now:
                        url = "https://www.reddit.com" + p["permalink"]
                        documents.append({"id": "rc:" + p["id"], "source": source["name"], "kind": "reddit", "url": url, "origin_url": url,
                                          "published_at": published.isoformat(), "fetched_at": now.isoformat(), "title": "Public discussion comment", "text": p.get("body", ""), "coverage": "oauth_comment", "author": p.get("author", "")})
                        status["comments"] += 1
                    if isinstance(p.get("replies"), dict):
                        queue.extend(p["replies"].get("data", {}).get("children", []))
                    count += 1
            status["status"] = "oauth_and_rss" if rows else "oauth"
        except Exception as exc:
            status["details"].append({"reason": "Approved OAuth read failed: " + type(exc).__name__})
    status["items"] = len(documents)
    return documents, status


def linked_official(url, now):
    """Read a cited official report only when its publication time is explicit."""
    page = get(url).decode("utf-8")
    dates = re.findall(r'"datePublished"\s*:\s*"([^"]+)"', page)
    if not dates:
        dates = re.findall(r'<meta[^>]+(?:property|name)=["\']article:published_time["\'][^>]+content=["\']([^"\']+)', page, re.I)
    if not dates:
        raise DataError("Official page has no verifiable publication timestamp; import its report if needed.")
    published = when(dates[0])
    if not now - timedelta(days=7) <= published <= now:
        raise DataError("Linked official article is outside the recent window.")
    title = re.search(r'<title[^>]*>(.*?)</title>', page, re.S | re.I)
    # Isolate the article/main body and remove script/style content, including ads.
    main = re.search(r'<article\b[^>]*>(.*?)</article>', page, re.S | re.I) or re.search(r'<main\b[^>]*>(.*?)</main>', page, re.S | re.I)
    if not main:
        raise DataError("Official article body was not identifiable; no page-navigation text used.")
    text = re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>', '', main.group(1), flags=re.S | re.I)
    return {"id": "official:" + hashlib.sha256(url.encode()).hexdigest()[:20], "url": url, "origin_url": url, "kind": "official",
            "source": urllib.parse.urlparse(url).hostname, "title": plain(title.group(1)) if title else "Official report", "text": plain(text),
            "published_at": published.isoformat(), "fetched_at": now.isoformat(), "coverage": "official_article"}


def collect(root, progress=lambda message: None, online=True):
    cfg, now = settings(root), when(utc_now())
    path = Path(root) / "data/qualitative_cache/documents.json"
    documents, coverage = [], []
    if online:
        sources = [s for s in cfg["sources"] if s.get("enabled", True)][:20]

        def read(source):
            try:
                return (youtube if source["kind"] == "youtube" else reddit)(source, cfg, root, now)
            except Exception as exc:
                return [], {"source": source["name"], "kind": source["kind"], "items": 0, "status": "unavailable", "details": [{"reason": type(exc).__name__ + ": " + str(exc)[:120]}]}

        progress("Reading public FPL discussions and recent YouTube transcripts")
        with ThreadPoolExecutor(max_workers=3) as pool:
            for docs, status in pool.map(read, sources):
                documents.extend(docs)
                coverage.append(status)
                progress(f"{status['source']}: {status['status']} ({status['items']} items)")
        official_links = list(dict.fromkeys(u.rstrip(".,)") for d in documents for u in d.get("reference_urls", []) if official_url(u)))[:5]
        if official_links:
            status = {"source": "Linked official reports", "kind": "official", "items": 0, "status": "official_articles", "details": []}
            for url in official_links:
                try:
                    documents.append(linked_official(url, now));status["items"] += 1
                except Exception as exc:
                    status["details"].append({"url": url, "reason": type(exc).__name__})
            coverage.append(status)
        if len([s for s in cfg["sources"] if s.get("enabled", True)]) > 20:
            coverage.append({"source": "Refresh limit", "status": "partial", "items": 0, "details": [{"reason": "First 20 enabled sources read this refresh; reorder or disable sources to change coverage."}]})
        # Retain genuine recent captures on an outage, with original fetch timestamps.
        if path.exists():
            old = json.loads(path.read_text())
            documents = [*old.get("documents", []), *documents]
        write_json(path, {"documents": list({r["url"]: r for r in documents if when(r["published_at"]) >= now - timedelta(days=cfg["lookback_days"])}.values()), "coverage": coverage, "created_at": now.isoformat()})
    elif path.exists():
        saved = json.loads(path.read_text())
        documents, coverage = saved["documents"], saved["coverage"]
    enabled_names = {s["name"] for s in cfg["sources"] if s.get("enabled", True)}
    documents = [d for d in documents if d["source"] in enabled_names or d["kind"] == "official"]
    documents.extend(imports(root))
    if imports(root):
        coverage.append({"source": "Imported evidence", "kind": "manual", "status": "imported_text", "items": len(imports(root)), "details": []})
    # Imported source text replaces a metadata-only capture of that same URL.
    documents = list({d["url"]: d for d in documents}.values())
    return {"documents": documents, "coverage": coverage, "settings": cfg, "created_at": now.isoformat(), "online": online}
