"""Explainable football text signals, bounded alternatives and paired live tests.

Extraction is deliberately explicit: ambiguous language is shown for review and
has zero influence. Sentiment does not become a new historical training feature
without genuine dated captures. Prospective learning chooses influence using
earlier gameweeks and tests that choice on later ones.
"""

import gzip
import hashlib
import json
import math
import re
import threading
import unicodedata
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .http import utc_now, write_json
from .qual_sources import collect, plain, when, official_url

VERSION = "qualitative-v1.1"
_LOCK = threading.RLock()
RULES = [
    ("injury_out", -1, r"\b(?:ruled out|will not play|won't play|will miss|unavailable for|suspended for)\b"),
    ("injury_risk", -1, r"\b(?:injured|injury concern|fitness concern|injury doubt|doubtful)\b"),
    ("fitness_positive", 1, r"\b(?:fit again|back in training|passed (?:a |his )?fitness test|cleared to play|available for selection)\b"),
    ("rotation_risk", -1, r"\b(?:rotation risk|will be rested|will be benched|has been dropped|minutes risk|likely to be benched)\b"),
    ("minutes_positive", 1, r"\b(?:nailed starter|guaranteed starter|will start|expected to start|first choice|first-choice)\b"),
    ("set_piece", 1, r"\b(?:on penalties|takes penalties|penalty taker|on corners|takes corners|on free kicks|on free-kicks)\b"),
    ("role_positive", 1, r"\b(?:playing further forward|more attacking role|playing as (?:a |the )?striker|moved up front)\b"),
    ("role_negative", -1, r"\b(?:deeper role|playing deeper|less attacking role|lost penalties|off penalties)\b"),
    ("sentiment_positive", 1, r"\b(?:great picks?|excellent picks?|best picks?|good options?|strong options?|must own|must-have|must have|buy|buying|recommend|i like|i prefer|top picks?|essential|underrated|differential)\b"),
    ("sentiment_negative", -1, r"\b(?:avoid|sell|selling|bad pick|poor pick|overrated|trap)\b"),
    ("captain", 1, r"\b(?:captain|captaincy)\b"),
    ("price_rise", 1, r"\b(?:price rise|price rising|rising in price|going up in price)\b"),
    ("price_fall", -1, r"\b(?:price fall|price drop|falling in price|going down in price)\b"),
]
UNCERTAIN = re.compile(r"\b(?:if|might|could|maybe|perhaps|rumou?r|unconfirmed|apparently|allegedly|suppose|sarcasm|lol|haha)\b|/s\b|\?", re.I)
NEGATION = re.compile(r"\b(?:not|never|isn't|is not|don't|do not|wouldn't|wasn't|no longer)\b", re.I)
REFERENCE = re.compile(r"\b(?:according to|reports say|someone said|he said|she said|they said|heard that)\b", re.I)


def normal(text):
    return re.sub(r"[^a-z0-9 ]", " ", unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()).strip()


def player_aliases(players):
    identities = {}
    for p in players:
        names = {p.get("name", ""), p.get("full_name", "")}
        full = str(p.get("full_name", "")).split()
        if full:
            names.add(full[-1])
            if len(full) > 2:
                names.add(full[0] + " " + full[-1])
        nicknames = {"gibbs white": "MGW", "calvert lewin": "DCL", "alexander arnold": "TAA"}
        for key, nickname in nicknames.items():
            if key in normal(p.get("name", "")):
                names.add(nickname)
        for name in names:
            key = re.sub(r"\s+", " ", normal(name))
            if len(key) >= 3:
                identities.setdefault(key, set()).add(int(p["id"]))
    return [(re.compile(r"(?<!\w)" + re.escape(key) + r"(?!\w)"), next(iter(ids)))
            for key, ids in sorted(identities.items(), key=lambda x: -len(x[0])) if len(ids) == 1]


def passages(doc):
    if doc.get("segments"):
        group, start = [], 0
        for s in doc["segments"]:
            text, at = plain(s["text"]), float(s.get("start", 0))
            if group and (at - start > 20 or len(" ".join(group)) > 500):
                yield " ".join(group), start
                group = []
            if not group:
                start = at
            group.append(text)
        if group:
            yield " ".join(group), start
    else:
        # Headlines never count as spoken video evidence.
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", str(doc.get("text", ""))):
            if sentence.strip():
                yield plain(sentence), None


