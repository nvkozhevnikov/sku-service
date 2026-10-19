"""Regenerate the synthetic SnapshotControlPlaneStore fixture in this directory.

Every value here is invented test data: no real supplier rows, prices, articles or
Sterbrust IDs. The CSV columns mirror IDENTITY_FIELDS of
scripts/generate_stage6b_reports.py, which produces the real packaged reports/.

    python tests/fixtures/control_plane_snapshot/make_fixture.py
"""
import csv
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)
FIELDS = [
    "supplier_external_id", "SKU", "source_name", "brand", "own_model", "reference_model",
    "model_role", "reference_parent_kind", "product_kind", "category", "candidate_sterbrust_ids",
    "best_candidate", "match_method", "identity_characteristics", "conflicts", "decision",
    "decision_reason", "auto_accepted",
]
REASONS = {
    "EXACT_EXISTING": "exact article match to existing Sterbrust product",
    "HIGH_CONFIDENCE_EXISTING": "brand and model match with compatible identity characteristics",
    "REVIEW_EXISTING": "plausible existing candidate(s); identity proof insufficient",
    "CONFLICT": "identity-critical property conflict with candidate",
    "SAFE_NEW_PRODUCT_CANDIDATE": "no compatible existing candidate; own model identity is sufficient",
    "ACCESSORY_OR_COMPATIBILITY_ITEM": "accessory or compatibility item; not a standalone machine",
    "INSUFFICIENT_IDENTITY": "no brand/model identity evidence",
}

# (decision, kind, category, name template, role)
PLAN = {
    "optimum": [
        ("SAFE_NEW_PRODUCT_CANDIDATE", "lathe", "Станки/Токарные станки", "Токарный станок Optimum ФТ-{n}", "OWN_MODEL"),
        ("SAFE_NEW_PRODUCT_CANDIDATE", "milling_machine", "Станки/Фрезерные станки", "Фрезерный станок Optimum ФФ-{n}", "OWN_MODEL"),
        ("SAFE_NEW_PRODUCT_CANDIDATE", "drill_press", "Станки/Сверлильные станки", "Сверлильный станок Optimum ФС-{n}", "OWN_MODEL"),
        ("ACCESSORY_OR_COMPATIBILITY_ITEM", "chuck", "Оснастка/Токарные патроны", "Патрон токарный Optimum ФП-{n} трёхкулачковый", "OWN_MODEL"),
        ("ACCESSORY_OR_COMPATIBILITY_ITEM", "collet", "Оснастка/Цанги и цанговые патроны", "Цанга Optimum ФЦ-{n} для станка ФФ-200", "REFERENCE_COMPATIBILITY"),
        ("ACCESSORY_OR_COMPATIBILITY_ITEM", "vise", "Оснастка/Тиски", "Тиски станочные Optimum ФВ-{n}", "OWN_MODEL"),
        ("REVIEW_EXISTING", "lathe", "Станки/Токарные станки", "Станок токарный Optimum ФТ-{n} Vario", "OWN_MODEL"),
        ("CONFLICT", "milling_machine", "Станки/Фрезерные станки", "Фрезерный станок Optimum ФФ-{n} 400 В", "OWN_MODEL"),
        ("INSUFFICIENT_IDENTITY", "unknown", "Прочее", "Комплект запасных частей №{n}", "NO_MODEL"),
        ("EXACT_EXISTING", "grinder", "Станки/Заточные станки", "Заточной станок Optimum ФЗ-{n}", "OWN_MODEL"),
        ("HIGH_CONFIDENCE_EXISTING", "drill_press", "Станки/Сверлильные станки", "Станок сверлильный Optimum ФС-{n} Pro", "OWN_MODEL"),
    ],
    "partner_st": [
        ("HIGH_CONFIDENCE_EXISTING", "lathe", "Металлообработка/Токарные станки", "Токарный станок Тестмаш ТМ-{n}", "OWN_MODEL"),
        ("EXACT_EXISTING", "milling_machine", "Металлообработка/Фрезерные станки", "Фрезерный станок Тестмаш ТФ-{n}", "OWN_MODEL"),
        ("REVIEW_EXISTING", "beveling_machine", "Деревообработка/Кромкооблицовочные станки", "Кромкооблицовочный станок Пробник КС-{n}", "OWN_MODEL"),
        ("SAFE_NEW_PRODUCT_CANDIDATE", "grinder", "Металлообработка/Заточные станки", "Заточной станок Пробник ЗС-{n}", "OWN_MODEL"),
        ("ACCESSORY_OR_COMPATIBILITY_ITEM", "table", "Оснастка/Столы", "Стол поворотный Тестмаш СП-{n}", "OWN_MODEL"),
        ("CONFLICT", "lathe", "Металлообработка/Токарные станки", "Токарный станок Тестмаш ТМ-{n} 220 В", "OWN_MODEL"),
        ("INSUFFICIENT_IDENTITY", "unknown", "Разное", "Набор принадлежностей №{n}", "NO_MODEL"),
    ],
}
COUNTS = {"optimum": [5, 4, 3, 6, 4, 3, 4, 3, 2, 1, 1], "partner_st": [4, 4, 4, 3, 4, 3, 2]}
BRANDS = {"optimum": ["Optimum"], "partner_st": ["Тестмаш", "Пробник"]}
FILES = {"optimum": "OPTIMUM_IDENTITY_AUDIT.csv", "partner_st": "PARTNER_ST_IDENTITY_REGRESSION.csv"}

