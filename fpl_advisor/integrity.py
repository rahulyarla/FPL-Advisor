"""Public deadline-pick similarity screening. No inference of guilt or copying.

Compare every A entry independently with every B entry. Team identities, public
picks and results stay under ignored user/, independently of model/rival data.
"""

import csv
import hashlib
import io
import json
import math
import re
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .data import API, season_from_bootstrap
from .http import DataError, utc_now, write_json
from .rivals import parse_entry_id

METRICS = {"squad": "15-player squad overlap", "xi": "Starting XI overlap", "combined": "Combined similarity"}
NOTE = "Similarity can reflect template picks, shared advice or chance. These percentages are overlap measures, not probabilities of cheating or findings of misconduct."
_BOOT_LOCK = threading.Lock()


def date(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Expected a timestamp with a time zone.")
    return dt.astimezone(timezone.utc)


def ids(value):
    if isinstance(value, str):
        value = [s for s in re.split(r"[\s,;]+", value.strip()) if s]
    if not isinstance(value, list) or not value:
        raise DataError("Enter at least one entry ID in each list.")
    result = []
    for item in value:
        if len(str(item)) > 300:
            raise DataError("An entry ID or official team link is too long.")
        entry = parse_entry_id(item)
        if entry > 999_999_999_999:
            raise DataError("Entry IDs must be no more than 12 digits.")
        if entry not in result:
            result.append(entry)
    return result


def integer(value, name, low, high):
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)) or not low <= int(value) <= high:
        raise DataError(f"{name} must be a whole number from {low} to {high}.")
    return int(value)


def percent(value, name):
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise DataError(f"{name} must be between 0 and 100%.") from None
    if isinstance(value, bool) or not math.isfinite(number) or not 0 <= number <= 100:
        raise DataError(f"{name} must be between 0 and 100%.")
    return number


def settings(payload, latest):
    a, b = ids(payload.get("group_a")), ids(payload.get("group_b"))
    if len(a) * len(b) > 10_000:
        raise DataError("This would create over 10,000 pairs. Split the lists into smaller batches.")
    start = integer(payload.get("start_gw", 1), "First GW", 1, latest)
    end = integer(payload.get("end_gw", latest), "Last GW", 1, latest)
    if start > end:
        raise DataError("First GW must not be after Last GW.")
    metric = payload.get("metric", "squad")
    if metric not in METRICS:
        raise DataError("Choose squad, starting XI or combined similarity.")
    return {"group_a": a, "group_b": b, "start_gw": start, "end_gw": end, "metric": metric,
            "threshold": percent(payload.get("threshold", 80), "Similarity threshold"),
            "min_matching_week_percent": percent(payload.get("min_matching_week_percent", 0), "Repeated-week requirement")}


class Cancelled(DataError):
    pass


class PublicReader:
    """Short-lived private cache; no retry/bypass on denied or rate-limited reads."""
    def __init__(self, root, cancelled=None, force=False):
        self.root = Path(root) / "user/integrity/http"
        self.cancelled = cancelled or threading.Event()
        self.force = force
        self.season = "metadata"

    def json(self, suffix, ttl=900):
        if self.cancelled.is_set():
            raise Cancelled("Check cancelled. Previous results are retained.")
        path = self.root / (hashlib.sha256((self.season + ":" + suffix).encode()).hexdigest() + ".json")
        if path.exists() and not self.force:
            try:
                saved = json.loads(path.read_text())
                if 0 <= (date(utc_now()) - date(saved["fetched_at"])).total_seconds() < ttl:
                    return saved["value"]
            except (ValueError, KeyError, OSError):
                pass
        try:
            req = urllib.request.Request(API + suffix, headers={"User-Agent": "FPL-Advisor/3.2 (public deadline comparison)"})
            with urllib.request.urlopen(req, timeout=20) as response:
                body = response.read(6 * 1024 * 1024 + 1)
            if len(body) > 6 * 1024 * 1024:
                raise DataError("FPL response exceeded the read limit.")
            result = json.loads(body)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                self.cancelled.set()
                raise DataError(f"FPL returned HTTP {exc.code}. Public reads stopped; try again later.") from exc
            raise DataError(f"FPL returned HTTP {exc.code}.") from exc
        except (OSError, ValueError) as exc:
            raise DataError("Could not read public FPL history. Try again later.") from exc
        if self.cancelled.is_set():
            raise Cancelled("Check cancelled. Previous results are retained.")
        write_json(path, {"fetched_at": utc_now(), "value": result})
        return result


