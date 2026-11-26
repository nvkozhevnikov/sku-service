# Competitor intelligence: four-source onboarding / 2026-10-12

Final status: COMPETITOR_INTELLIGENCE_FOUR_SOURCE_ACCESS_INCOMPLETE
WAITING_FOR_OPERATOR_REPORT_REVIEW — STOP. Requested all-four VERIFIED status is not reached because YouMet public access is not reliably available.

OPERATOR-CONFIRMED: primary domains jet-online.ru, www.stanki.ru, youmet.ru, titaniumgroup.ru; September 1 to October 12 2026; bounded public surfaces, no full catalogue. Robots informational independently of fetch status. AI and scheduler OFF; no develop merge or production work.

DONE: separate historical lookup, source registration, bounded source audits/captures, observed-DOM adapters, conservative access transport, generic six-source HTML and separate BLOG export. APPLIED: competitor DB55454 only, four source registry rows, 5264 historical cache rows and three accessible LIVE baselines. VERIFIED: integrity/isolation/price roles/baseline and offline UI. NOT VERIFIED: complete YouMet LIVE extraction.

## Current accepted LIVE data

Counts refer to current accepted versions; items are campaign/page contexts, not distinct catalogue products. Earlier extractor attempts remain rejected history.

| Source | Pages | Item contexts | Old+new rows | HIGH | Price types |
|---|---:|---:|---:|---:|---|
| jet_online | 30 | 1553 | 1547 | 259 | {"SALE": 1547, "UNKNOWN": 6} |
| stanki_ru | 26 | 558 | 248 | 155 | {"SALE": 248, "FROM_PRICE": 1, "UNKNOWN": 253, "PUBLIC": 56} |
| titaniumgroup | 8 | 68 | 67 | 67 | {"SALE": 29, "PUBLIC": 1, "UNKNOWN": 38} |
| youmet | 0 | 0 | 0 | 0 | {} |

JET: all 26 current special_offers pages captured; 1543 sale products, plus 10 homepage contexts. Six call-for-price rows are UNKNOWN/NULL. Visible price/old-price DOM is authoritative; analytics attributes supply optional metadata only. Broken analytics JSON stays unknown. Leasing/credit/monthly amounts cannot become product prices. One current NEWS, no archive crawl.

KAMI: homepage, sale index, five machine/industrial sale categories, actual /spec/ navigation and 12 current special campaign pages, one homepage machine campaign and five current news details. 14 campaign identities including an explicit timed campaign published as news. One additionally discovered glue campaign /spec/skidka-do-30-kley/ returns404 and remains ERROR, not no-data; no bypass/retry expansion. Original capture time is separate from LIVE observation time. Machine priority uses LIVE names/categories; historical metadata stays separately labelled.

Titanium: homepage, robots, announced sitemap, actions, news and articles audit; eight included pages, four inspected news details excluded by published date. Actual /actions/ DOM contains all 13 products despite its displayed “12 of 13” counter, plus 17 homepage promotional contexts and 38 published campaign-table rows. Two BLOG articles are separate from NEWS. Published table prices are UNKNOWN with PUBLICATION_PRICE_CONTEXT; current sale listing takes precedence. Ordinary catalogue cards on BLOG are excluded.

YouMet: HTTP robots and homepage returned403. One ordinary Edge visit displayed public store DOM with h1 and real navigation while its initial document status stayed403; a further ordinary visit did not expose a usable public DOM. No challenge solving, login, private API, regional mirror or evasion attempted. /akcia/, /about/news/, /blog/ are DISCOVERED_NOT_FETCHED from that saved DOM, not fabricated paths. No live prices/campaigns imported and no “no data” claim used to disguise access failure.

## Historical cache and efficiency

REPOSITORY-VERIFIED prepared bundle:5264 rows/unique canonical URLs; physical UTF8/CRLF SHA9418d99c2109b60519d46ef38579a3039652d63b9bc2081ba88ba7c62242c310; LF logical SHAba60d3052d2c6a3265719d49b2d9be7bc0ff6b6adb6633dfe87916cebfb56f07. Neither prepared bytes nor original corpus rebuilt. Future export newline hashing corrected. Original Universal Supplier corpus/DB/worktree/state not accessed during continuation, writes0.

Storage: competitor_historical_seeds, provenance REUSED_KAMI_CAPTURE / HISTORICAL_SEED, is_live=false, original timestamps, NULL price/old_price/availability. Lookup is outside current pages/items. 5264 inserted, idempotent repeat inserted0. Current promotions, prices, event counts and new-products metric proved unchanged immediately after import. Exact URL context attached separately without overwriting LIVE fields.

| Efficiency measure | Count |
|---|---:|
| historical_seed_rows | 5264 |
| live_listing_GETs | 57 |
| live_content_GETs | 14 |
| targeted_product_detail_GETs | 3 |
| targeted_product_detail_pages | 2 |
| product_detail_GETs_avoided | 321 |
| refresh_candidates | 436 |
| refresh_deferred | 434 |

