# STERBRUST — единый журнал этапов / старт 19.10.2026 (V2)

> Один трекер, обновляет Codex в новой **integration feature**. Здесь — начальная фиксация из handoff и GitHub. Перенос в `develop` не разрешён. Нельзя путать локальное APPLIED с LIVE server.

| Milestone | DONE | APPLIED | VERIFIED | Block / owner |
|---|---|---|---|---|
| F01/F03 isolated local implementation | YES | YES, только F01/F03 feature | 414 PASS +4 subtests, 2 historical SKIP, diff independently NOT_REVIEWED | GLM review; Python3.12/Linux; historical Beka replays |
| Scoped F01 fetch base | YES (handoff) | 151 objects, one new worktree | reported refs unchanged | Exact local receipt verify upon new agent start |
| Competitor local project discovery | YES (paths confirmed) | none | GitHub competitor feature code, 12 configured sources, schema/UI; local current state NOT_VERIFIED | Codex + Grok |
| Frozen source inventory (all supplier snapshots) | PARTIAL | none | Historical LTT/USS backup, KAMI, Beka, Intervesp references | Codex checks actual bytes and DB roles |
| Integrated feature code across branches | NO | NO | NO | Codex owner |
| One UI + one auth flow | NO | NO | NO | Codex implements; Grok UI contract |
| All 11 supplier source sites are runnable via shared job mechanism | NO | NO | NO | Some adapter-only; no live crawling authorized |
| Unified populated PostgreSQL 17.11 | NO | NO | NO | New isolated DB; source DBs untouched |
| Consolidated verified dump + clean restore | NO | NO | NO | Codex, actual bytes+SHA manifest |
| Human site-manager exports | PARTIAL historical evidence | NO unified exports | NO | Codex, Grok QA |
| CI/replay/critical regression | PARTIAL F01 feature | NO integrated | NO | Codex+GLM |
| Feature publication to GitHub | NO | NO | NO | Separate owner approval |
| Merge to `develop` | NO | NO | NO | EXPLICIT OWNER APPROVAL REQUIRED |
| Server deployment / live cron / ESOL | NO | NO | NO | Independent explicit approvals; ESOL Stage6 BLOCKED |

## Workflow and minimal evidence labels

- `CURRENT_RUNNING_TASK`: create isolated integration feature preserving remote develop and second developer fixes; incorporate local F01/F03 by review; import frozen existing source data into *new* PG; current existing panel only.
- Do not run a new crawl or update observed_at artificially to 19.10. 19.10 = inclusion cutoff, NOT as-of freshness of every row.
- Earlier fixed checkpoint: LTT/USS 2453 source records / 2449 active, source-ingestion VERIFIED 16.10, custom dump+restore historically documented; competitor source DB 55454 last known running 16.10; LTT/USS 55455 reportedly stopped. Not LIVE reverified now.
- Scope of 'one DB': integrated parser platform only; NOT Bitrix `sterbrust.com` or unknown `e.sterbrust` backend DB.

## Каждый ответ агента / обязательный short status

`ЭТАП | DONE | APPLIED (where) | VERIFIED (how) | BLOCKED (why) | NEXT | NEEDS_OWNER_APPROVAL`.

Не более 1–2 экранов промежуточного отчёта. Не писать «всё готово», если один из migration+dump+restore+UI QA gate не пройден.


## Codex current execution
Source table above is EXTERNAL-SPEC historical input. Current authoritative result: ../../INTEGRATION_STATUS.md. Partial RC; full release/matching/live cron/develop transfer NOT_VERIFIED/NOT_APPLIED.
