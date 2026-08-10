# Archive Coverage Report — sportsauthority.com

**Prepared:** 27 July 2026
**Purpose:** Methodology validation on a public test site. Not client work.
**Test site rationale:** A US sporting-goods retailer that ceased trading in 2016 and now exists only in web archives — structurally the same case as a defunct competitor in a price-history reconstruction.

Every claim below is marked **[Established]** (measured directly), **[Inferred]** (reasoned from evidence, not proven), or **[Open]** (unresolved).

---

## Summary

1. Raw archive volume is high but misleading. 1.27M unique URLs reduce to **41,460 distinct pages** once query strings are normalised — roughly **30x inflation**. **[Established]**
2. Coverage is **clustered, not continuous**. A single year, 2015, contains 77% of every distinct page the archive holds for this domain. **[Established]**
3. The date range of archive captures is **not** the date range of usable data. Captures continue to July 2026; product prices end around mid-2016. **[Established]**
4. Full enumeration of the domain is not practical, and does not need to be. Extraction must be driven by a product list, not by crawling the archive. **[Inferred]**

---

## 1. Archive depth

| Measure | Value |
|---|---|
| First capture | 1998-12-06 |
| Latest capture | 2026-07-17 |
| Captures of the homepage URL | 4,428 |
| Capture records retrieved in domain sweep | 1,500,000 (floor — see note) |
| Unique URLs | ~1,270,500 |
| Distinct pages after normalisation | 41,460 |

**Note on the sweep figure.** The domain-wide enumeration was capped at 300 result pages and returned exactly 1,500,000 records — an exact multiple of the page size, which indicates the cap was reached rather than the data exhausted. The true total is higher and was not measured. **[Established]**

This matters beyond bookkeeping. The CDX API sorts results by URL key, not by date. A truncated result set is therefore **biased toward alphabetically early URLs**, not merely incomplete. An earlier partial run of the same query reported 17% monthly coverage; the fuller run reported 58%; the Wayback calendar shows near-continuous coverage. Any coverage percentage derived from a partial enumeration is unusable, and I would rather report no number than a biased one. **[Established]**

---

## 2. URL structure and the deduplication problem

Product pages follow a consistent pattern:

```
/<product-name-slug>/product.jsp?productId=NNNNNNN
```

Category pages use `/category/index.jsp?categoryId=NNNNNNN` and a human-readable hierarchy such as `/Athletic-Apparel/Mens-Apparel/`.

Of roughly two dozen query parameters observed, only **`productId`** and **`categoryId`** carry meaning. The remainder are navigation and tracking artefacts:

| Parameter group | Examples | Function |
|---|---|---|
| Faceted navigation | `f`, `fbc`, `fbn`, `fbx`, `lmdn` | filter breadcrumbs (`Brand\|…`, `StorePrice\|under $500`) |
| Navigation context | `cp`, `parentPage`, `backTo`, `savePath` | "where the visitor came from" |
| Pagination / sorting | `pg`, `sr`, `view` | list controls |
| Campaign tracking | `clickid`, `camp`, `csu`, `csm`, `csc` | marketing attribution |

The most frequent parameter, `f`, appears 1.45M times and carries no information.

**Concrete illustration:** a single product (`productId=3812709`) appears under at least twelve distinct URLs in the sample, all resolving to the same page.

**Consequence:** normalising to `path + productId` discards roughly 97% of the URL set before a single snapshot is downloaded. Any effort estimate based on raw URL counts overstates the work by an order of magnitude. **[Established]**

---

## 3. Coverage is clustered, not continuous

| Year | Capture records | Share |
|---|---:|---:|
| 1998–2014 (17 years combined) | ~228,000 | 15% |
| 2015 | 1,075,583 | 72% |
| 2016 | 195,687 | 13% |

More significant than the record counts: **2015 alone accounts for 31,762 of the 41,460 distinct pages** ever archived for this domain — 77% of the catalogue, from a single year.

The parameter inflation ratio in 2015 (32.9x) is close to the overall figure (30.6x), so this concentration is **not** an artefact of parameter noise. It reflects a genuinely deeper crawl. **[Established]**