def evidence(capture, players, manifest, clock=None):
    now = when(clock or utc_now())
    deadline = when(manifest["deadline"])
    lookback = int(capture.get("settings", {}).get("lookback_days", 7))
    aliases, facts, rejected = player_aliases(players), [], []
    seen, duplicates = set(), 0
    for doc in capture.get("documents", []):
        try:
            published, fetched = when(doc["published_at"]), when(doc["fetched_at"])
            if published > fetched or fetched > now or fetched >= deadline or published < now - timedelta(days=lookback):
                continue
        except (ValueError, KeyError, TypeError):
            continue
        age = (now - published).total_seconds() / 3600
        fetch_age = (now - fetched).total_seconds() / 3600
        # A failed refresh can retain context, but not quietly keep influencing choices.
        stale = fetch_age > 24 and not doc.get("manual")
        mentioned_gws = [int(w) for w in re.findall(r"\b(?:GW|gameweek)\s*(\d+)\b", doc.get("title", ""), re.I)]
        if mentioned_gws and int(manifest["next_gameweek"]) not in mentioned_gws:
            continue
        year = manifest["season"].replace("-", "/")
        seasons = re.findall(r"\b20\d{2}[/-]\d{2}\b", doc.get("title", ""))
        if seasons and year not in [s.replace("-", "/") for s in seasons]:
            continue
        reliability = {"official": .95, "youtube": .65, "reddit": .35, "other": .3}.get(doc["kind"], .3)
        origin = doc.get("origin_url") or doc["url"]
        spans = [(part, at) for passage, at in passages(doc) for part in re.split(r"(?<=[.!?])\s+|\s+(?:but|however|whereas)\s+", passage, flags=re.I) if part.strip()]
        for text, at in spans:
            lowered = normal(text)
            ids = {pid for pattern, pid in aliases if pattern.search(lowered)}
            if not ids:
                continue
            found = [(kind, direction, match) for kind, direction, rule in RULES for match in [re.search(rule, text, re.I)] if match]
            if not found:
                continue
            # Multi-player clauses require human attribution rather than spreading one claim to everyone.
            problem = "Multiple players in the same passage" if len(ids) != 1 else "Uncertain, conditional or sarcastic language" if UNCERTAIN.search(text) else None
            if problem:
                rejected.append({"url": doc["url"], "source": doc["source"], "reason": problem, "player_ids": sorted(ids)})
                continue
            pid = next(iter(ids))
            for kind, direction, match in found:
                before = text[max(0, match.start() - 35):match.start()]
                after = text[match.end():match.end() + 25]
                if NEGATION.search(before) or re.search(r"\b(?:is not|isn't|not actually|not true|not confirmed)\b", after, re.I):
                    rejected.append({"url": doc["url"], "source": doc["source"], "reason": "Negated claim", "player_ids": [pid]})
                    continue
                signature = (pid, kind, normal(text))
                if signature in seen:
                    duplicates += 1
                    continue
                seen.add(signature)
                words = text.split()
                excerpt = " ".join(words[:24]) + ("…" if len(words) > 24 else "")
                # Shared cited links identify one original claim, even across creators.
                urls = re.findall(r"https://[^\s<>]+", text)
                cited = next((u.rstrip(".,)") for u in urls if official_url(u)), None)
                independent = cited or origin
                if doc["kind"] == "youtube" and not cited and origin == doc["url"]:
                    independent = "youtube:" + doc["source"].lower()
                if doc["kind"] == "reddit" and not cited:
                    independent = "reddit:" + str(doc.get("author") or doc["source"]).lower()
                confidence = reliability * math.exp(-age / 72)
                if doc.get("generated_captions"):
                    confidence *= .9
                # Descriptions may contain ad-copy: retain as context with zero model influence.
                usable = not stale and doc.get("coverage") != "description_only"
                item = {"player_id": pid, "kind": kind, "direction": direction, "source": doc["source"], "source_kind": doc["kind"],
                        "url": doc["url"], "origin_url": independent, "published_at": doc["published_at"], "fetched_at": doc["fetched_at"],
                        "title": doc.get("title", ""), "excerpt": excerpt, "timestamp": at, "confidence": round(confidence, 4),
                        "coverage": doc.get("coverage"), "stale": stale, "usable": usable, "reported_claim": bool(REFERENCE.search(text)),
                        "official": doc["kind"] == "official" and official_url(doc["url"]),
                        "new_since_stats": published >= when(manifest["created_at"]),
                        "current_out": bool(re.search(r"\b(?:next Premier League (?:match|game|fixture)|GW\s*" + str(manifest["next_gameweek"]) + r"|gameweek\s*" + str(manifest["next_gameweek"]) + r")\b", text, re.I))}
                item["id"] = hashlib.sha256(json.dumps({k: item[k] for k in ("player_id", "kind", "url", "excerpt", "timestamp")}, sort_keys=True).encode()).hexdigest()[:20]
                facts.append(item)
    return {"version": VERSION, "as_of": now.isoformat(), "signals": facts, "review": rejected[:100], "duplicates_removed": duplicates,
            "coverage": capture.get("coverage", []), "settings": capture.get("settings", {}),
            "policy": "Transparent phrase extraction. Ambiguous or negated claims have zero effect; metadata-only videos provide context. Recent independent corroboration is required for football-news adjustments."}