GET accounting includes redirects/retries. Listing GETs cover homepage/index/current sale/campaign surfaces; content GETs cover bounded NEWS/BLOG details. Robots/sitemap and two ordinary YouMet browser visits are separate. The one standalone stanki /spec/ GET is explicitly included. 321 avoided metadata GETs refer only to exact historical matches not fetched for QA, not to all5264 seeds. All436 candidates derive from CURRENT_CAMPAIGN_MEMBER, no age-only requests. Two campaign targets selected for QA;434 deferred, including candidates whose current commercial facts are already available in listings. No catalogue expansion follows a detail response.

Targeted detail QA found a campaign-specific listing price versus standard detail price for IRONMAC ITX-510 HCNC; this is not a daily decrease. Another execution URL redirected to its series page. Both raw responses and redirect are retained as QA evidence; no execution-to-series identity merge or price overwrite was performed. Detail prices are not projected into current metrics.

## Verification and limits

REPOSITORY-VERIFIED:150 tests PASS, one old live SQL fixture deliberately deselected to avoid modifying frozen source data. Native Edge file:// QA:25 PASS; all remote requests aborted; desktop/mobile layout and six source cards, separate BLOG, historical5264 label and YouMet gap visible. Saved-capture replay/re-extraction is not a fresh LIVE idempotence crawl.

Duplicates pages0 / versions0 / per-version items0. Price parsing corruption0. Explicit request-price/phone prices NULL, personal/auth/monthly semantics not counted as public sale. Exact original Kuvalda/MetalMaster source group hashes unchanged; every original table row prefix unchanged, including runs. Frozen sources were not recrawled.

Generic report:6 sources, 98 current pages, 2993 item contexts, 31 campaigns (including two explicit commercial publications still retained as NEWS), 9 NEWS and 2 BLOG. Event counts:{"BASELINE_OBSERVATION": 98}. No false NEW_PRODUCT/NEW_PROMOTION/PRICE_INCREASE/PRICE_DECREASE. Ending-soon campaign cards are date-derived source facts; they are not comparable daily changes. Second report build:new event IDs0, duplicates0, identical facts hash.

Unknown dates/models/availability stay unknown. Sale pagination of KAMI is bounded first surfaces rather than full catalogue; item/source completeness flags remainfalse. No disappearance/change event can be inferred from that coverage. Titanium unpublished campaign end dates remain unknown. YouMet LIVE extraction and the broken KAMI glue campaign URL are declared access gaps.

AI calls0 / scheduler activation0 / supplier KAMI writes0 / Vekprom writes0 / Sterbrust+ESOL+production writes0 / develop merges0. Only feature/competitive-intelligence-kuvalda-metalmaster may be committed/pushed.

PACKAGED-EVIDENCE:reports/COMPETITOR_FOUR_SOURCE_ONBOARDING_2026-10-12 contains AUDIT/LIVE/EXTRA captures, request accounting, seed import proof, exact historical context, refresh plan, targeted detail QA, final isolation proof and tests. reports/COMPETITOR_BUSINESS_REPORT_PREVIEW_2026-10-12 contains the single generic index.html, CSVs (BLOG separate), build/rebuild proofs, offline UI receipts and screenshots.

## Allowed/public URLs actually checked

Access listed below is independent of robots policy. Raw references and observed_at are retained in audit manifests and database source surfaces.

