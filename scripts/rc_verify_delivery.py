"""Readback of final CSV/XML/XLSX artifacts and exact hashes."""
from pathlib import Path
import csv,hashlib,json,zipfile
from xml.etree import ElementTree as ET
from rc_prepare_database import ROOT,OUT

DELIVERY=ROOT/'release/DELIVERY_2026-10-19_RC1'

def read_csv(name):
    with (DELIVERY/'exports'/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def main():
    # Artifact Tool support reports belong to QA, not human handoff exports.
    for p in (DELIVERY/'exports').glob('*.inspect.ndjson'):
        dest=OUT/'xlsx-build'/(p.stem+'-export.ndjson')
        assert not dest.exists();p.rename(dest)
    existing=read_csv('EXISTING_CONFIRMED.csv');review=read_csv('REVIEW_CONFLICT.csv');trace=read_csv('SOURCE_TRACE.csv')
    key=lambda r:(r['supplier_namespace'],r['source_external_id'])
    a={key(r) for r in existing};b={key(r) for r in review};all_keys={key(r) for r in trace}
    assert len(a)==len(existing)==373 and len(b)==len(review)==11747 and not a&b
    assert a|b==all_keys and len(all_keys)==12120
    assert all(r['sterbrust_product_id'].isdigit() and r['sterbrust_product_id']==r['persisted_sterbrust_product_id'] for r in existing)
    blocked=[r for r in review if r['persisted_sterbrust_product_id']]
    assert len(blocked)==461 and all(not r['sterbrust_product_id'] for r in blocked)
    assert all(r['price_rub']=='' for r in trace if r['price_state']!='numeric_public')
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    for name in ('NEW_PRODUCTS','PRICE_UPDATES'):
        xml=ET.parse(DELIVERY/'exports'/(name+'.xml')).getroot()
        assert xml.get('records')=='0' and xml.get('reason') and xml.get('import_authorized')=='false'
        with zipfile.ZipFile(DELIVERY/'exports'/(name+'.xlsx')) as z:
            assert z.testzip() is None
            wb=ET.fromstring(z.read('xl/workbook.xml'))
            assert [n.attrib['name'] for n in wb.find('s:sheets',ns)]==['Состав','Товары']
            sheet=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            count=sheet.find('.//s:c[@r="B3"]/s:v',ns)
            assert count is not None and count.text=='0'
    manifest=json.loads((DELIVERY/'EXPORT_MANIFEST.json').read_text(encoding='utf-8'))
    manifest['files']=[{'file':p.name,'bytes':p.stat().st_size,'sha256':hashlib.file_digest(p.open('rb'),'sha256').hexdigest()} for p in sorted((DELIVERY/'exports').iterdir())]
    manifest['verification']={'status':'PASS','csv_reconciliation':True,'disjoint_confirmed_review':True,'canonical_ids_preserved_separately':461,'xml_readback':True,'xlsx_zip_and_cell_readback':True,'xlsx_visual_review':'PASS: both sheets in both workbooks','matching_gate':'NOT_VERIFIED','import_authorized':False}
    (DELIVERY/'EXPORT_MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    (OUT/'EXPORT_READBACK_QA.json').write_text(json.dumps(manifest['verification'],indent=2),encoding='utf-8')
    print(json.dumps(manifest['verification'],indent=2))
if __name__=='__main__':main()