def summarize(signals, player):
    usable = [s for s in signals if s["usable"]]
    official_events = sorted([s for s in usable if s.get("official") and s["kind"] in ("injury_out", "fitness_positive", "minutes_positive")], key=lambda s: s["published_at"])
    latest = official_events[-1] if official_events else {}
    hard_out = bool(latest.get("kind") == "injury_out" and latest.get("current_out") and latest.get("new_since_stats") and latest.get("confidence", 0) >= .5)
    buckets, notes = {}, []
    for s in usable:
        # Several comments or repeated statements by one creator cannot increase their vote.
        bucket = buckets.setdefault(s["kind"], {})
        if s["origin_url"] not in bucket or bucket[s["origin_url"]]["confidence"] < s["confidence"]:
            bucket[s["origin_url"]] = s
    strength = {kind: sum(s["confidence"] * s["direction"] for s in group.values()) / max(1, len(group)) for kind, group in buckets.items()}
    corroborated = {kind: any(s["official"] for s in group.values()) or len([s for s in group.values() if not s["reported_claim"] and s["confidence"] >= .3]) >= 2
                    for kind, group in buckets.items()}
    news = 0.0
    if any(k in buckets for k in ("injury_out", "injury_risk", "rotation_risk")) and any(k in buckets for k in ("fitness_positive", "minutes_positive")):
        notes.append("Conflicting minutes/fitness reports: news adjustment withheld.")
    elif float(player.get("availability", 1)) < 1 and any(k in buckets for k in ("injury_out", "injury_risk", "fitness_positive")):
        notes.append("Official availability already reduces the forecast; fitness claims are context only.")
    else:
        for kind, magnitude in [("injury_out", .2), ("injury_risk", .12), ("rotation_risk", .12), ("minutes_positive", .08), ("fitness_positive", .05)]:
            if corroborated.get(kind):
                news += magnitude * strength[kind]
        news = max(-.2, min(.1, news))
    role_fraction = 0.0
    if not ("role_positive" in buckets and "role_negative" in buckets):
        for kind in ("role_positive", "role_negative", "set_piece"):
            if corroborated.get(kind):
                role_fraction += .03 * strength[kind]
    else:
        notes.append("Conflicting tactical reports: role adjustment withheld.")
    # Roles and set pieces are reviewed evidence until a validated outcome history exists.
    sentiment = strength.get("sentiment_positive", 0) + strength.get("sentiment_negative", 0)
    if "sentiment_positive" in buckets and "sentiment_negative" in buckets:
        notes.append("Community opinions disagree; their votes offset one another.")
    consensus = max(-1, min(1, sentiment))
    for kind in buckets:
        if kind in ("injury_out", "injury_risk", "rotation_risk", "minutes_positive", "fitness_positive", "role_positive", "role_negative", "set_piece") and not corroborated[kind]:
            notes.append(kind.replace("_", " ") + ": independent corroboration needed.")
    if hard_out:
        notes.append("Fresh official report rules this player out of the next match: next-GW points and minutes set to zero in qualitative views.")
    return {"signals": signals, "signal_count": len(signals), "usable_count": len(usable), "consensus": round(consensus, 4),
            "role_fraction": round(role_fraction, 5), "hard_out": hard_out,
            "news_fraction": round(news, 5), "corroborated": corroborated, "notes": notes,
            "confidence": max((s["confidence"] for s in usable), default=0),
            "roles": [k for k in ("set_piece", "role_positive", "role_negative") if k in buckets],
            "price_context": [k for k in ("price_rise", "price_fall") if k in buckets],
            "captain_mentions": len(buckets.get("captain", {}))}


