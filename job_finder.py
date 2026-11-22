"""Reads LinkedIn's public (signed-out) job pages.

search(keywords, geo_id, ...)  -> list of job cards (10 per page)
details(job_id)                -> dict with criteria, description, apply link, ...

Run  python job_finder.py --test  to check that LinkedIn still answers.
Standard library only.
"""
import html
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

MIN_GAP = 0.7          # seconds between any two requests (can be changed by the caller)
_lock = threading.Lock()
_last = [0.0]


class NoMorePages(Exception):
    pass


def _get(url, tries=3):
    """GET with a polite gap between requests and retries on HTTP 429."""
    for attempt in range(tries + 1):
        with _lock:
            wait = _last[0] + MIN_GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                raise NoMorePages(e.code)
            if e.code == 429 and attempt < tries:
                time.sleep(5 * (attempt + 1) ** 2)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt < tries:
                time.sleep(3 * (attempt + 1))
                continue
            raise


# ---------- small HTML helpers ----------

_TAG = re.compile(r"<[^>]+>")


def _text(s):
    if s is None:
        return ""
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _class_inner(block, cls, tag=r"[a-z0-9]+"):
    """Inner HTML of the first element whose class attribute contains cls."""
    m = re.search(r'<(%s)\b[^>]*class="[^"]*\b%s\b[^"]*"[^>]*>(.*?)</\1>' % (tag, re.escape(cls)),
                  block, re.S)
    return m.group(2) if m else None


def _attr(tag_html, name):
    m = re.search(r'\b%s="([^"]*)"' % re.escape(name), tag_html)
    return html.unescape(m.group(1)) if m else ""


def html_to_text(s):
    """Job description HTML -> plain text with line breaks and • bullets."""
    if not s:
        return ""
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "\n• ", s)
    s = re.sub(r"(?i)</(p|div|ul|ol|h[1-6])>", "\n", s)
    s = re.sub(r"(?i)<(p|div|ul|ol|h[1-6])[^>]*>", "\n", s)
    s = _TAG.sub("", s)
    s = html.unescape(s).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n")]
    out, blank = [], False
    for ln in lines:
        if ln in ("", "•"):
            if out and not blank:
                out.append("")
            blank = True
        else:
            out.append(ln)
            blank = False
    return "\n".join(out).strip()


# ---------- search ----------

def parse_search(page):
    jobs = []
    for li in re.findall(r"<li\b[^>]*>(.*?)</li>", page, re.S):
        m = re.search(r"urn:li:jobPosting:(\d+)", li)
        if not m:
            m = re.search(r"/jobs/view/[^\"?]*?-?(\d{6,})", li)
        if not m:
            continue
        jid = m.group(1)
        comp_link = ""
        sub = _class_inner(li, "base-search-card__subtitle")
        if sub:
            a = re.search(r'<a\b[^>]*href="([^"]+)"', sub)
            if a:
                comp_link = html.unescape(a.group(1)).split("?")[0]
        tm = re.search(r'<time\b[^>]*class="[^"]*job-search-card__listdate[^"]*"[^>]*>', li)
        img = re.search(r"<img\b[^>]*data-delayed-url=\"[^\"]+\"[^>]*>", li)
        jobs.append({
            "id": jid,
            "title": _text(_class_inner(li, "base-search-card__title")),
            "company": _text(sub),
            "companyUrl": comp_link,
            "location": _text(_class_inner(li, "job-search-card__location")),
            "date": _attr(tm.group(0), "datetime") if tm else "",
            "salary": _text(_class_inner(li, "job-search-card__salary-info")),
            "benefit": _text(_class_inner(li, "job-posting-benefits__text")),
            "logo": _attr(img.group(0), "data-delayed-url") if img else "",
            "url": "https://www.linkedin.com/jobs/view/%s/" % jid,
        })
    return jobs


def search(keywords, geo_id, posted="r2592000", levels="", start=0):
    q = {"keywords": keywords, "geoId": geo_id, "f_TPR": posted,
         "sortBy": "DD", "start": start}
    if levels:
        q["f_E"] = levels
    url = SEARCH_URL + "?" + urllib.parse.urlencode(q)
    try:
        return parse_search(_get(url))
    except NoMorePages:
        return []


# ---------- details ----------

def parse_details(page):
    d = {"criteria": {}}
    for item in re.findall(r'<li\b[^>]*class="[^"]*description__job-criteria-item[^"]*"[^>]*>(.*?)</li>',
                           page, re.S):
        k = _text(_class_inner(item, "description__job-criteria-subheader"))
        v = _text(_class_inner(item, "description__job-criteria-text"))
        if k and v:
            d["criteria"][k] = v
    d["applicants"] = _text(_class_inner(page, "num-applicants__caption"))
    d["posted"] = _text(_class_inner(page, "posted-time-ago__text"))
    m = re.search(r'<div\b[^>]*class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>\s*'
                  r'(?:<button|</section>|<div)', page, re.S)
    desc = m.group(1) if m else _class_inner(page, "show-more-less-html__markup", "div")
    d["description"] = html_to_text(desc or "")
    a = re.search(r'<code\b[^>]*id="applyUrl"[^>]*>\s*<!--\s*"(.*?)"\s*-->', page, re.S)
    apply = ""
    if a:
        apply = html.unescape(a.group(1))
        p = urllib.parse.urlparse(apply)
        qs = urllib.parse.parse_qs(p.query)
        if "url" in qs and "linkedin.com" in p.netloc:
            apply = qs["url"][0]
    d["applyUrl"] = apply if apply.startswith("https://") else ""
    d["closed"] = "No longer accepting applications" in page
    return d


def details(job_id):
    try:
        return parse_details(_get(DETAIL_URL.format(job_id)))
    except NoMorePages:
        return None


# ---------- self-test ----------

def _test():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    kw, geo = '"Planning Engineer" OR "Project Controls"', "104305776"  # UAE
    p1 = search(kw, geo, start=0)
    print("Page 1: %d jobs" % len(p1))
    if not p1:
        print("FAIL: no jobs returned (LinkedIn may be blocking this network).")
        sys.exit(1)
    j = p1[0]
    for k, v in j.items():
        print("  %-10s %s" % (k, v))
    d = details(j["id"]) or {}
    print("Details: criteria=%s" % d.get("criteria"))
    print("  applicants=%r posted=%r closed=%r apply=%r" % (
        d.get("applicants"), d.get("posted"), d.get("closed"), d.get("applyUrl")))
    print("  description: %s..." % (d.get("description") or "")[:300].replace("\n", " | "))
    p2 = search(kw, geo, start=10)
    overlap = {x["id"] for x in p1} & {x["id"] for x in p2}
    print("Page 2: %d jobs, %d repeated from page 1" % (len(p2), len(overlap)))
    print("OK" if p2 and not overlap else "WARNING: paging looks wrong")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _test()
    else:
        print(__doc__)
