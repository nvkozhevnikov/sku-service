# RC1_VI — final acceptance proposal

Proposed MATCHING HOLD decision: **ACCEPTED_WITH_CONDITIONS**.
Operator confirmation: **PENDING**. Full RC acceptance: **PENDING**.
The current task authorizes this proposal, not its acceptance or develop transfer.

The runtime remains the verified code at0d02027881817cd7009fe20dceb27f63b0501a09;
this final supplement changes documentation only. Existing IDs/links remain;
automatic reconcile, REVIEW/CONFLICT acceptance and matching apply are prohibited.
461stored-link risk/switch cases and102promotions remain evidence-backed queues,
not accepted semantic corrections. Operator acceptance of this restricted mode
does not remove HOLD, waive LinuxQA, or authorize live writes/import/deployment.

## Exact source matrix

Offline READY below means the existing saved-data route is available: supplier
adapter/queue contracts tested on Windows; competitors use the existing saved
snapshot/shared service; Youmet uses manual capture. It is not a per-site claim
of full discovery, real HTTP, real SQL writes or Linux execution.

| Domain | Namespace | Offline | Live HTTP/SQL | Execution |
|---|---|---|---|---|
| Supplier | partner_st | READY | NOT_VERIFIED | Shared supplier queue |
| Supplier | optimum | READY | NOT_VERIFIED | Shared supplier queue |
| Supplier | beka_mak | READY | NOT_VERIFIED | Shared supplier queue |
| Supplier | beka_mak_tr | READY | NOT_VERIFIED | Shared supplier queue |
| Supplier | intervesp | READY | NOT_VERIFIED | Shared supplier queue |
| Supplier | vekprom | READY | NOT_VERIFIED | Shared supplier queue |
| Supplier | kami | READY | NOT_VERIFIED | Shared supplier queue |
| Supplier | ltt_msk | READY | NOT_VERIFIED | Shared supplier queue |
| Supplier | ltt_sib | READY | NOT_VERIFIED | Shared supplier queue |
| Supplier | ltt_yug | READY | NOT_VERIFIED | Shared supplier queue |
| Supplier | uss_stanko | READY | NOT_VERIFIED | Shared supplier queue |
| Competitor | amur_stan | READY | NOT_VERIFIED | Existing competitor service |
| Competitor | belmash | READY | NOT_VERIFIED | Existing competitor service |
| Competitor | harvey_rus | READY | NOT_VERIFIED | Existing competitor service |
| Competitor | jet_online | READY | NOT_VERIFIED | Existing competitor service |
| Competitor | jettools_ru | READY | NOT_VERIFIED | Existing competitor service |
| Competitor | kuvalda_nnov | READY | NOT_VERIFIED | Existing competitor service |
| Competitor | lasercut_ru | READY | NOT_VERIFIED | Existing competitor service |
| Competitor | metalmaster | READY | NOT_VERIFIED | Existing competitor service |
| Competitor | rustan | READY | NOT_VERIFIED | Existing competitor service |
| Competitor | stanki_ru | READY | NOT_VERIFIED | Existing competitor service |
| Competitor | titaniumgroup | READY | NOT_VERIFIED | Existing competitor service |
| Competitor | youmet | READY | NOT_VERIFIED | MANUAL_CAPTURE; no unattended runner |

All schedules remain disabled. Live NOT_VERIFIED is an explicit acceptance
boundary, not a request to run live collection in this task.

## Remaining gates

- Operator must confirm or reject the MATCHING HOLD proposal.
- Actual Python3.12/LinuxQA is BLOCKED: only stopped docker-desktop is installed;
  Docker Linux Engine is unavailable. No user Docker/service/process was started.
- Full live operation is NOT_VERIFIED and outside this offline acceptance proposal.
  Authorizing HOLD does not convert offline tests into live acceptance.
- STOP before develop; no push, merge, deploy, cron activation or ESOL.

## One concrete Linux verification method

Use a new disposable Ubuntu24.04VM on an independent Linux host/CI executor,
with CPython3.12 and project requirements-dev.txt dependencies preinstalled in
a venv. Do not use the operator's Docker Desktop. Give the VM no NIC during QA,
no business credentials, no PostgreSQL service and no production/shared mounts.
Transfer only a git archive of the final feature HEAD into the VM, extract into
a new disposable directory and verify the supplied archive SHA256.

Inside that VM, as an unprivileged user:

```sh
.venv/bin/python --version
.venv/bin/python scripts/qa_rc_linux_offline.py \
  --basetemp=/tmp/sterbrust-rc-linux-qa \
  --junitxml=/tmp/sterbrust-rc-linux-qa.xml
```

The existing script refuses non-Linux/non3.12 and blocks sockets/PostgreSQL.
Accept the Linux gate only after recording actual platform,Python3.12 version,
exit0,JUnit results and exact code HEAD. Account for each SKIP explicitly;
missing evidence for a mandatory case keeps that case unverified. No dependency
installation or network access is performed during the isolated test run.
This is a prepared procedure, **not an executed Linux PASS**.

## Code and data composition

Runtime code base:0d02027881817cd7009fe20dceb27f63b0501a09.
Final documentation HEAD and archive SHA256 are recorded externally in
workspace reports/RC_FINAL_ACCEPTANCE_20261019/RC_COMPOSITION.json.
Unchanged PostgreSQL17.11dump:
release/DELIVERY_2026-10-19_RC1_VI/database/sterbrust_unified_2026-10-19_RC1_VI.dump
—133823099bytes,SHA256
`a2c9e3fec764714f00648485dee7c72d7b158439a9863803a722b3080de083c2`.
Its recorded engine commit is6d54f6a8c205e75a81cbf359a6ef151834f301f1;
later code-only changes did not change the verified data snapshot.
Existing full empty-DB restore evidence is retained; no dump was recreated.

No new critical runtime change in this supplement. GLM dispatch is not triggered;
only the small documentation diff is provided for operator review.