| Source | URL | HTTP/access |
|---|---|---|
| jet_online | https://jet-online.ru/ | 200 |
| jet_online | https://jet-online.ru/articles/ | 200 |
| jet_online | https://jet-online.ru/news/ | 200 |
| jet_online | https://jet-online.ru/news/postuplenije-strubcin-i-izmeritelnogo-instrumenta-groz-iz-indii-2026/ | 200 |
| jet_online | https://jet-online.ru/robots.txt | 200 |
| jet_online | https://jet-online.ru/special_offers/ | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=10 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=11 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=12 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=13 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=14 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=15 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=16 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=17 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=18 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=19 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=2 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=20 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=21 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=22 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=23 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=24 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=25 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=26 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=3 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=4 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=5 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=6 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=7 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=8 | 200 |
| jet_online | https://jet-online.ru/special_offers/?page=9 | 200 |
| stanki_ru | https://www.stanki.ru/ | 200 |
| stanki_ru | https://www.stanki.ru/about/news/ | 200 |
| stanki_ru | https://www.stanki.ru/about/news/detail/03_09_2026_10187873/ | 200 |
| stanki_ru | https://www.stanki.ru/about/news/detail/11_09_2026_10189109/ | 200 |
| stanki_ru | https://www.stanki.ru/about/news/detail/16_09_2026_10189905/ | 200 |
| stanki_ru | https://www.stanki.ru/about/news/detail/23_09_2026_10191853/ | 200 |
| stanki_ru | https://www.stanki.ru/about/news/detail/25_09_1970_10192183/ | 200 |
| stanki_ru | https://www.stanki.ru/catalog/s_naklonnoy_staninoy/tokarnyy_stanok_s_naklonnoy_staninoy_s_chpu_ironmac_itx_408_hcnc/itx_510_hcnc/ | 200 |
| stanki_ru | https://www.stanki.ru/catalog/s_naklonnoy_staninoy/tokarnyy_tsentr_s_naklonnoy_staninoy_kmt_56_570m/ | 200 |
| stanki_ru | https://www.stanki.ru/catalog/s_naklonnoy_staninoy/tokarnyy_tsentr_s_naklonnoy_staninoy_kmt_56_570m/56_570m/ | 301 |
| stanki_ru | https://www.stanki.ru/close-sale-metall-2026/ | 200 |
| stanki_ru | https://www.stanki.ru/robots.txt | 200 |
| stanki_ru | https://www.stanki.ru/sale/ | 200 |
| stanki_ru | https://www.stanki.ru/sale/derevoobrabatyvayushhee_oborudovanie/ | 200 |
| stanki_ru | https://www.stanki.ru/sale/kamneobrabatyvayushchee_oborudovanie/ | 200 |
| stanki_ru | https://www.stanki.ru/sale/metalloobrabatyvayuschee_oborudovanie/ | 200 |
| stanki_ru | https://www.stanki.ru/sale/oborudovanie_dlya_proizvodstva_mebeli/ | 200 |
| stanki_ru | https://www.stanki.ru/sale/oborudovanie_dlya_zatochki_instrumenta/ | 200 |
| stanki_ru | https://www.stanki.ru/spec/ | 200 |
| stanki_ru | https://www.stanki.ru/spec/khit-prodazh-drev/ | 200 |
| stanki_ru | https://www.stanki.ru/spec/khit-prodazh-kamen/ | 200 |
| stanki_ru | https://www.stanki.ru/spec/khit-prodazh-mebel/ | 200 |
| stanki_ru | https://www.stanki.ru/spec/komplekt-pil-v-podarok-krafter/ | 200 |
| stanki_ru | https://www.stanki.ru/spec/komplekt-pil-v-podarok/ | 200 |
| stanki_ru | https://www.stanki.ru/spec/letnyaya-rasprodazha/ | 200 |
| stanki_ru | https://www.stanki.ru/spec/novinki-filato/ | 200 |
| stanki_ru | https://www.stanki.ru/spec/otgruzka-pri-10/ | 200 |
| stanki_ru | https://www.stanki.ru/spec/rasprodazha-drev-2025-1gruppa/ | 200 |
| stanki_ru | https://www.stanki.ru/spec/skidka-10-optima-atv/ | 200 |
| stanki_ru | https://www.stanki.ru/spec/skidka-15-na-komplekt-nanxing/ | 200 |
| stanki_ru | https://www.stanki.ru/spec/skidka-do-30-kley/ | 404 |
| stanki_ru | https://www.stanki.ru/spec/spetstsena-3200start/ | 200 |
| titaniumgroup | https://titaniumgroup.ru/ | 200 |
| titaniumgroup | https://titaniumgroup.ru/actions/ | 200 |
| titaniumgroup | https://titaniumgroup.ru/articles/ | 200 |
| titaniumgroup | https://titaniumgroup.ru/articles/ehffektivnost-obrabotki-detalej-na-stankakh-s-chpu.html | 200 |
| titaniumgroup | https://titaniumgroup.ru/articles/tokarno-vintoreznyj-stanok-16k20.html | 200 |
| titaniumgroup | https://titaniumgroup.ru/news/ | 200 |
| titaniumgroup | https://titaniumgroup.ru/news/lazernye-stanki-oree-laser.html | 200 |
| titaniumgroup | https://titaniumgroup.ru/news/metalloobrabotka-2026.html | 200 |
| titaniumgroup | https://titaniumgroup.ru/news/novye-stanki-weida-i-dmtg.html | 200 |
| titaniumgroup | https://titaniumgroup.ru/news/rasprodazha-stalex.html | 200 |
| titaniumgroup | https://titaniumgroup.ru/news/stanochnaya-osnastka.html | 200 |
| titaniumgroup | https://titaniumgroup.ru/news/tekhnologicheskie-zhidkosti-katana.html | 200 |
| titaniumgroup | https://titaniumgroup.ru/robots.txt | 200 |
| titaniumgroup | https://titaniumgroup.ru/sitemap.xml | 200 |
| youmet | https://youmet.ru/ | 403 |
| youmet | https://youmet.ru/robots.txt | 403 |
