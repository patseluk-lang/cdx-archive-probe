"""
cdx_probe.py (v2) - archive coverage reconnaissance via the Wayback Machine CDX API.

TWO MODES
---------
1. sweep - walk the whole domain. Answers the question
   "how deeply and how densely does the archive cover this site at all".

       python cdx_probe.py sweep --domain sportsauthority.com

2. status - HTTP status timeline for a single URL. Answers the question
   "when did the site stop serving content and start serving a redirect".
   That date is the commercial death of the shop.

       python cdx_probe.py status --url sportsauthority.com

WHY TWO MODES AND NOT ONE
-------------------------
In sweep mode we filter on `statuscode:200`, because we are looking for pages
with content. But that very filter hides redirects - and the 200 -> 301
transition is the single most important event in the life of a dead shop.
So diagnosis (status) takes EVERYTHING and groups it, while extraction (sweep)
filters. Different jobs, different queries.

Dependencies: the Python 3.9+ standard library only.

RATE LIMITS
-----------
The Internet Archive rate-limits the CDX API. A safe pace is about one request
per second. On 429 the script waits and retries. Dropped connections
(WinError 10054) are normal on long runs and are retried as well.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"

REQUEST_DELAY_SEC = 1.1
PAGE_SIZE = 5000
MAX_RETRIES = 5
USER_AGENT = "cdx-coverage-probe/2.0 (freelance research; contact: your-email@example.com)"

CACHE_DIR = Path("cdx_cache")

# Fields requested from CDX. Order matters - the response arrives as arrays.
FIELDS = "timestamp,original,statuscode,mimetype,digest"


# --------------------------- network ---------------------------


def _http_get(url: str) -> str:
    """GET with retries. Returns the response body as text."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read().decode("utf-8", errors="replace")

        except urllib.error.HTTPError as error:
            if error.code == 429:
                retry_after = error.headers.get("Retry-After")
                wait = int(retry_after) if retry_after and retry_after.isdigit() else 10 * attempt
                print(f"  [429] rate limited, waiting {wait}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            if 500 <= error.code < 600:
                wait = 5 * attempt
                print(f"  [{error.code}] server error, waiting {wait}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            raise

        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            wait = 5 * attempt
            print(f"  [network] {error}, waiting {wait}s (attempt {attempt}/{MAX_RETRIES})")
            time.sleep(wait)

    raise RuntimeError(f"Could not fetch {url} after {MAX_RETRIES} attempts")


def _build_query(
    target: str,
    match_type: str,
    filters: list[str],
    collapse: str | None,
    resume_key: str | None,
    date_from: str | None,
    date_to: str | None,
) -> str:
    params = [
        ("url", target),
        ("matchType", match_type),
        ("output", "json"),
        ("fl", FIELDS),
        ("limit", str(PAGE_SIZE)),
        ("showResumeKey", "true"),
    ]
    for f in filters:
        params.append(("filter", f))
    if collapse:
        params.append(("collapse", collapse))
    if date_from:
        params.append(("from", date_from))
    if date_to:
        params.append(("to", date_to))
    if resume_key:
        params.append(("resumeKey", resume_key))

    return f"{CDX_ENDPOINT}?{urllib.parse.urlencode(params)}"


# --------------------------- cache ---------------------------
#
# JSONL format: one record per line. Appending to the end of the file costs
# exactly as much as the new data. In v1 I rewrote the entire JSON file after
# every page - by page 150 that meant rewriting 750,000 records from scratch.
# Classic quadratic cost where linear was available.


def _cache_paths(cache_key: str) -> tuple[Path, Path]:
    CACHE_DIR.mkdir(exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in cache_key)
    return CACHE_DIR / f"{safe}.jsonl", CACHE_DIR / f"{safe}.TRUNCATED"


def _read_cache(data_file: Path) -> list[dict]:
    records = []
    with data_file.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _read_legacy_cache(cache_key: str) -> tuple[list[dict], bool] | None:
    """
    Reads the old v1 cache format (.json) so collected data is not lost.

    The problem: v1 never wrote the truncation flag into the cache - it lived
    only in memory during the run. A day later the provenance of the data is
    no longer recoverable.

    The rescue is the shape of the data itself. Collection proceeds in pages of
    PAGE_SIZE records. A complete result set ends on a partial page, so an
    exact multiple of PAGE_SIZE almost certainly means the run hit the ceiling.
    A false positive is possible (roughly 1 in 5000), but it errs on the safe
    side: a spurious warning beats a silent falsehood.
    """
    legacy = CACHE_DIR / f"{cache_key}.json"
    if not legacy.exists():
        return None

    print(f"Found a v1-format cache: {legacy}")
    records = json.loads(legacy.read_text(encoding="utf-8"))

    suspected = len(records) > 0 and len(records) % PAGE_SIZE == 0
    if suspected:
        print(f"  !! {len(records):,} = an exact multiple of {PAGE_SIZE}. "
              f"Almost certainly a truncated run - marking the report invalid.")
    return records, suspected


# --------------------------- collection ---------------------------


def fetch_captures(
    target: str,
    match_type: str,
    filters: list[str],
    collapse: str | None,
    cache_key: str,
    use_cache: bool,
    max_pages: int,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[list[dict], bool]:
    """
    Pulls captures page by page. Returns (records, was_truncated).

    WARNING: CDX sorts by URL key, not by date. A truncated result set is
    therefore BIASED, not merely incomplete, and is unusable for a coverage
    report. That is why truncation is recorded in a separate marker file and
    printed into the report itself, not only to the console.
    """
    data_file, marker_file = _cache_paths(cache_key)

    if use_cache:
        if data_file.exists():
            print(f"Reading from cache: {data_file}")
            return _read_cache(data_file), marker_file.exists()
        legacy = _read_legacy_cache(cache_key)
        if legacy is not None:
            return legacy

    data_file.unlink(missing_ok=True)
    marker_file.unlink(missing_ok=True)

    captures: list[dict] = []
    resume_key: str | None = None
    page = 0
    truncated = False

    with data_file.open("a", encoding="utf-8") as sink:
        while page < max_pages:
            page += 1
            query_url = _build_query(
                target, match_type, filters, collapse, resume_key, date_from, date_to
            )
            print(f"Page {page}... ({len(captures)} collected so far)")

            body = _http_get(query_url)
            time.sleep(REQUEST_DELAY_SEC)

            if not body.strip():
                break

            rows = json.loads(body)
            if not rows:
                break

            header = rows[0]
            payload = rows[1:]

            # The resume key arrives in the final rows of the array:
            # first an empty row, then a row holding a single element.
            resume_key = None
            while payload and len(payload[-1]) <= 1:
                last = payload.pop()
                if len(last) == 1 and last[0]:
                    resume_key = last[0]

            for row in payload:
                if len(row) == len(header):
                    record = dict(zip(header, row))
                    captures.append(record)
                    sink.write(json.dumps(record, ensure_ascii=False) + "\n")

            sink.flush()  # so that Ctrl+C does not eat the last page

            if not resume_key:
                break
        else:
            truncated = True
            marker_file.write_text(f"max_pages={max_pages}", encoding="utf-8")
            print(f"\n  !! Hit the --max-pages limit ({max_pages}). "
                  f"Data is TRUNCATED and BIASED - the coverage figures are invalid.")

    print(f"Cache: {data_file}")
    return captures, truncated


# --------------------------- analysis: coverage ---------------------------


def _months_between(first: str, last: str) -> list[str]:
    year, month = int(first[:4]), int(first[4:6])
    end_year, end_month = int(last[:4]), int(last[4:6])
    result = []
    while (year, month) <= (end_year, end_month):
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return result


def analyse_coverage(captures: list[dict], url_contains: str | None) -> dict:
    if url_contains:
        captures = [c for c in captures if url_contains in c["original"]]
    if not captures:
        return {"empty": True}

    timestamps = sorted(c["timestamp"] for c in captures)
    first, last = timestamps[0], timestamps[-1]
    by_month = Counter(f"{ts[:4]}-{ts[4:6]}" for ts in timestamps)
    all_months = _months_between(first, last)
    empty_months = [m for m in all_months if by_month[m] == 0]

    return {
        "empty": False,
        "total": len(captures),
        "unique_urls": len({c["original"] for c in captures}),
        "first": first,
        "last": last,
        "by_year": dict(sorted(Counter(ts[:4] for ts in timestamps).items())),
        "months_total": len(all_months),
        "months_empty": len(empty_months),
        "empty_months_sample": empty_months[:24],
        "sample_captures": captures[:5],
    }


# --------------------------- analysis: statuses ---------------------------


def analyse_status_timeline(captures: list[dict]) -> dict:
    """
    Groups captures by year and HTTP status.

    Status '-' means a revisit record (the archive confirmed the content had
    not changed since the previous capture). That is not an error - it is a
    full capture.
    """
    by_year_status: dict[str, Counter] = defaultdict(Counter)
    for capture in captures:
        year = capture["timestamp"][:4]
        status = capture.get("statuscode") or "-"
        by_year_status[year][status] += 1

    ok = sorted(c["timestamp"] for c in captures if c.get("statuscode") == "200")
    redirects = sorted(
        c["timestamp"] for c in captures
        if (c.get("statuscode") or "").startswith("3")
    )

    # The redirect target cannot be found from CDX - there is no Location there.
    # It has to be checked by eye in the snapshot itself. We say so honestly.
    return {
        "by_year_status": {y: dict(sorted(c.items())) for y, c in sorted(by_year_status.items())},
        "all_statuses": sorted({s for c in by_year_status.values() for s in c}),
        "first_200": ok[0] if ok else None,
        "last_200": ok[-1] if ok else None,
        "first_redirect": redirects[0] if redirects else None,
        "total": len(captures),
    }


# --------------------------- reports ---------------------------


def _truncation_banner() -> list[str]:
    return [
        "> **INVALID - DO NOT USE THESE FIGURES.**",
        ">",
        "> The CDX query hit the page limit before returning all records.",
        "> CDX sorts results by URL key, not by date, so a truncated result set",
        "> is systematically biased toward alphabetically-early URLs. The figures",
        "> below are not representative of actual archive coverage.",
        "",
    ]


def render_coverage_report(domain, url_contains, stats, truncated) -> str:
    lines = [f"# Archive coverage - {domain}", ""]
    if truncated:
        lines += _truncation_banner()

    lines += [
        "Source: Internet Archive Wayback Machine, CDX API",
        "Query: whole domain and subdomains (`matchType=domain`)",
        "Filters: HTTP 200 only, `text/html` only, duplicate content collapsed"
        + (f", URL contains `{url_contains}`" if url_contains else ""),
        "",
        "**Note:** redirects (3xx) are excluded by these filters. See the status",
        "timeline report for when the site stopped serving content.",
        "",
    ]

    if stats["empty"]:
        lines.append("**No captures matched these filters.**")
        return "\n".join(lines)

    first, last = stats["first"], stats["last"]
    pct = 100 * (stats["months_total"] - stats["months_empty"]) / stats["months_total"]

    lines += [
        "## Summary", "",
        f"- First capture: **{first[:4]}-{first[4:6]}-{first[6:8]}**",
        f"- Last capture: **{last[:4]}-{last[4:6]}-{last[6:8]}**",
        f"- Captures: **{stats['total']:,}**",
        f"- Unique URLs: **{stats['unique_urls']:,}**",
        f"- Months in range: **{stats['months_total']}**, empty: "
        f"**{stats['months_empty']}** -> monthly coverage **{pct:.0f}%**",
        "", "## Captures per year", "", "| Year | Captures |", "| --- | ---: |",
    ]
    for year, count in stats["by_year"].items():
        lines.append(f"| {year} | {count:,} |")

    lines += ["", "## Months with zero captures (first 24)", ""]
    lines.append(", ".join(stats["empty_months_sample"]) or "None.")

    lines += ["", "## Sample snapshots (verifiable)", ""]
    for capture in stats["sample_captures"]:
        lines.append(f"- https://web.archive.org/web/{capture['timestamp']}/{capture['original']}")

    return "\n".join(lines)


def render_status_report(url, stats, truncated) -> str:
    lines = [f"# Status timeline - {url}", ""]
    if truncated:
        lines += _truncation_banner()

    lines += [
        "Source: Internet Archive Wayback Machine, CDX API",
        "Query: single URL (`matchType=exact`), **no status filter**",
        "",
        "Purpose: identify when this URL stopped serving content and started",
        "serving redirects or errors. The date range of archive captures is not",
        "the same as the date range of usable data.",
        "",
        f"- Total captures: **{stats['total']:,}**",
    ]

    def fmt(ts):
        return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if ts else "n/f"

    lines += [
        f"- First HTTP 200: **{fmt(stats['first_200'])}**",
        f"- Last HTTP 200: **{fmt(stats['last_200'])}**",
        f"- First redirect (3xx): **{fmt(stats['first_redirect'])}**",
        "",
        "## Captures per year by HTTP status",
        "",
    ]

    statuses = stats["all_statuses"]
    lines.append("| Year | " + " | ".join(statuses) + " |")
    lines.append("| --- | " + " | ".join("---:" for _ in statuses) + " |")
    for year, counts in stats["by_year_status"].items():
        cells = [str(counts.get(s, 0)) for s in statuses]
        lines.append(f"| {year} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "`-` = revisit record (archive confirmed content unchanged since the",
        "previous capture). These are valid captures, not errors.",
        "",
        "**Redirect targets are not visible in CDX data** - the API returns no",
        "`Location` header. Confirming where a redirect points requires opening",
        "the snapshot itself.",
    ]
    return "\n".join(lines)


# --------------------------- CLI ---------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Archive coverage reconnaissance (Wayback CDX API)"
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", default=None, help="report file (.md)")
    common.add_argument("--no-cache", action="store_true", help="ignore the cache")
    common.add_argument("--max-pages", type=int, default=10, help="safety limit (default 10)")
    common.add_argument("--from", dest="date_from", default=None, help="YYYY or YYYYMMDD")
    common.add_argument("--to", dest="date_to", default=None, help="YYYY or YYYYMMDD")

    p_sweep = sub.add_parser("sweep", parents=[common], help="walk the whole domain")
    p_sweep.add_argument("--domain", required=True)
    p_sweep.add_argument("--url-contains", default=None, help="e.g. /product/")

    p_status = sub.add_parser("status", parents=[common], help="status timeline for one URL")
    p_status.add_argument("--url", required=True, help="e.g. sportsauthority.com")

    args = parser.parse_args()

    if args.mode == "sweep":
        captures, truncated = fetch_captures(
            target=args.domain,
            match_type="domain",
            filters=["statuscode:200", "mimetype:text/html"],
            collapse="digest",
            cache_key=args.domain,
            use_cache=not args.no_cache,
            max_pages=args.max_pages,
            date_from=args.date_from,
            date_to=args.date_to,
        )
        print(f"Captures: {len(captures):,}")
        report = render_coverage_report(
            args.domain, args.url_contains,
            analyse_coverage(captures, args.url_contains), truncated,
        )
        default_name = f"coverage_{args.domain}.md"

    else:
        # No filters and no collapse: diagnosis needs EVERYTHING.
        captures, truncated = fetch_captures(
            target=args.url,
            match_type="exact",
            filters=[],
            collapse=None,
            cache_key=f"status_{args.url}",
            use_cache=not args.no_cache,
            max_pages=args.max_pages,
            date_from=args.date_from,
            date_to=args.date_to,
        )
        print(f"Captures: {len(captures):,}")
        report = render_status_report(args.url, analyse_status_timeline(captures), truncated)
        default_name = f"status_{args.url}.md"

    out_path = Path(args.out or default_name.replace("/", "_"))
    out_path.write_text(report, encoding="utf-8")
    print(f"\nReport: {out_path.resolve()}\n")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
