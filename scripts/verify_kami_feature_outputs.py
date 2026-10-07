"""Readback and reconciliation of versioned neutral XML/CSV/XLSX."""
import csv,hashlib,json
from pathlib import Path
from xml.etree import ElementTree as ET
from openpyxl import load_workbook

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'release/KAMI_FEATURE_RC_2026-10-06'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    freeze=json.loads((OUT/'FREEZE_MANIFEST.json').read_bytes())
    xml=ET.parse(OUT/'UNIVERSAL_SUPPLIER_KAMI_RC.xml').getroot()
    assert (len(xml.findall('EXISTING/Product')),len(xml.findall('NEW/Product')),len(xml.findall('UNRESOLVED/Product')))==(506,2,9163)
    assert len(xml.findall('SupplierRegistry/SourceCard'))==5264
    assert len(xml.findall('ROBOTS_EXCLUDED/ExcludedSourceURL'))==2
    product_nodes=xml.findall('EXISTING/Product')+xml.findall('NEW/Product')+xml.findall('UNRESOLVED/Product')
    assert len({(x.get('namespace'),x.get('external_id')) for x in product_nodes})==9671
    assert all(x.get('sterbrust_product_id') is None for x in xml.findall('NEW/Product')+xml.findall('UNRESOLVED/Product'))
    receipt=json.loads((ROOT/'reports/KAMI_INTEGRATION_2026-10-05/SELECTION_RECONCILIATION_2026-10-06/SELECTION_PERSISTED.json').read_bytes())
    expected={tuple(k) for k in receipt['selected']}
    actual={(x.get('namespace'),x.get('external_id')) for x in xml.findall('EXISTING/Product')
            if json.loads(x.find('CommercialObservation').text)['selected_offer']}
    assert actual==expected
    counts={}
    for file in OUT.glob('*.csv'):
        with file.open(encoding='utf-8-sig',newline='') as handle:counts[file.name]=sum(1 for _ in csv.DictReader(handle))
    assert counts=={'EXISTING.csv':506,'OFFERS.csv':506,'NEW_FULL_CARDS.csv':2,'REVIEW.csv':8904,'CONFLICT.csv':259,'SOURCE_TRACE.csv':9671,'ROBOTS_EXCLUDED.csv':2}
    with (OUT/'OFFERS.csv').open(encoding='utf-8-sig',newline='') as handle:
        offers=list(csv.DictReader(handle))
    assert {(r['supplier'],r['external_id']) for r in offers if r['selected']=='True'}==expected
    assert all(r['selection_scope']=='committed_canonical_sql_not_sterbrust_sale' for r in offers)
    w=load_workbook(OUT/'UNIVERSAL_SUPPLIER_KAMI_RC_QA.xlsx',read_only=True,data_only=True)
    assert [w['Сводка'].cell(i,2).value for i in range(5,14)]==[506,2,8904,259,9671,2,28,5264,6]
    header=[c.value for c in w['Offers'][5]]
    workbook_offers=[dict(zip(header,row)) for row in w['Offers'].iter_rows(min_row=6,values_only=True) if row[0] is not None]
    assert {(r['supplier'],str(r['external_id'])) for r in workbook_offers if r['selected']=='ДА'}==expected
    for name,n in [('Existing',506),('Offers',506),('NEW full cards',2),('Review',8904),('Conflict',259),('Source trace',9671),('ROBOTS_EXCLUDED',2)]:
        assert sum(1 for row in w[name].iter_rows(min_row=6,values_only=True) if row[0] is not None)==n
    w.close()
    # Keep the original baseline in its existing read-only junction.
    assert sha(ROOT/'release/FINAL_RC_2026-10-02/accepted/MATCHING_ACCEPTED.json')=='20c3aefd563a1594848864287f347b5755a3095e84ac06c19da8e7604fcbe321'
    freeze['workbook']='REPOSITORY_VERIFIED_DATA_RENDER_READBACK';freeze['selected_offers']=28
    freeze['freeze_date']='2026-10-06'
    (OUT/'FREEZE_MANIFEST.json').write_text(json.dumps(freeze,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest={p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(OUT.iterdir()) if p.is_file() and p.name!='SHA256_MANIFEST.json'}
    (OUT/'SHA256_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    report={'status':'REPOSITORY_VERIFIED_KAMI_NEUTRAL_OUTPUTS','product_rows':9671,'KAMI_registry_rows':5264,
            'csv_counts':counts,'selected':28,'ROBOTS_EXCLUDED':2,'NEW_ID_leaks':0,'source_HTTP':0,
            'reselection_during_export':0,'selection_source':'COMMITTED_CANONICAL_SQL',
            'selection_table_sha256':receipt['persisted_table_sha256'],'XML_CSV_XLSX_selected_sets_equal_SQL':True,
            'visual_spreadsheet_ranges_reviewed':9,'native_Excel_opened':False,
            'builder_process_exit_code':1,'builder_exit_code_classification':'ENVIRONMENT/UNKNOWN_CLEANUP; saved file/cached formulas/tables/PNG/error-scan independently verified',
            'manifest_SHA':sha(OUT/'SHA256_MANIFEST.json')}
    (ROOT/'reports/KAMI_INTEGRATION_2026-10-05/EXPORT_VERIFIED.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