sterbrust_id = 990000
for supplier, plan in PLAN.items():
    rows = []
    ext = 1 if supplier == "partner_st" else 7001
    for (decision, kind, category, template, role), count in zip(plan, COUNTS[supplier]):
        for index in range(count):
            n = 100 + 10 * len(rows) + index
            name = template.format(n=n)
            brand = "Optimum" if supplier == "optimum" else ("Тестмаш" if "Тестмаш" in name else ("Пробник" if "Пробник" in name else ""))
            model = ""
            for token in name.split():
                if "-" in token and any(ch.isdigit() for ch in token):
                    model = token
                    break
            if role == "NO_MODEL":
                model = ""
            candidates = []
            if decision in {"EXACT_EXISTING", "HIGH_CONFIDENCE_EXISTING", "REVIEW_EXISTING", "CONFLICT"}:
                sterbrust_id += 1
                candidates = [str(sterbrust_id)]
                if decision == "REVIEW_EXISTING" and index % 2 == 0:
                    sterbrust_id += 1
                    candidates.append(str(sterbrust_id))
            method = {"EXACT_EXISTING": "EXACT_ARTICLE", "HIGH_CONFIDENCE_EXISTING": "BRAND_MODEL",
                      "REVIEW_EXISTING": "BRAND_MODEL", "CONFLICT": "BRAND_MODEL"}.get(decision, "")
            characteristics = {
                "category": category,
                "identity_critical_properties": {"voltage": "400 V"} if kind in {"lathe", "milling_machine"} else {},
                "execution": {"base_model": model.lower(), "execution_qualifier": ""},
                "candidate_sterbrust_ids": candidates,
                "candidate_evidence": [{"sterbrust_product_id": c, "source": "brand_model"} for c in candidates],
            }
            conflicts = {"voltage": {"source": "400 V", "candidate": "220 V"}} if decision == "CONFLICT" else {}
            rows.append({
                "supplier_external_id": str(ext),
                "SKU": f"FAKE-{supplier[:3].upper()}-{ext}",
                "source_name": name,
                "brand": brand,
                "own_model": model if role == "OWN_MODEL" else "",
                "reference_model": "ФФ-200" if role == "REFERENCE_COMPATIBILITY" else "",
                "model_role": role,
                "reference_parent_kind": "milling_machine" if role == "REFERENCE_COMPATIBILITY" else "",
                "product_kind": kind,
                "category": category,
                "candidate_sterbrust_ids": "|".join(candidates),
                "best_candidate": candidates[0] if candidates else "",
                "match_method": method,
                "identity_characteristics": json.dumps(characteristics, ensure_ascii=False, sort_keys=True),
                "conflicts": json.dumps(conflicts, ensure_ascii=False, sort_keys=True),
                "decision": decision,
                "decision_reason": REASONS[decision],
                "auto_accepted": "TRUE" if decision in {"EXACT_EXISTING", "HIGH_CONFIDENCE_EXISTING"} else "FALSE",
            })
            ext += 1
    with (OUT / FILES[supplier]).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(supplier, len(rows))
