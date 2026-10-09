# Kuvalda coverage extension / operator policy correction / 2026-10-09

OPERATOR-CONFIRMED latest instruction supersedes previous robots-hard-block policy
only for this competitor-intelligence workflow. robots.txt remains recorded
informational evidence. Ordinary public HTTP GET and normal Edge rendering are
allowed for /promo/, /promo/item-* and actually tested /news/ without login,
CAPTCHA bypass, private/admin API, proxy rotation, identity rotation or evasion.
Other STERBRUST workflows and production authority are unchanged.

Existing feature/base: feature/competitive-intelligence-kuvalda-metalmaster /
961e7459d6840d7db1cbc7d97ca560dbafe76a40. No develop merge.

## Status model

`competitor_source_surfaces` is isolated within the competitor DB. Its rows in
this extension belong only to kuvalda_nnov. `robots_disallowed` is independent of
`fetch_status`: PUBLIC_FETCH_OK, BROWSER_FETCH_OK, HTTP_BLOCKED, AUTH_REQUIRED,
CAPTCHA_BLOCKED or ERROR. Surface coverage status is LIVE_CRAWLED, INDEX_ONLY,
DISCOVERED_NOT_FETCHED, ERROR or historical ROBOTS_BLOCKED. No page is declared
blocked solely from a Disallow under the corrected policy.

INDEX_ONLY requires EXTERNAL_INDEX_EVIDENCE. No indexed snippets or prices were
used here. Actual crawl evidence remains raw HTML/browser DOM/tab manifests.
Robots matching preserves wildcard/end-anchor/longest-match evidence; references
[RFC9309](https://www.rfc-editor.org/rfc/rfc9309.html). Search-engine groups are not
selected to change identity or obtain access. The separate operator policy decides
whether ordinary public navigation is authorized for this workflow.

The Kuvalda transport is exact-host nnov HTTPS only and refuses auth/admin/API/
account/cart/order/payment endpoints. Normal fresh public browser context, fixed
UA, single worker and new random 3–7s pacing. HTTP403/challenge still stops access;
no rendering fallback to defeat a protection response. Browser accessibility was
actually tested for /news/ and /promo/; HTTP details were normal public responses.

## Operations

```powershell
python -m scripts.run_kuvalda_public_extension --max-pages 35
python -m scripts.verify_kuvalda_extension
python -m competitor_intel --dev-config competitor-data/dev-config.json crawl --source kuvalda_nnov --since 2026-09-01 --through 2026-10-09
```

The normal Kuvalda CLI now routes to corrected public policy. `--all` remains an
explicit request for both competitors; it was not run in this Kuvalda-only task.
MetalMaster collection/parsing behavior is unchanged. No global schema migration
or analyzer run was used in this extension; one additive surfaces table only.

Scope: homepage and all six winter tabs, /sale/, machine/wood/metal/welding/
compressor/autoservice categories and one observed general-industrial motor
category. First category pages only, two product and two category navigation
samples. No unrelated consumer/full catalog/archive crawl. Current14 promotion
details include campaign existence/date/text/items/price roles; September overlap
is retained even if publication is unknown. /news/ listing has no September+
article in its current visible dated entries; accessibility is BROWSER_FETCH_OK,
not ROBOTS_BLOCKED or an inferred missing dataset.

Primary public sitemap /sitemap.xml returned404; no sitemap entries fabricated.
An optional missing sitemap is DONE_WITH_WARNINGS for the source run, independent
of successfully verified public promo/news/sale coverage. Other discovered
navigation is DISCOVERED_NOT_FETCHED, not silently absent or failed.

New Kuvalda per-page extractor version kuvalda-allowed-1.1. Existing generic
extractor1.3 stays unchanged for MetalMaster. Campaign end dates preserve actual
listing evidence alongside abbreviated detail headers; publication is NULL when
not proved. A single explicit year at the end of a same-year textual range applies
to the two dates, never the current observation year. Auth markers without numeric
amount stay AUTH_REQUIRED with NULL amounts; no invented price or old/new delta.
The two1.0 unknown-role rows were corrected from exact captures, no source recrawl.

## Checkpoint

LIVE-VERIFIED isolated competitor/public source:29pages,14promotions,743current
campaign-context cards;200sale cards,135HIGH,221old+new. Price roles:271AUTH_REQUIRED,
197SALE,265PUBLIC,10UNKNOWN. Duplicate pages/versions/item keys0; all743card DOM
checks and29page title/date checks passed. Indexed evidence rows0;AI network0.
16robots-disallowed pages fetched normally; actual access-control blocked URLs0.
Source /promo/ and /news/ checked by ordinary browser, other details/public GET.

MetalMaster before/after exact hashes: source1/pages5/versions16/items284/
observations31/analysis16, all unchanged. None of its stored facts, timestamps,
versions, QA status or analysis rows was updated. No KAMI/Vekprom/US business
table/Sterbrust/ESOL/production/develop/scheduler mutation or connection.

Report/evidence: reports/KUVALDA_ALLOWED_SOURCE_EXTENSION_2026-10-09/
KUVALDA_ALLOWED_SOURCE_EXTENSION_REPORT.md, SOURCE_STATUS.csv, CAMPAIGNS.csv,
CAMPAIGN_ITEMS.csv, QA_RESULT.json and METALMASTER_IMMUTABILITY.json.
Final status COMPETITOR_INTELLIGENCE_KUVALDA_ALLOWED_SCOPE_VERIFIED.
This is bounded source coverage, not full catalogue/historical-price reconstruction.