def options(root, reader=None, now=None):
    with _BOOT_LOCK:
        bootstrap = (reader or PublicReader(root)).json("bootstrap-static/", ttl=60)
    now = now or datetime.now(timezone.utc)
    weeks = [e for e in bootstrap["events"] if date(e["deadline_time"]) <= now]
    if not weeks:
        raise DataError("No gameweek deadline has passed in this season yet.")
    return {"season": season_from_bootstrap(bootstrap), "latest_gw": max(int(e["id"]) for e in weeks),
            "events": [{"id": int(e["id"]), "deadline": e["deadline_time"], "finished": bool(e.get("finished"))} for e in weeks],
            "players": {str(p["id"]): {"name": p["web_name"], "position": p.get("element_type")} for p in bootstrap["elements"]},
            "checked_at": utc_now(), "note": NOTE}


def snapshot(payload, gw):
    if not isinstance(payload, dict) or not isinstance(payload.get("picks"), list) or not all(isinstance(p, dict) for p in payload["picks"]):
        raise DataError("FPL returned an invalid public squad response.")
    picks = payload.get("picks", [])
    if (len(picks) != 15 or any(type(p.get("position")) is not int or type(p.get("element")) is not int for p in picks)
            or len({p.get("element") for p in picks}) != 15
            or {p.get("position") for p in picks} != set(range(1, 16))
            or any(not isinstance(p.get("element"), int) or p["element"] <= 0 for p in picks)
            or payload.get("entry_history", {}).get("event") != gw):
        raise DataError("The public snapshot does not contain a valid 15-player deadline squad for this GW.")
    ordered = sorted(picks, key=lambda p: p["position"])
    captains = [p["element"] for p in picks if p.get("is_captain") is True]
    vice = [p["element"] for p in picks if p.get("is_vice_captain") is True]
    return {"squad": [p["element"] for p in ordered], "xi": [p["element"] for p in ordered[:11]],
            "bench": [p["element"] for p in ordered[11:]], "captain": captains[0] if len(captains) == 1 else None,
            "vice_captain": vice[0] if len(vice) == 1 else None, "chip": payload.get("active_chip")}


def load_entry(entry, cfg, metadata, reader, progress=lambda message: None):
    result = {"id": entry, "name": f"Entry {entry}", "weeks": {}, "warnings": []}
    try:
        profile = reader.json(f"entry/{entry}/")
        if not isinstance(profile, dict) or not profile.get("name"):
            raise DataError("A valid public entry profile was not returned.")
        result["name"] = str(profile.get("name") or result["name"])
        joined = int(profile.get("started_event") or 1)
    except Cancelled:
        raise
    except DataError as exc:
        if reader.cancelled.is_set():
            raise
        result["warnings"].append({"gw": None, "reason": str(exc)})
        return result
    # One earlier deadline provides context for inferred squad changes.
    for gw in range(max(1, cfg["start_gw"] - 1), cfg["end_gw"] + 1):
        if reader.cancelled.is_set():
            raise Cancelled("Check cancelled. Previous results are retained.")
        if gw < joined:
            result["warnings"].append({"gw": gw, "reason": "Entry had not joined this season yet."})
            continue
        try:
            body = reader.json(f"entry/{entry}/event/{gw}/picks/")
            result["weeks"][str(gw)] = snapshot(body, gw)
        except Cancelled:
            raise
        except (DataError, ValueError, KeyError, TypeError) as exc:
            if reader.cancelled.is_set():
                raise DataError("FPL access was interrupted; no new report was saved.") from exc
            result["warnings"].append({"gw": gw, "reason": str(exc)})
        progress(f"Reading published squads: GW{gw}")
    return result


def moves(entry, gw):
    now, before = entry["weeks"].get(str(gw)), entry["weeks"].get(str(gw - 1))
    if not now or not before or now["chip"] == "freehit" or before["chip"] == "freehit":
        return None
    return {"in": sorted(set(now["squad"]) - set(before["squad"])), "out": sorted(set(before["squad"]) - set(now["squad"]))}