**[Inferred]** The most likely explanation is a targeted deep crawl conducted as the retailer approached insolvency. This has not been confirmed against Archive-It collection records.

**Practical consequence for price history:** a continuous month-by-month series is not achievable here. What is achievable is a set of dated observations — dense in 2015–2016, sparse before, absent after — with every gap explicitly marked. A client expecting a smooth time series should be told this before extraction begins, not at delivery.

---

## 4. Site lifecycle — when the data actually ends

The homepage has been captured 4,428 times across 28 years. Reading that as 28 years of available pricing would be wrong.

| Period | Dominant HTTP status |
|---|---|
| 1998–2001 | 200 |
| 2002–2011 | 302, intermittent 200 |
| 2012–2020 | 301 and revisit records; **zero** 200 responses |
| 2021–2026 | 301, with three isolated 200s |

The chain was trading from more than 450 stores until 2016. A method that dates closure from homepage status would place it in 2011 — five years early — and would also register the site as live in 2021, 2023 and 2025. **Homepage HTTP status reflects web-server configuration, not commercial state, and cannot be used to date a site's death.** **[Established]**

Manual inspection of the 2025-03-01 snapshot resolves the actual situation: the domain returns a 301 to `dickssportinggoods.com/?tsa=Y`. Dick's Sporting Goods acquired the Sports Authority brand and website at auction in 2016. The domain remains live and archived; it has served no product content for roughly a decade. **[Established]**

Note that redirect *targets* are not exposed by the CDX API — there is no `Location` field. Establishing where a redirect points requires opening the snapshot. **[Established]**

---

## 5. Data quality hazards observed

- **Archived error pages.** The redirect target snapshot captured a "Something Went Wrong" error page. A parser would read this as "no price found". It is not — it is a failed capture, and must be recorded distinctly from a genuine absence.
- **HTML entity corruption.** 10,826 URLs contain parameters named `amp;backTo`, `amp;infoPath` and similar — `&amp;` entering the URL literally. Without normalisation these duplicate existing pages.
- **Fabricated URLs.** At least one captured URL contains `%E2%80%A6` (a horizontal ellipsis), produced by a visually truncated link. Such URLs never existed on the live site.

---

## 6. What extraction would look like

Given the above, a workable approach:

1. Client supplies the target SKU list. Extraction is driven by that list, never by enumerating the archive.
2. Per product, a CDX query filtered on the product identifier, with `collapse=timestamp:6` to return at most one capture per month.
3. Fetch only the resulting snapshots, rate-limited to roughly one request per second.
4. Extract price from **JSON-LD / microdata first**, DOM parsing as fallback. Structured markup survives layout changes and is the more reliable source in older snapshots.
5. Validate that each fetched page is a product page before recording an absence, so that error pages and redirects are never silently reported as "no price".
6. Output: one row per observation, with both the original URL and the archive snapshot link, so any figure can be independently verified.

Order-of-magnitude: 50 products across 4 sites, deduplicated to monthly observations, is a few thousand fetches per site — hours, not weeks. Enumerating the archive first would be hundreds of hours and would produce nothing extra.

---

## 7. What this report does not establish

- The true total capture count for this domain. The sweep was capped; the figure quoted is a floor.
- Whether the 2015 concentration was a targeted crawl or an incidental one. **[Open]**
- Whether product pages themselves follow the same status pattern as the homepage. The closure date given here is bounded by external evidence, not by product-page measurement. **[Open]**
- Prices. No snapshot content was downloaded or parsed for this report. Coverage assessment and extraction are separate steps, deliberately.

---

## Verification

Any claim above can be checked directly:

- Homepage capture history: `https://web.archive.org/web/*/sportsauthority.com`
- Redirect behaviour, 2025-03-01: `https://web.archive.org/web/20250301171359/http://www.sportsauthority.com/`
- Early capture, 1998-12-06: `https://web.archive.org/web/19981206065329/http://sportsauthority.com/`

Tooling used: a custom CDX collector (resume-key pagination, rate-limit and connection-drop handling, incremental caching) and a local cache analyser. Python standard library only.