def apply_evidence(players, weeks, report, discount=.8, learned_gain=0):
    grouped = {}
    for s in report["signals"]:
        grouped.setdefault(s["player_id"], []).append(s)
    candidate, adjusted, details = [], [], {}
    gw = int(weeks[0])
    for player in players:
        row = dict(player)
        info = summarize(grouped.get(int(row["id"]), []), row)
        base = float(row[f"gw{gw}"])
        games = int(row.get(f"games{gw}", 0))
        sentiment = max(-.25 * games, min(.25 * games, .05 * base * info["consensus"]))
        delta = base * (info["news_fraction"] + info["role_fraction"]) + sentiment if games and base > 0 else 0.0
        delta = round(max(-.2 * base, min(.1 * base, delta)), 3)
        if info["hard_out"]:
            delta = -base
        learned_delta = delta if info["hard_out"] else round(float(learned_gain) * delta, 3)
        info.update({"stats_points": base, "experimental_points": round(max(0, base + delta), 3), "experimental_delta": delta,
                     "learned_delta": learned_delta, "expected_minutes_proxy": 0 if info["hard_out"] else round(min(90, float(row.get("minutes_recent", 0)) * (1 + info["news_fraction"])), 1)})
        if info["signal_count"]:
            details[str(row["id"])] = info
        for target, gain in [(candidate, 1), (adjusted, learned_gain)]:
            changed = dict(row)
            changed[f"gw{gw}"] = round(max(0, base + (delta if info["hard_out"] else float(gain) * delta)), 3)
            changed["next_points"] = changed[f"gw{gw}"]
            changed["horizon_points"] = round(sum(float(changed[f"gw{w}"]) for w in weeks), 3)
            changed["weighted_points"] = round(sum(discount ** i * float(changed[f"gw{w}"]) for i, w in enumerate(weeks)), 3)
            target.append(changed)
    report["players"] = details
    report["affected_players"] = sum(abs(p["experimental_delta"]) > 0 for p in details.values())
    report["learned_gain"] = float(learned_gain)
    report["discount"] = float(discount)
    report["experimental_note"] = "Soft adjustments are experimental until later-gameweek tests demonstrate lower errors. A fresh official report explicitly ruling a player out of the next match sets next-GW points to zero in qualitative views. Other changes are bounded and affect the next GW only; future recovery and price movements are not predicted."
    return candidate, adjusted


def archive_pair(root, snapshot):
    manifest, report = snapshot["manifest"], snapshot.get("qualitative", {})
    if not report or report.get("online_capture") is False or manifest.get("cached_run") or manifest.get("offline_run") or not manifest.get("live_refresh"):
        return False
    as_of, deadline = when(report["as_of"]), when(manifest["deadline"])
    if as_of >= deadline or when(manifest["created_at"]) > as_of:
        return False
    baseline = {int(p["id"]): p for p in snapshot["players"]}
    candidates = snapshot.get("qualitative_candidate_players", snapshot["players"])
    rows = []
    for p in candidates:
        b = baseline[int(p["id"])]
        if not b.get("code"):
            continue
        info = report["players"].get(str(p["id"]), {"expected_minutes_proxy": float(b.get("minutes_recent",0)), "hard_out": False, "signals": []})
        rows.append({"id": int(p["id"]), "code": int(b["code"]), "position": b["position"], "stats": float(b["next_points"]), "candidate": float(p["next_points"]),
                     "minutes_recent": float(b.get("minutes_recent", 0)), "minutes_proxy": info["expected_minutes_proxy"], "games": int(b.get(f"games{manifest['next_gameweek']}", 0)),
                     "availability": float(b.get("availability", 1)), "hard_out": info["hard_out"], "signal_ids": [s["id"] for s in info["signals"]],
                     "signals": [{k: s[k] for k in ("kind", "source_kind", "confidence", "published_at", "fetched_at", "usable", "id")} for s in info["signals"]]})
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    record = {"schema": 1, "version": VERSION, "season": manifest["season"], "gw": int(manifest["next_gameweek"]),
              "created_at": report["as_of"], "deadline": manifest["deadline"], "stats_created_at": manifest.get("forecast_created_at", manifest["created_at"]),
              "trained_through": manifest.get("model_trained_through", 0), "base_model": manifest.get("model_version"), "base_fingerprint": manifest.get("model_fingerprint"),
              "evidence_fingerprint": digest, "used_gain": report["learned_gain"], "rows": rows}
    record["extractor_fingerprint"] = report.get("extractor_fingerprint")
    path = Path(root) / "data/qualitative_archive" / manifest["season"] / f"gw{record['gw']:02d}.json.gz"
    if path.exists():
        old = json.loads(gzip.decompress(path.read_bytes()))
        if when(old["created_at"]) >= as_of:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(record, separators=(",", ":"), allow_nan=False).encode(), mtime=0))
    tmp.replace(path)
    return True


