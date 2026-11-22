"""Builds the website into a folder (normally site/).

    python update_jobs.py site [URL of yesterday's jobs.json]

Searches LinkedIn for every country x keyword group, scores each job title,
fetches full details for good matches, and writes site/jobs.json next to
index.html and icon.png.  Standard library only.
Environment: PAGES (pages per search, default 3), MIN_GAP (seconds, default 1.5).
"""
import json
import os
import re
import shutil
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import job_finder as lf

# ======================= SETTINGS =======================

CORE_KEYWORDS = [
    "Planning Manager", "Planning Engineer", "Project Controls", "Project Planner",
    "Primavera P6", "Delay Analysis", "Claims Manager", "Claims Consultant",
]
RELATED_KEYWORDS = [
    "Contracts Manager", "PMO Manager", "Project Risk Manager", "Cost Control",
    "Dispute Resolution", "Project Management Consultant", "Project Assurance",
]
# Stretch roles (not searched): Construction Data Analyst, 4D BIM Planner,
# Investment Analyst, ML Research Engineer, Circular Construction.
KEYWORDS = CORE_KEYWORDS + RELATED_KEYWORDS
TERMS_PER_QUERY = 5            # longer OR-queries can hit LinkedIn's sign-in wall

COUNTRIES = {                  # code -> LinkedIn geoId
    "EG": "106155005",         # Egypt
    "AE": "104305776",         # United Arab Emirates
    "SA": "100459316",         # Saudi Arabia
    "QA": "104170880",         # Qatar
    "KW": "103239229",         # Kuwait
    "BH": "100425729",         # Bahrain
    "OM": "103619019",         # Oman
    "IT": "103350119",         # Italy
}

LEVELS = "4,5"                 # 2 Entry, 3 Associate, 4 Mid-Senior, 5 Director, 6 Executive
POSTED = "r2592000"            # last month
PAGES = int(os.environ.get("PAGES", "3"))
lf.MIN_GAP = float(os.environ.get("MIN_GAP", "1.5"))

# Title score: every rule whose pattern appears in the title adds its weight.
SCORING = [
    (5, r"planning (manager|director|lead|head)|head of (planning|project controls?)|"
        r"project controls? (manager|lead|director)|claims? (manager|lead|director|consultant|expert)|"
        r"delay analy|forensic"),
    (4, r"planning engineer|planner|schedul|project controls?|claims?\b|delay|primavera"),
    (3, r"contracts? (manager|administrat|engineer|lead|specialist)|\bpmo\b|project risk|"
        r"risk manager|cost control|dispute|assurance|\beot\b|extension of time"),
    (2, r"senior|\bsr\b|\blead\b|principal|manager|\bhead\b|director|chief"),
    (2, r"construction|infrastructure|\bepc\b|capital project|\boil\b|\bgas\b|data cent|"
        r"railway|metro|giga"),
    (-6, r"\bintern\b|internship|trainee|junior|\bjr\b|graduate|student|fresher"),
    (-4, r"(demand|supply|production|financial|media|event|sales|marketing|merchandis\w*|"
         r"inventory|wedding|travel|tax|wealth|retirement) planner|insurance|adjuster|"
         r"claims? (handler|processor|officer|examiner)|medical|underwrit"),
]
STRONG, GOOD = 6, 3            # Strong >= 6, Good >= 3, Possible below 3
DETAILS_MIN_SCORE = GOOD       # fetch full details only for these

# ========================================================

HERE = os.path.dirname(os.path.abspath(__file__))
_RULES = [(w, re.compile(p, re.I)) for w, p in SCORING]


def score(title):
    return sum(w for w, rx in _RULES if rx.search(title or ""))


def match_level(s):
    return "strong" if s >= STRONG else "good" if s >= GOOD else "possible"


def chunks(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


def load_previous(url):
    if not url:
        return None
    sep = "&" if "?" in url else "?"
    try:
        req = urllib.request.Request(url + sep + "t=%d" % time.time(), headers=lf.HEADERS)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print("No previous jobs.json (%s) - first run?" % e)
        return None


def run_search(country, geo, query):
    found = []
    for page in range(PAGES):
        got = lf.search(query, geo, posted=POSTED, levels=LEVELS, start=page * 10)
        found += got
        if len(got) < 10:
            break
    return country, query, found


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    out = sys.argv[1] if len(sys.argv) > 1 else "site"
    prev = load_previous(sys.argv[2] if len(sys.argv) > 2 else "")
    prev_ids = {j["id"] for j in (prev or {}).get("jobs", [])}
    prev_details = (prev or {}).get("details", {})

    queries = [" OR ".join('"%s"' % k for k in c) for c in chunks(KEYWORDS, TERMS_PER_QUERY)]
    tasks = [(c, g, q) for c, g in COUNTRIES.items() for q in queries]
    print("Searching %d countries x %d keyword groups, up to %d pages each..."
          % (len(COUNTRIES), len(queries), PAGES))

    jobs, failed, per_country = {}, [], {c: 0 for c in COUNTRIES}
    with ThreadPoolExecutor(3) as ex:
        futs = [ex.submit(run_search, *t) for t in tasks]
        for t, f in zip(tasks, futs):
            try:
                country, query, found = f.result()
            except Exception as e:
                failed.append("%s: %s (%s)" % (t[0], t[2], e))
                print("  FAILED", failed[-1])
                continue
            for j in found:
                if j["id"] in jobs:
                    continue
                j["country"] = country
                j["score"] = score(j["title"])
                j["match"] = match_level(j["score"])
                j["isNew"] = bool(prev_ids) and j["id"] not in prev_ids
                jobs[j["id"]] = j
                per_country[country] += 1

    if not jobs:
        print("ERROR: zero jobs found - keeping yesterday's site.")
        sys.exit(1)

    # Full details for good matches (reuse yesterday's where possible)
    details = {}
    need = []
    for jid, j in jobs.items():
        if j["score"] < DETAILS_MIN_SCORE:
            continue
        if jid in prev_details:
            details[jid] = prev_details[jid]
        else:
            need.append(jid)
    print("Details: %d reused, %d to fetch" % (len(details), len(need)))
    with ThreadPoolExecutor(3) as ex:
        for jid, d in zip(need, ex.map(lambda i: _safe_details(i), need)):
            if d:
                details[jid] = d

    ordered = sorted(jobs.values(), key=lambda j: j.get("date", ""), reverse=True)
    ordered.sort(key=lambda j: -j["score"])          # best match first, newest within a score
    for j in ordered:
        if details.get(j["id"], {}).get("closed"):
            j["closed"] = True

    os.makedirs(out, exist_ok=True)
    for name in ("index.html", "icon.png"):
        src = os.path.join(HERE, name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(out, name))
    now = datetime.now(timezone.utc)
    data = {
        "updated": int(now.timestamp() * 1000),
        "time": now.strftime("%Y-%m-%d %H:%M UTC"),
        "failed": failed,
        "jobs": ordered,
        "details": details,
    }
    with open(os.path.join(out, "jobs.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    print("Done: %d jobs, %d with details, %d failed searches" % (len(ordered), len(details), len(failed)))
    for c, n in per_country.items():
        print("  %s: %d" % (c, n))
    m = {k: sum(1 for j in ordered if j["match"] == k) for k in ("strong", "good", "possible")}
    print("  match: %s" % m)


def _safe_details(jid):
    try:
        return lf.details(jid)
    except Exception as e:
        print("  details failed for %s: %s" % (jid, e))
        return None


if __name__ == "__main__":
    main()
