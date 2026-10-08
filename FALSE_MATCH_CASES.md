# Frozen Matching V2.1 regression cases

OPERATOR-CONFIRMED 2026-10-08. Tests: tests/test_matching_policy_v2.py,
tests/test_matching.py, tests/test_effective_identity.py, manual lifecycle suites.

| Non-equivalent identities | Required behavior |
|---|---|
| LX20 Pro / LX20 NEW | no auto |
| PP-13D NEW / PP-13D | no auto |
| ETM-16U controller or handle / ETM-16U machine | no auto |
| HCV125 jaws / HCV125 vise | no auto |
| TU2304V / TU2304 | no auto |
| AHS20/35 / AHS20/50 | no auto |
| KMT45/500S+ /45/500S;50/650S+ /50/650S | no auto, no approved alias |
| X+/X;X+Y/XY;X+Y/X in model identity | distinct token-aware keys |
| 5.5kW/55000W;11kW/1100W;7.5kW/7.5W | same-role material disagreement blocks |

Positive35 operator calibrations are frozen with provenance/input hashes in
tests/fixtures/kami_v21_operator_calibrations.json (bounded observed subset, no raw
HTML). Exact expected IDs and full candidate sets are asserted, not invented.
Same semantic plus execution is positive; prose plus is separately classified.
Absent specs/count differences do not block; duty/motor/capacity roles stay separate.
The complete frozen registry/dump remains external evidence, never this test subset.