def weekly(a, b, gw):
    left, right = a["weeks"].get(str(gw)), b["weeks"].get(str(gw))
    if not left or not right:
        return {"gw": gw, "available": False, "reason": "One or both public squads are unavailable."}
    squad, xi = sorted(set(left["squad"]) & set(right["squad"])), sorted(set(left["xi"]) & set(right["xi"]))
    captain = None if left["captain"] is None or right["captain"] is None else int(left["captain"] == right["captain"]) * 100
    vice = None if left["vice_captain"] is None or right["vice_captain"] is None else int(left["vice_captain"] == right["vice_captain"]) * 100
    squad_pct, xi_pct = 100 * len(squad) / 15, 100 * len(xi) / 11
    ma, mb = moves(a, gw), moves(b, gw)
    changes = None
    if ma is not None and mb is not None:
        sa = {(direction, p) for direction in ("in", "out") for p in ma[direction]}
        sb = {(direction, p) for direction in ("in", "out") for p in mb[direction]}
        # Two teams making no moves are not a 100% matching transfer decision.
        if sa | sb:
            changes = 100 * len(sa & sb) / len(sa | sb)
    return {"gw": gw, "available": True, "squad": squad_pct, "xi": xi_pct, "captain": captain, "vice_captain": vice,
            "combined": None if captain is None else .6 * squad_pct + .3 * xi_pct + .1 * captain,
            "shared_squad": squad, "shared_xi": xi, "left_only": sorted(set(left["squad"]) - set(right["squad"])),
            "right_only": sorted(set(right["squad"]) - set(left["squad"])), "bench_order": 25 * sum(x == y for x, y in zip(left["bench"], right["bench"])),
            "left_captain": left["captain"], "right_captain": right["captain"], "left_chip": left["chip"], "right_chip": right["chip"],
            "left_moves": ma, "right_moves": mb, "changes": changes}


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def summarize(a, b, cfg, details=False):
    rows = [weekly(a, b, gw) for gw in range(cfg["start_gw"], cfg["end_gw"] + 1)]
    metric, threshold = cfg["metric"], cfg["threshold"]
    valid = [r for r in rows if r["available"] and r[metric] is not None]
    average = mean([r[metric] for r in valid])
    hits = sum(r[metric] + 1e-9 >= threshold for r in valid)
    streak = longest = 0
    for r in rows:
        streak = streak + 1 if r["available"] and r[metric] is not None and r[metric] + 1e-9 >= threshold else 0
        longest = max(longest, streak)
    hit_rate = 100 * hits / len(valid) if valid else None
    complete = len(valid) == len(rows)
    meets = (average + 1e-9 >= threshold and hit_rate + 1e-9 >= cfg["min_matching_week_percent"]) if complete else None
    item = {"a": a["id"], "b": b["id"], "a_name": a["name"], "b_name": b["name"], "average": average,
            "latest": rows[-1].get(metric), "compared_weeks": len(valid), "requested_weeks": len(rows), "complete": complete,
            "weeks_above": hits, "weeks_above_percent": hit_rate, "longest_streak": longest, "meets_threshold": meets,
            "metrics": {key: mean([r.get(key) for r in rows if r["available"]]) for key in ("squad", "xi", "captain", "vice_captain", "combined", "bench_order", "changes")},
            "exact_squad_weeks": sum(len(r.get("shared_squad", [])) == 15 for r in rows),
            "status": "Insufficient coverage" if not complete else "Threshold met" if meets else "Below threshold"}
    if details:
        item["weeks"] = rows
    return item


def analyse(dataset, cfg):
    entries = dataset["entries"]
    pairs = [summarize(entries[str(a)], entries[str(b)], cfg) for a in cfg["group_a"] for b in cfg["group_b"] if a != b]
    pairs.sort(key=lambda p: (p["average"] is None, -(p["average"] or 0), p["a"], p["b"]))
    return {"schema": 1, "season": dataset["season"], "created_at": utc_now(), "source_checked_at": dataset["checked_at"],
            "settings": cfg, "pairs": pairs, "players": dataset["players"], "events": dataset["events"],
            "self_pairs_skipped": sum(a == b for a in cfg["group_a"] for b in cfg["group_b"]),
            "matched_pairs": sum(p["meets_threshold"] is True for p in pairs),
            "incomplete_pairs": sum(not p["complete"] for p in pairs),
            "warnings": [{"entry": e["id"], **w} for e in entries.values() for w in e["warnings"]], "note": NOTE,
            "definitions": {"squad": "100 × common squad players / 15", "xi": "100 × common players in positions 1–11 / 11",
                            "combined": "60% squad overlap + 30% XI overlap + 10% captain agreement",
                            "changes": "Jaccard similarity of inferred incoming/outgoing players; no-action and Free Hit transitions excluded.",
                            "decision": "Average reaches the similarity threshold AND the share of matching weeks reaches the repeated-week requirement. All requested weeks must be comparable."}}


