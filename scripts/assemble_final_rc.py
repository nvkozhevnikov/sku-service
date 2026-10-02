"""Freeze accepted files and emit deterministic neutral XML/CSV, no recomputation."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from universal_supplier.release_export import CONTRACT, summarize, tables, validate, xml_bytes

def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()

def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def assemble(source, output):
    hashes=json.loads((source/"ARTIFACT_SHA256.json").read_text(encoding="utf-8"))
    frozen={}
    names=("MATCHING_ACCEPTED.json","PRODUCT_CARD_PAYLOADS.json","CHECKPOINT.json","FIELD_STATUS_AUDIT.json")
    for name in names:
        actual=digest(source/name)
        if actual != hashes[name]:
            raise RuntimeError("Frozen predecessor SHA mismatch: "+name)
        frozen[name]=actual
    rows=json.loads((source/"MATCHING_ACCEPTED.json").read_text(encoding="utf-8"))["rows"]
    payloads=json.loads((source/"PRODUCT_CARD_PAYLOADS.json").read_text(encoding="utf-8"))
    validate(rows,payloads)
    summary=summarize(rows)
    if (summary["total_source_rows"],summary["Existing"],summary["READY_TO_CREATE_FULL"],summary["Review"],summary["Conflict"]) != (4407,506,2,3640,259):
        raise RuntimeError("Accepted reconciliation changed")
    output.mkdir(parents=True,exist_ok=True)
    accepted=output/"accepted"
    accepted.mkdir(exist_ok=True)
    for name in names:
        target=accepted/name
        if target.exists() and digest(target)!=frozen[name]:
            raise RuntimeError("Existing release freeze differs; refuse overwrite")
        if not target.exists(): shutil.copyfile(source/name,target)
    xml=xml_bytes(rows,payloads,frozen)
    (output/"UNIVERSAL_SUPPLIER_RC_FINAL.xml").write_bytes(xml)
    data=tables(rows,payloads)
    workbook=[]
    for name,items in data.items():
        headers=list(items[0]) if items else []
        filename=name.upper().replace(" ","_")+".csv"
        # Machine CSV is literal UTF-8, not Excel formula-executing import.
        with (output/filename).open("w",encoding="utf-8",newline="") as f:
            writer=csv.DictWriter(f,fieldnames=headers)
            writer.writeheader();writer.writerows(items)
        workbook.append({"name":name,"headers":headers,"rows":[[r.get(k) for k in headers] for r in items]})
    save(output/"WORKBOOK_TABLES.json",workbook)
    save(output/"PRODUCT_CARD_PAYLOADS.json",payloads)
    corrections=ROOT/"reports/RC_LOCAL/TARGETED_NINE_INTEGRATION_2026-10-02"
    for name in ("CANONICAL_DUPLICATE_CORRECTION_PACK.json","CANONICAL_DATA_ERROR_PACK.json"):
        if not (corrections/name).is_file():raise RuntimeError("Required correction pack missing")
        shutil.copyfile(corrections/name,output/name)
    save(output/"FREEZE_MANIFEST.json",{"contract":CONTRACT,"summary":summary,"input_sha256":frozen,
         "new_group_ids":sorted(p["new_group_id"] for p in payloads),
         "csv_counts":{name:len(items) for name,items in data.items()},
         "http":0,"sql_writes":0,"matcher_rerun":False,"import_authorized":False})
    manifest={p.name:{"sha256":digest(p),"bytes":p.stat().st_size}
              for p in sorted(output.iterdir()) if p.is_file() and p.name!="SHA256_MANIFEST.json"}
    save(output/"SHA256_MANIFEST.json",manifest)
    print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args();assemble(args.source,args.output)