def evaluate(root, history, manifest, minimum_gameweeks=3):
    from .data import season_order
    actual = history[(history.season == manifest["season"]) & history.gw.isin(manifest["completed_gameweeks"])].set_index(["element", "gw"])
    matched, warnings = [], []
    for path in sorted((Path(root) / "data/qualitative_archive" / manifest["season"]).glob("*.json.gz")):
        try:
            pair = json.loads(gzip.decompress(path.read_bytes()))
            gw = int(pair["gw"])
            if pair.get("version") != VERSION or pair.get("season") != manifest["season"] or gw not in manifest["completed_gameweeks"]:
                continue
            created = when(pair["created_at"])
            if created >= when(pair["deadline"]) or when(pair["stats_created_at"]) > created or int(pair["trained_through"]) >= season_order(manifest["season"], gw):
                continue
            for row in pair["rows"]:
                if (row["id"], gw) not in actual.index:
                    continue
                a = actual.loc[(row["id"], gw)]
                if row["code"] != int(a.code) or any(when(s["fetched_at"]) > created or when(s["published_at"]) > when(s["fetched_at"]) for s in row["signals"]):
                    continue
                nums = [float(row[k]) for k in ("stats", "candidate", "minutes_proxy", "minutes_recent")]
                if not np.isfinite([*nums, float(a.total_points), float(a.minutes)]).all():
                    continue
                matched.append({"gw": gw, "element": row["id"], "code": row["code"], "position": row["position"], "stats": row["stats"],
                                "candidate": row["candidate"], "actual": float(a.total_points), "used_gain": float(pair["used_gain"]),
                                "minutes_recent": row["minutes_recent"], "minutes_proxy": row["minutes_proxy"], "actual_minutes": float(a.minutes) / max(1, row["games"]),
                                "hard_out": bool(row.get("hard_out",False)), "created_at": pair["created_at"], "evidence_fingerprint": pair["evidence_fingerprint"]})
        except (ValueError, KeyError, TypeError, OSError) as exc:
            warnings.append(f"Invalid qualitative archive {path.name}: {type(exc).__name__}")
    frame = pd.DataFrame(matched)
    report = {"version": VERSION, "matched_rows": len(frame), "gameweeks": [], "player_predictions": {}, "recommended_gain": 0, "warnings": warnings,
              "status": "Waiting for finalized outcomes of paired forecasts saved before deadlines. Stats remain the default.",
              "policy": "Learn on earlier affected-player GWs and assess that choice on later GWs. Require at least three GWs, 30 affected outcomes and >=0.5% lower forward RMSE without higher MAE. No retrospective sentiment enters historical training."}
    if frame.empty:
        return report, frame
    for element, rows in frame.groupby("element"):
        report["player_predictions"][str(int(element))] = [{"gameweek": int(r.gw), "stats": float(r.stats), "experimental": float(r.candidate), "actual": float(r.actual)} for r in rows.sort_values("gw").itertuples()]

    def score(rows, gain):
        from .model import metrics
        return metrics(rows.actual, rows.stats + gain * (rows.candidate - rows.stats))

    for gw, rows in frame.groupby("gw"):
        active = rows[abs(rows.candidate - rows.stats) > .0005]
        report["gameweeks"].append({"gameweek": int(gw), "rows": len(rows), "affected": len(active), "stats": score(rows, 0), "experimental": score(rows, 1),
                                   "affected_stats": score(active, 0) if len(active) else None, "affected_experimental": score(active, 1) if len(active) else None,
                                   "minutes_stats_mae": float(abs(active.actual_minutes - active.minutes_recent).mean()) if len(active) else None,
                                   "minutes_proxy_mae": float(abs(active.actual_minutes - active.minutes_proxy).mean()) if len(active) else None})
    affected = frame[(abs(frame.candidate - frame.stats) > .0005) & ~frame.hard_out].copy()
    weeks = sorted(map(int, affected.gw.unique()))
    if len(weeks) < max(3, int(minimum_gameweeks)) or len(affected) < 30:
        report["status"] = f"{len(weeks)} finalized GWs and {len(affected)} affected outcomes; at least {max(3, int(minimum_gameweeks))} GWs and 30 affected outcomes needed before influence can be learned."
        return report, frame
    validation = []
    for gw in weeks[2:]:
        prior, later = affected[affected.gw < gw], affected[affected.gw == gw].copy()
        choices = [(gain, score(prior, gain)) for gain in (0, .25, .5, 1)]
        gain, result = min(choices, key=lambda x: (x[1]["rmse"], x[0]))
        base = score(prior, 0)
        if result["rmse"] > .995 * base["rmse"] or result["mae"] > base["mae"]:
            gain = 0
        later["forward_prediction"] = later.stats + gain * (later.candidate - later.stats)
        later["chosen_gain"] = gain
        validation.append(later)
    valid = pd.concat(validation, ignore_index=True)
    from .model import metrics
    measured = metrics(valid.actual, valid.forward_prediction)
    baseline = score(valid, 0)
    choices = [(gain, score(affected, gain)) for gain in (0, .25, .5, 1)]
    gain, chosen = min(choices, key=lambda x: (x[1]["rmse"], x[0]))
    full_base = score(affected, 0)
    passed = (measured["rmse"] <= .995 * baseline["rmse"] and measured["mae"] <= baseline["mae"]
              and chosen["rmse"] <= .995 * full_base["rmse"] and chosen["mae"] <= full_base["mae"])
    report.update({"recommended_gain": gain if passed else 0, "forward_test": {"gameweeks": list(map(int, valid.gw.unique())), "stats": baseline, "chosen_on_earlier_gws": measured},
                   "status": "Qualitative influence passed the chronological live check." if passed else "Qualitative influence did not improve later-GW errors sufficiently; stats remain the default."})
    return report, frame