class SimilarityJob:
    def __init__(self, root):
        self.root, self.lock, self.cancelled = Path(root), threading.RLock(), threading.Event()
        self.current = {"running": False, "message": "", "error": None, "finished_at": None}

    def status(self):
        with self.lock:
            return dict(self.current)

    def report(self):
        with self.lock:
            path = self.root / "user/integrity/latest.json"
            return json.loads(path.read_text()) if path.exists() else {"ready": False}

    def _dataset(self):
        path = self.root / "user/integrity/dataset.json"
        if not path.exists():
            raise DataError("Run a check to load the public history first.")
        return json.loads(path.read_text())

    def start(self, payload):
        # Reject malformed IDs/percentages immediately, before any network read.
        settings(payload, 100)
        if not isinstance(payload.get("refresh", False), bool):
            raise DataError("Refresh public history must be on or off.")
        with self.lock:
            if self.current["running"]:
                raise DataError("A similarity check is already running.")
            self.cancelled = threading.Event()
            self.current = {"running": True, "message": "Checking official published deadlines…", "error": None, "finished_at": None}

        def progress(message):
            with self.lock:
                self.current["message"] = message

        def work():
            try:
                reader = PublicReader(self.root, self.cancelled, force=payload.get("refresh") is True)
                meta = options(self.root, reader)
                reader.season = meta["season"]
                cfg = settings(payload, meta["latest_gw"])
                unique = list(dict.fromkeys(cfg["group_a"] + cfg["group_b"]))
                entries = {}
                with ThreadPoolExecutor(max_workers=3) as pool:
                    pending = {pool.submit(load_entry, entry, cfg, meta, reader, progress): entry for entry in unique}
                    for future in as_completed(pending):
                        result = future.result()
                        entries[str(result["id"])] = result
                        progress(f"Loaded {len(entries)} of {len(unique)} entries")
                if self.cancelled.is_set():
                    raise Cancelled("Check cancelled. Previous results are retained.")
                dataset = {**meta, "entries": entries, "loaded_start": cfg["start_gw"], "loaded_end": cfg["end_gw"]}
                progress("Comparing each A entry separately with each B entry…")
                result = {"ready": True, **analyse(dataset, cfg)}
                with self.lock:
                    if self.cancelled.is_set():
                        raise Cancelled("Check cancelled. Previous results are retained.")
                    write_json(self.root / "user/integrity/dataset.json", dataset)
                    write_json(self.root / "user/integrity/latest.json", result)
            except Exception as exc:
                with self.lock:
                    self.current["error"] = str(exc)
            finally:
                with self.lock:
                    self.current.update({"running": False, "finished_at": utc_now(), "message": "Check finished."})

        threading.Thread(target=work, daemon=True).start()
        return {"started": True}

    def cancel(self):
        with self.lock:
            self.cancelled.set()
            self.current["message"] = "Stopping after current public reads…"
        return self.status()

    def recalculate(self, payload):
        with self.lock:
            if self.current["running"]:
                raise DataError("Wait for the current check before applying new thresholds.")
            dataset = self._dataset()
            old = self.report()
            cfg = settings({**old["settings"], **{k: v for k, v in payload.items() if k in ("start_gw", "end_gw", "metric", "threshold", "min_matching_week_percent")}}, max(e["id"] for e in dataset["events"]))
            if cfg["start_gw"] < dataset["loaded_start"] or cfg["end_gw"] > dataset["loaded_end"]:
                raise DataError("Run a new check to load the larger gameweek range.")
            result = {"ready": True, **analyse(dataset, cfg)}
            write_json(self.root / "user/integrity/latest.json", result)
            return result

    def pair(self, payload):
        with self.lock:
            report, dataset = self.report(), self._dataset()
            a, b = parse_entry_id(payload.get("a")), parse_entry_id(payload.get("b"))
            cfg = report["settings"]
            if a not in cfg["group_a"] or b not in cfg["group_b"] or a == b:
                raise DataError("Choose a pair from the completed comparison.")
            result = summarize(dataset["entries"][str(a)], dataset["entries"][str(b)], cfg, details=True)
            return {**result, "season": report["season"], "settings": cfg, "players": report["players"], "note": NOTE}

    def csv(self):
        report = self.report()
        if not report.get("ready"):
            raise DataError("Run a check before downloading results.")
        output = io.StringIO(newline="")
        fields = ["season", "start_gw", "end_gw", "a", "b", "metric", "average", "latest", "compared_weeks", "requested_weeks", "threshold", "weeks_above", "weeks_above_percent", "longest_streak", "status"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for pair in report["pairs"]:
            row = {**pair, **report["settings"], "season": report["season"]}
            # Use numeric entry IDs, not user-controlled team names in spreadsheet cells.
            writer.writerow({key: row.get(key) for key in fields})
        return output.getvalue()
