# CDX Archive Coverage Probe

Two command-line tools for answering a question that comes before any archive
extraction project: **how much of this site does the Wayback Machine actually
hold, and over what period is that data usable?**

Python 3.9+ standard library only. No dependencies.

The tools were built for reconstructing price history from a retailer that no
longer exists. Reports produced with them are in `reports/`.

## Why this exists

Archive extraction quotes are usually built on a raw capture count. That number
is close to meaningless:

- Raw URL counts overstate the real page count by an order of magnitude, because
  query-string parameters multiply every page into dozens of URLs.
- Captures cluster into a few years rather than spreading evenly, so a
  month-by-month series is often not achievable at all.
- The date range of captures is not the date range of usable content. A domain
  can keep being archived for a decade after it stops serving products.

These tools measure all three before extraction starts, so scope is set against
what the archive holds rather than against a guess.

## Files

| File | Purpose |
|------|---------|
| `cdx_probe_v2.py` | collects capture records from the CDX API and writes a coverage or status report |
| `analyse_cache.py` | analyses an already-collected cache offline: URL structure, parameter inflation, per-year distribution |
| `reports/` | reports produced by these tools on a real domain |

## Usage

Domain-wide sweep, HTML pages with HTTP 200 only, duplicate content collapsed:

```bash
python cdx_probe_v2.py sweep --domain example.com --max-pages 300 --out coverage_example.md
```

HTTP status timeline for a single URL, no filters:

```bash
python cdx_probe_v2.py status --url example.com
```

Offline analysis of a collected cache:

```bash
python analyse_cache.py --cache cdx_cache/example.com.jsonl
python analyse_cache.py --cache cdx_cache/example.com.jsonl --year 2015
```

`--help` lists every option for both tools.

## Design notes

**Two modes, not one.** Sweep filters on `statuscode:200` because it looks for
pages with content. That same filter hides redirects, and the 200 to 301
transition is the single most informative event in the life of a dead site. So
diagnosis takes everything and groups it; extraction filters. Different
questions, different queries.

**Truncation is a correctness problem, not a completeness problem.** CDX sorts
results by URL key, not by date. A run that hits its page cap returns a set
biased toward alphabetically early URLs, so any coverage percentage derived from
it is wrong rather than merely partial. When the cap is reached, the tool writes
a marker file and stamps the report with an INVALID banner. An earlier partial
run of the same query reported 17% monthly coverage; a fuller run reported 58%.
Neither number would have been flagged without this.

**Truncation is also detected in old caches.** Version 1 never recorded the
truncation flag on disk. Version 2 recovers it from the shape of the data:
collection proceeds in pages of 5000 records, and a complete result set ends on
a partial page, so an exact multiple of 5000 almost certainly means the run hit
the ceiling. False positives are possible at roughly 1 in 5000, and they err
toward a spurious warning rather than a silent falsehood.

**Append, don't rewrite.** The cache is JSONL, one record per line. Version 1
rewrote the whole JSON file after every page, which by page 150 meant rewriting
750,000 records to add 5000. Quadratic cost where linear was available.

**Rate limits are handled, not avoided.** Roughly one request per second, with
backoff on 429 and on 5xx, and retries on dropped connections. Long runs against
the Internet Archive drop connections as a matter of course.

## Reports

`reports/SAMPLE_coverage_report.md` is the analysis deliverable: URL structure,
a 30x parameter inflation factor, catalogue concentration in a single year, site
lifecycle from HTTP status history, observed data-quality hazards, and a section
on what the report does not establish. Every claim is marked **[Established]**
(measured directly), **[Inferred]** (reasoned from evidence, not proven), or
**[Open]** (unresolved).

The other two files are raw tool output: `status_sportsauthority.com.md` is a
valid status timeline, and `coverage_sportsauthority.md` is a truncated run
retained deliberately, because it shows what the INVALID banner looks like when
it fires.

## Note on the cache directory

`cdx_cache/` is not in this repository. A domain-wide sweep of a large retailer
produced 476 MB of capture records. The tools create the directory as needed.
