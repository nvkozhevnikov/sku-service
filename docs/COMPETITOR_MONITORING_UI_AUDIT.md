# Local UI audit / 2026-10-12

REPOSITORY-VERIFIED: the existing local application is FastAPI/Jinja control plane
in universal_supplier/control_plane/web.py, shared base.html/app.css/rc_ui.css.
The current feature checkout includes this main application; no other checkout
or production site is needed. Add a native navigation entry and shared module
subnavigation at /competitor-monitoring, not an iframe/report embed.

Reuse authentication/guard/context, common base template, filters/pagination,
panels/badges and existing competitor normalized_dataset/build_facts/detect_events.
Dedicated read service queries competitor55454 only. Explicit projected columns,
accepted current extractor lineage, bounded history/items, server-side filters
and pagination; no raw capture HTML or historical seed payload in frontend.

Read-only DB audit:98pages/347versions/11428storeditemrows/461observations;
98accepted current-lineage versions,471201normalized characters. Current UI
dataset is small; loader caps pages/versions/items and caches30seconds in memory.
No additional DB/index migration is needed for this MVP. No event table is
invented; existing pure change detection remains the system of record.

Available:6registry sources,current observed cards/promotion dates/NEWS/BLOG,
price roles,provenance,source-page diagnostics,baseline history. Missing:repeat
comparable price snapshots,YouMet operatorcapture,Sterbrust catalogue comparison,
full catalogue coverage,declared automatic refresh cadence. Do not invent these.
YouMet deliberately MANUAL; KAMI404 is a page warning,not global source failure.
No stale-source critical rule applies without an explicitly declared cadence.

Local launcher uses the existing application factory in read-only mode, disables
the module's default DB bootstrap and commercial collection bridge, and never
connects to supplier DBs. Supplier screens show unavailable local data rather
than fixtures; their navigation remains native. Only competitor reads enabled.
No worker/scheduler/startup migration/import/HTTP crawl; no push/merge/PR/deploy.
Target delivery is localhost-only local review in the current feature branch.