def build(root, players, manifest, history, config, online=True, progress=lambda message: None):
    with _LOCK:
        try:
            capture = collect(root, progress, online)
        except Exception as exc:
            progress("Qualitative collection unavailable; retaining statistical forecasts.")
            capture = {"documents": [], "settings": {}, "coverage": [{"source": "Evidence collection", "status": "unavailable", "items": 0, "details": [{"reason": type(exc).__name__}]}]}
        report = evidence(capture, players, manifest)
        report.update({"season": manifest["season"], "origin_gw": manifest["next_gameweek"], "stats_created_at": manifest["created_at"], "stats_fingerprint": manifest.get("model_fingerprint")})
        report["extractor_fingerprint"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        report["online_capture"] = bool(online)
        learning, matched = evaluate(root, history, manifest, config.get("feedback_min_gameweeks", 3))
        candidates, learned = apply_evidence(players, manifest["forecast_weeks"], report, config.get("discount", .8), learning["recommended_gain"])
        report["learning"] = learning
        reports = Path(root) / "reports"
        write_json(reports / "qualitative.json", report)
        if not matched.empty:
            matched.to_csv(reports / "qualitative_results.csv.tmp", index=False)
            (reports / "qualitative_results.csv.tmp").replace(reports / "qualitative_results.csv")
        else:
            (reports / "qualitative_results.csv").unlink(missing_ok=True)
        return report, candidates, learned
